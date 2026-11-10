r"""Agent 2 - The Purchaser (LangGraph). Roadmap P2, F-11..F-15.

Graph:
    load_inputs -> prioritize -> optimize -> explain -> validate -> persist -> END

Semua keputusan finansial dihitung oleh Python tool deterministik. Reason Trace
berisi ringkasan evidence, bukan chain-of-thought internal. Unit keputusan selalu
(SKU, cabang); stok antar cabang tidak pernah dijumlahkan.
"""
from __future__ import annotations

import math
import uuid
from collections import defaultdict
from datetime import date, datetime, timezone
from typing import Any, TypedDict
from zoneinfo import ZoneInfo

from langgraph.graph import END, START, StateGraph

from src.config import LABEL_NOT_FOUND, MAX_PURCHASER_ITEMS
from src.services.auth import PURCHASER_RUN_ROLES, ActorContext, authorize, system_actor
from src.services.budget import load_budget_snapshot
from src.services.supabase_client import DataLimitExceeded, to_jsonable
from src.services.work_admission import WorkflowDeadline
from src.tools.optimizer import optimize_budget_allocation, validate_procurement_plan
from src.utils.errors import safe_error
from src.utils.formulas import RISK_URGENT, RISK_WARNING
from src.utils.trace import ExecutionTracer, new_run_id

AGENT_NAME = "Purchaser"
PRIORITY_HORIZON_DAYS = 14.0


class PurchaserState(TypedDict, total=False):
    run_id: str
    period: str
    analyst_run_id: str
    budget: dict
    forecasts: list[dict]
    items: list[dict]
    impact: dict
    optimization: dict
    validation: dict
    warnings: list[str]
    status: str
    persisted: bool
    plan_id: str


def _fraction(value: Any) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number < 0:
        return None
    return number / 100.0 if number > 1 else number


def _valid_on(contract: dict, as_of: date) -> bool:
    valid_until = contract.get("valid_until")
    return not valid_until or date.fromisoformat(str(valid_until)) >= as_of


def select_reference_contracts(contracts: list[dict], as_of: date) -> dict[str, dict]:
    """Pilih satu harga referensi terakhir per produk; bukan perbandingan supplier P3."""
    grouped: dict[str, list[dict]] = defaultdict(list)
    for contract in contracts:
        try:
            if float(contract.get("price") or 0) > 0:
                grouped[str(contract["product_id"])].append(contract)
        except (TypeError, ValueError, KeyError):
            continue

    selected: dict[str, dict] = {}
    for product_id, rows in grouped.items():
        rows.sort(
            key=lambda row: (
                _valid_on(row, as_of),
                str(row.get("created_at") or ""),
                str(row.get("valid_until") or "9999-12-31"),
            ),
            reverse=True,
        )
        row = dict(rows[0])
        is_valid = _valid_on(row, as_of)
        discount = _fraction(row.get("discount")) if is_valid else 0.0
        discount = min(discount or 0.0, 1.0)
        row["discount_fraction"] = discount
        row["effective_price"] = round(float(row["price"]) * (1.0 - discount), 2)
        row["price_status"] = "valid_contract" if is_valid else "historical_contract"
        selected[product_id] = row
    return selected


def score_priority(items: list[dict]) -> list[dict]:
    """Skor §6.2 DESIGN.md; urutan rank tetap urgency, margin, turnover, discount."""
    if not items:
        return []
    max_margin = max((item.get("margin_fraction") or 0 for item in items), default=0) or 1.0
    max_turnover = max((float(item.get("turnover_rate") or 0) for item in items), default=0) or 1.0
    max_discount = max((float(item.get("discount_fraction") or 0) for item in items), default=0) or 1.0
    scored: list[dict] = []
    for item in items:
        days = float(item.get("days_to_stockout") or 0)
        lead = float(item.get("lead_time_days") or 0)
        slack = days - lead
        urgency = max(0.0, min(1.0, 1.0 - slack / PRIORITY_HORIZON_DAYS))
        margin_norm = (item.get("margin_fraction") or 0) / max_margin
        turnover_norm = float(item.get("turnover_rate") or 0) / max_turnover
        discount_norm = float(item.get("discount_fraction") or 0) / max_discount
        score = 0.45 * urgency + 0.25 * margin_norm + 0.20 * turnover_norm + 0.10 * discount_norm
        scored.append({
            **item,
            "slack_days": round(slack, 4),
            "urgency_score": round(urgency, 6),
            "priority_score": round(score, 6),
        })
    scored.sort(
        key=lambda item: (
            float(item.get("days_to_stockout") or math.inf),
            -(item.get("margin_fraction") or 0),
            -float(item.get("turnover_rate") or 0),
            -float(item.get("discount_fraction") or 0),
            str(item.get("sku") or ""),
            str(item.get("branch") or ""),
        )
    )
    return [{**item, "rank": rank} for rank, item in enumerate(scored, start=1)]


def _missing_price_item(forecast: dict, product: dict, branch: str, rank: int) -> dict:
    return {
        "product_id": forecast["product_id"],
        "branch_id": forecast["branch_id"],
        "sku": product.get("sku", LABEL_NOT_FOUND),
        "product_name": product.get("name", LABEL_NOT_FOUND),
        "branch": branch,
        "rank": rank,
        "decision": "deferred",
        "requested_qty": math.ceil(float(forecast.get("restock_qty") or 0)),
        "allocated_qty": 0,
        "unit_price": None,
        "moq": None,
        "pack_size": None,
        "line_total": 0.0,
        "priority_score": None,
        "risk_level": forecast.get("risk_level", LABEL_NOT_FOUND),
        "days_to_stockout": forecast.get("days_to_stockout"),
        "reason_code": "MISSING_REFERENCE_PRICE",
        "reference_contract_id": None,
        "evidence": [
            {
                "label": "Harga referensi",
                "value": LABEL_NOT_FOUND,
                "source": "Supabase contracts",
            }
        ],
    }


class PurchaserAgent:
    def __init__(self, repo, persist: bool = True, actor: ActorContext | None = None):
        self.repo = repo
        self.persist_enabled = persist
        self.actor = actor or system_actor("purchaser-internal")
        self.tracer: ExecutionTracer | None = None
        self.deadline: WorkflowDeadline | None = None

    def node_load_inputs(self, state: PurchaserState) -> PurchaserState:
        self.deadline.check("load_inputs")
        tr = self.tracer
        warnings = list(state.get("warnings", []))
        with tr.step("get_budget_state", f"periode {state['period']}") as rec:
            budget = load_budget_snapshot(self.repo, state["period"])
            rec["output"] = (
                f"total Rp {budget.total_budget:,.0f}; used Rp {budget.used_budget:,.0f}; "
                f"committed PO Rp {budget.committed_budget:,.0f}; "
                f"allocated plan Rp {budget.allocated_budget:,.0f}; "
                f"tersedia Rp {budget.available_budget:,.0f}"
            )
        warnings.extend(budget.warnings)

        analyst_run_id = state.get("analyst_run_id") or self.repo.latest_analyst_run_id()
        if not analyst_run_id:
            tr.log(
                "baca_hasil_analyst",
                "skipped",
                input_summary="latest analyst_forecasts",
                output_summary=LABEL_NOT_FOUND,
                error_message="Belum ada hasil Analyst yang tersimpan",
            )
            return {
                "budget": budget.to_dict(),
                "items": [],
                "warnings": warnings + [f"Hasil Analyst: {LABEL_NOT_FOUND}"],
                "status": "no_action",
            }

        with tr.step("baca_hasil_analyst", f"run {analyst_run_id}") as rec:
            forecasts = self.repo.fetch_forecasts(analyst_run_id)
            risk = [
                row for row in forecasts
                if row.get("risk_level") in {RISK_URGENT, RISK_WARNING}
                and float(row.get("restock_qty") or 0) > 0
            ]
            if len(risk) > MAX_PURCHASER_ITEMS:
                raise DataLimitExceeded(
                    f"Purchaser menerima {len(risk)} item, melewati batas aman {MAX_PURCHASER_ITEMS}"
                )
            rec["output"] = f"{len(forecasts)} forecast dibaca; {len(risk)} SKU-cabang masuk prioritas"
        return {
            "analyst_run_id": analyst_run_id,
            "budget": budget.to_dict(),
            "forecasts": risk,
            "warnings": warnings,
        }

    def node_prioritize(self, state: PurchaserState) -> PurchaserState:
        self.deadline.check("prioritize")
        tr = self.tracer
        forecasts = state.get("forecasts", [])
        if not forecasts:
            return {"items": [], "status": state.get("status", "no_action")}
        with tr.step("score_priority", "urgensi + margin + turnover + diskon kontrak") as rec:
            products = {str(row["id"]): row for row in self.repo.fetch_products()}
            branches = {str(row["id"]): row["name"] for row in self.repo.fetch_branches()}
            as_of = date.fromisoformat(str(forecasts[0]["as_of_date"]))
            references = select_reference_contracts(self.repo.fetch_reference_contracts(), as_of)
            ready: list[dict] = []
            missing: list[tuple[dict, dict, str]] = []
            for forecast in forecasts:
                self.deadline.check("prioritize_item")
                product = products.get(str(forecast["product_id"]), {})
                branch = branches.get(str(forecast["branch_id"]), LABEL_NOT_FOUND)
                reference = references.get(str(forecast["product_id"]))
                if not product or reference is None:
                    missing.append((forecast, product, branch))
                    continue
                margin_fraction = _fraction(product.get("margin"))
                ready.append({
                    "product_id": forecast["product_id"],
                    "branch_id": forecast["branch_id"],
                    "sku": product.get("sku", LABEL_NOT_FOUND),
                    "product_name": product.get("name", LABEL_NOT_FOUND),
                    "branch": branch,
                    "requested_qty": math.ceil(float(forecast.get("restock_qty") or 0)),
                    "allocated_qty": 0,
                    "unit_price": reference["effective_price"],
                    "moq": int(reference.get("moq") or 1),
                    "pack_size": int(reference.get("pack_size") or 1),
                    "margin_fraction": margin_fraction,
                    "turnover_rate": float(product.get("turnover_rate") or 0),
                    "discount_fraction": float(reference.get("discount_fraction") or 0),
                    "days_to_stockout": float(forecast["days_to_stockout"]),
                    "forecast_daily_demand": float(forecast.get("forecast_daily_demand") or 0),
                    "lead_time_days": int(forecast.get("lead_time_days") or product.get("lead_time_days") or 0),
                    "risk_level": forecast["risk_level"],
                    "reference_contract_id": reference["id"],
                    "reference_price_status": reference["price_status"],
                    "reference_valid_until": reference.get("valid_until"),
                })
            ranked = score_priority(ready)
            next_rank = len(ranked) + 1
            missing_items = [
                _missing_price_item(forecast, product, branch, next_rank + offset)
                for offset, (forecast, product, branch) in enumerate(missing)
            ]
            rec["output"] = f"{len(ranked)} item punya harga referensi; {len(missing_items)} item ditunda karena data harga"
        return {"items": [*ranked, *missing_items]}

    def node_optimize(self, state: PurchaserState) -> PurchaserState:
        self.deadline.check("optimize")
        tr = self.tracer
        items = state.get("items", [])
        available = float(state["budget"]["available_budget"])
        eligible = [item for item in items if item.get("unit_price")]
        with tr.step("optimize_budget_allocation", f"{len(eligible)} item; budget Rp {available:,.0f}") as rec:
            result = optimize_budget_allocation(eligible, available)
            rec["output"] = (
                f"method {result.method}; total Rp {result.proposed_total:,.0f}; "
                f"solver {result.solver_status}"
            )
            if result.method == "greedy_fallback":
                rec["status"] = "fallback"
            optimized: list[dict] = []
            for item in items:
                if not item.get("unit_price"):
                    optimized.append(item)
                    continue
                qty = int(result.allocations.get(f"{item['product_id']}|{item['branch_id']}", 0))
                requested = int(item["requested_qty"])
                decision = "deferred" if qty == 0 else ("partial" if qty < requested else "selected")
                minimum_qty = math.ceil(int(item["moq"]) / int(item["pack_size"])) * int(item["pack_size"])
                full_qty = math.ceil(requested / int(item["pack_size"])) * int(item["pack_size"])
                reason_code = (
                    "SELECTED" if decision == "selected"
                    else "PARTIAL_BUDGET" if decision == "partial"
                    else "DEFERRED_MOQ" if minimum_qty > full_qty
                    else "DEFERRED_BUDGET"
                )
                optimized.append({
                    **item,
                    "allocated_qty": qty,
                    "decision": decision,
                    "line_total": round(qty * float(item["unit_price"]), 2),
                    "reason_code": reason_code,
                })
        optimization = {
            "method": result.method,
            "status": result.status,
            "proposed_total": result.proposed_total,
            "minimum_urgent_cost": result.minimum_urgent_cost,
            "budget_shortfall": result.budget_shortfall,
            "solver_status": result.solver_status,
        }
        return {"items": optimized, "optimization": optimization, "status": result.status}

    def node_explain(self, state: PurchaserState) -> PurchaserState:
        self.deadline.check("explain")
        tr = self.tracer
        with tr.step("susun_reason_trace", "evidence Analyst + Supabase + optimizer") as rec:
            items = []
            analyst_run_id = state.get("analyst_run_id", LABEL_NOT_FOUND)
            available = float(state["budget"]["available_budget"])
            for item in state.get("items", []):
                self.deadline.check("explain_item")
                evidence = list(item.get("evidence", []))
                if item.get("unit_price"):
                    evidence.extend([
                        {"label": "Days-to-stockout", "value": round(float(item["days_to_stockout"]), 2), "unit": "hari", "source": f"Analyst {analyst_run_id}"},
                        {"label": "Lead time", "value": item["lead_time_days"], "unit": "hari", "source": "Supabase products / analyst_forecasts"},
                        {"label": "Margin", "value": LABEL_NOT_FOUND if item.get("margin_fraction") is None else round(100 * item["margin_fraction"], 2), "unit": "%", "source": "Supabase products.margin"},
                        {"label": "Turnover", "value": item.get("turnover_rate"), "source": "Supabase products.turnover_rate"},
                        {"label": "Harga referensi", "value": item["unit_price"], "unit": "Rp/unit", "source": f"Supabase contracts.id={item['reference_contract_id']}"},
                        {"label": "Diskon kontrak valid", "value": round(100 * item.get("discount_fraction", 0), 2), "unit": "%", "source": f"Supabase contracts.id={item['reference_contract_id']}"},
                        {"label": "MOQ / pack", "value": f"{item['moq']} / {item['pack_size']}", "unit": "unit", "source": "Supabase contracts"},
                        {"label": "Priority score", "value": item.get("priority_score"), "source": "Python score_priority"},
                        {"label": "Budget tersedia sebelum plan", "value": available, "unit": "Rp", "source": "Supabase budgets + purchase_orders + procurement_budget_reservations"},
                    ])
                decision = item.get("decision", "deferred")
                margin_text = (
                    LABEL_NOT_FOUND
                    if item.get("margin_fraction") is None
                    else f"{100 * item['margin_fraction']:.2f}%"
                )
                if item.get("reason_code") == "MISSING_REFERENCE_PRICE":
                    summary = (
                        f"{item['sku']} di {item['branch']} ditunda: harga referensi {LABEL_NOT_FOUND}; "
                        "Purchaser tidak mengarang harga dan tidak mengalokasikan budget."
                    )
                elif decision == "selected":
                    summary = (
                        f"{item['sku']} di {item['branch']} dipilih rank {item['rank']}: "
                        f"days-to-stockout {item['days_to_stockout']:.2f} hari, margin {margin_text}, "
                        f"turnover {item['turnover_rate']:.2f}, "
                        f"alokasi {item['allocated_qty']} unit senilai Rp {item['line_total']:,.0f}."
                    )
                elif decision == "partial":
                    summary = (
                        f"{item['sku']} di {item['branch']} dikurangi dari {item['requested_qty']} menjadi "
                        f"{item['allocated_qty']} unit agar total plan tidak melebihi budget tersedia."
                    )
                elif item.get("reason_code") == "DEFERRED_MOQ":
                    summary = (
                        f"{item['sku']} di {item['branch']} ditunda: kebutuhan {item['requested_qty']} unit "
                        f"lebih kecil dari MOQ efektif {item['moq']} unit dan tidak boleh melanggar kontrak."
                    )
                else:
                    summary = (
                        f"{item['sku']} di {item['branch']} ditunda: setelah item berprioritas lebih tinggi, "
                        f"budget tidak cukup untuk MOQ {item['moq']} unit."
                    )
                items.append({
                    **item,
                    "evidence": evidence,
                    "reason_trace": {
                        "summary": summary,
                        "decision": decision,
                        "reason_code": item.get("reason_code"),
                        "evidence": evidence,
                        "assumptions": [
                            "Harga Purchaser adalah harga referensi kontrak; kuotasi aktual diperiksa Negotiator dan budget_recheck pada fase berikutnya.",
                            "Skor = 45% urgensi + 25% margin + 20% turnover + 10% diskon valid.",
                            "PO waiting approval dihitung committed; plan aktif dihitung allocated; PO approved/sent masuk used budget tanpa hitung ganda.",
                        ],
                    },
                })
            rec["output"] = f"{len(items)} Reason Trace berbasis evidence dibuat"

        selected = [item for item in items if item.get("allocated_qty", 0) > 0]
        coverage_days = sum(
            float(item["allocated_qty"]) / float(item["forecast_daily_demand"])
            for item in selected
            if float(item.get("forecast_daily_demand") or 0) > 0
        )
        saved_margin = sum(
            min(int(item["allocated_qty"]), int(item["requested_qty"]))
            * float(item["unit_price"])
            * float(item.get("margin_fraction") or 0)
            for item in selected
        )
        impact = {
            "selected_items": len(selected),
            "deferred_items": sum(item.get("allocated_qty", 0) == 0 for item in items),
            "partial_items": sum(item.get("decision") == "partial" for item in items),
            "urgent_items_covered": sum(item.get("risk_level") == RISK_URGENT for item in selected),
            "additional_coverage_days": round(coverage_days, 2),
            "estimated_margin_protected": round(saved_margin, 2),
            "impact_basis": "allocated_qty / forecast_daily_demand; allocated_qty x contract reference price x product margin",
        }
        return {"items": items, "impact": impact}

    def node_validate(self, state: PurchaserState) -> PurchaserState:
        self.deadline.check("validate")
        tr = self.tracer
        available = float(state["budget"]["available_budget"])
        with tr.step("validate_procurement_plan", "budget + MOQ + pack + duplikasi") as rec:
            validation = validate_procurement_plan(state.get("items", []), available)
            validation["optimization"] = state.get("optimization", {})
            rec["output"] = (
                f"valid={validation['valid']}; total Rp {validation['calculated_total']:,.0f}; "
                f"{len(validation['errors'])} error"
            )
            if not validation["valid"]:
                rec["status"] = "failed"
        return {
            "validation": validation,
            "status": state.get("status", "completed") if validation["valid"] else "failed",
        }

    def node_persist(self, state: PurchaserState) -> PurchaserState:
        self.deadline.check("persist")
        tr = self.tracer
        warnings = list(state.get("warnings", []))
        persisted = False
        plan_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"stockqu:{state['run_id']}"))
        status = state.get("status", "completed")
        if self.persist_enabled and state.get("analyst_run_id") and status != "failed":
            plan = {
                "id": plan_id,
                "run_id": state["run_id"],
                "analyst_run_id": state["analyst_run_id"],
                "period": state["period"],
                "budget_total": state["budget"]["total_budget"],
                "budget_used": state["budget"]["used_budget"],
                "budget_committed": state["budget"]["committed_budget"],
                "budget_allocated": state["budget"]["allocated_budget"],
                "budget_available": state["budget"]["available_budget"],
                "proposed_total": state.get("optimization", {}).get("proposed_total", 0),
                "remaining_after_plan": max(
                    float(state["budget"]["available_budget"])
                    - float(state.get("optimization", {}).get("proposed_total", 0)),
                    0,
                ),
                "status": status,
                "optimization_method": state.get("optimization", {}).get("method", "not_run"),
                "impact": state.get("impact", {}),
                "validation": state.get("validation", {}),
                "requested_by": None if self.actor.is_system else self.actor.user_id,
                "requested_by_role": self.actor.role,
                "created_at": datetime.now(timezone.utc).isoformat(),  # noqa: UP017 - Python 3.10 compatible
            }
            item_fields = {
                "product_id", "branch_id", "reference_contract_id", "rank", "decision",
                "requested_qty", "allocated_qty", "unit_price", "moq", "pack_size",
                "line_total", "priority_score", "risk_level", "reason_trace", "evidence",
            }
            payload = [{key: value for key, value in item.items() if key in item_fields} for item in state.get("items", [])]
            try:
                with tr.step("update_database", "procurement_plans + procurement_plan_items") as rec:
                    stored_id = self.repo.persist_procurement_plan(to_jsonable(plan), to_jsonable(payload))
                    rec["output"] = f"plan {stored_id}; {len(payload)} item disimpan transaksional"
                plan_id = stored_id
                persisted = True
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"Simpan procurement plan gagal ({safe_error(exc)}); lihat log server.")

        tr.log(
            "selesai",
            "success" if status != "failed" else "failed",
            input_summary=f"run {state['run_id']}",
            output_summary=(
                f"status {status}; total Rp {state.get('optimization', {}).get('proposed_total', 0):,.0f}; "
                f"persisted={persisted}"
            ),
        )
        if self.persist_enabled:
            try:
                audit = self.actor.audit_fields()
                self.repo.insert_agent_logs([{**step.to_db_row(), **audit} for step in tr.steps])
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"Simpan agent_logs gagal ({safe_error(exc)})")
        return {"persisted": persisted, "plan_id": plan_id, "warnings": warnings}

    def build_graph(self):
        graph = StateGraph(PurchaserState)
        graph.add_node("load_inputs", self.node_load_inputs)
        graph.add_node("prioritize", self.node_prioritize)
        graph.add_node("optimize", self.node_optimize)
        graph.add_node("explain", self.node_explain)
        graph.add_node("validate", self.node_validate)
        graph.add_node("persist", self.node_persist)
        graph.add_edge(START, "load_inputs")
        graph.add_edge("load_inputs", "prioritize")
        graph.add_edge("prioritize", "optimize")
        graph.add_edge("optimize", "explain")
        graph.add_edge("explain", "validate")
        graph.add_edge("validate", "persist")
        graph.add_edge("persist", END)
        # Durability lintas approval dimiliki graph orkestrator. State internal
        # agent sudah dipersist ke tabel domain dan tidak boleh ikut mewarisi
        # checkpointer parent.
        return graph.compile(checkpointer=False)

    def run(
        self,
        period: str | None = None,
        analyst_run_id: str | None = None,
        run_id: str | None = None,
    ) -> dict:
        authorize(self.actor, PURCHASER_RUN_ROLES, "menjalankan Agent Purchaser")
        period = period or datetime.now(ZoneInfo("Asia/Jakarta")).strftime("%Y-%m")
        run_id = run_id or new_run_id("PUR")
        self.tracer = ExecutionTracer(AGENT_NAME, run_id)
        self.deadline = WorkflowDeadline()
        initial: PurchaserState = {"run_id": run_id, "period": period, "warnings": []}
        if analyst_run_id:
            initial["analyst_run_id"] = analyst_run_id
        result = self.build_graph().invoke(initial)
        result["execution_trace"] = [step.to_dict() for step in self.tracer.steps]
        return result


def run_purchaser(
    *,
    actor: ActorContext,
    persist: bool = True,
    period: str | None = None,
    analyst_run_id: str | None = None,
    read_client=None,
) -> dict:
    from src.services.auth import AuthenticationRequired
    from src.services.supabase_client import SupabaseRepository, get_client

    authorize(actor, PURCHASER_RUN_ROLES, "menjalankan Agent Purchaser")
    if read_client is None:
        if not actor.is_system:
            raise AuthenticationRequired("Browser run wajib memakai Supabase client user terverifikasi")
        read_client = get_client(write=True)

    return PurchaserAgent(
        SupabaseRepository(read_client=read_client),
        persist=persist,
        actor=actor,
    ).run(
        period=period,
        analyst_run_id=analyst_run_id,
    )
