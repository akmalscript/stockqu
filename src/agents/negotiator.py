r"""Agent 3 - The Negotiator (LangGraph). Roadmap P3-13..15, SRS-F-24..31, F-33, F-53..57.

Graph:
    load_plan -(ada item terpilih?)-> search_suppliers -> retrieve_contracts -> compare_select
        -> budget_recheck -(lewat budget & belum replan)-> replan_cheapest -> budget_recheck
                          -(ok)-> draft_documents -> validate -> persist -> END
                          -(tetap lewat budget)-------------------------> persist (failed)
    load_plan -(tidak ada)---------------------------------------------> persist (no_action)

- Input: procurement plan Purchaser (P2) + forecast Analyst (P1). Qty = allocated_qty plan.
- Penawaran: Supabase contracts + suppliers (minimal 3 supplier per item, ditandai bila kurang).
- RAG: kutipan klausul kontrak PDF (Storage + chunks) + verifikasi angka vs data terstruktur.
- Output: matriks perbandingan, supplier terpilih + alasan, draft PO per (supplier, gudang),
  draft email PO + draft email negosiasi. TIDAK ADA yang dikirim; approval di P4.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime
from typing import TypedDict
from zoneinfo import ZoneInfo

from langgraph.graph import END, START, StateGraph

from src.config import LABEL_NOT_FOUND, MIN_SUPPLIERS
from src.services.auth import (
    NEGOTIATOR_RUN_ROLES,
    ActorContext,
    authorize,
    system_actor,
)
from src.services.budget import load_budget_snapshot
from src.services.work_admission import WorkflowDeadline
from src.tools.contract_rag import (
    latest_documents,
    retrieve,
    verify_offer_against_clauses,
)
from src.tools.supplier_search import build_offers
from src.tools.supplier_selection import (
    build_po_drafts,
    compare_offers,
    draft_negotiation_email,
    draft_po_email,
    select_supplier,
    validate_po_drafts,
)
from src.utils.errors import safe_error
from src.utils.trace import ExecutionTracer, new_run_id

AGENT_NAME = "Negotiator"
WIB = ZoneInfo("Asia/Jakarta")


class NegotiatorState(TypedDict, total=False):
    run_id: str
    as_of: str
    purchaser_run_id: str
    plan: dict
    items: list[dict]
    branches: dict
    offers: dict
    citations: dict
    verifications: dict
    comparisons: list[dict]
    mode: str
    replanned: bool
    budget: dict
    budget_limit: float
    po_total: float
    pos: list[dict]
    validation: dict
    warnings: list[str]
    status: str
    persisted: bool
    negotiation_id: str


class NegotiatorAgent:
    def __init__(self, repo, persist: bool = True, actor: ActorContext | None = None):
        self.repo = repo
        self.persist_enabled = persist
        self.actor = actor or system_actor("negotiator-internal")
        self.tracer: ExecutionTracer | None = None
        self.deadline: WorkflowDeadline | None = None

    # ------------------------------------------------------------------ nodes
    def node_load_plan(self, state: NegotiatorState) -> NegotiatorState:
        self.deadline.check("load_plan")
        tr = self.tracer
        run = state.get("purchaser_run_id") or self.repo.latest_purchaser_run_id()
        plan = self.repo.fetch_procurement_plan(run) if run else None
        if not plan:
            tr.log("baca_procurement_plan", "skipped", input_summary="latest procurement_plans",
                   output_summary=LABEL_NOT_FOUND, error_message="Belum ada plan Purchaser tersimpan")
            return {"status": "no_action", "items": [],
                    "warnings": [*state.get("warnings", []), f"Plan Purchaser: {LABEL_NOT_FOUND}"]}
        with tr.step("baca_procurement_plan", f"plan run {run}") as rec:
            plan_items = self.repo.fetch_procurement_plan_items(plan["id"])
            forecasts = {
                (str(f["product_id"]), str(f["branch_id"])): f
                for f in self.repo.fetch_forecasts(plan["analyst_run_id"])
            }
            products = {str(p["id"]): p for p in self.repo.fetch_products()}
            branches = {str(b["id"]): b["name"] for b in self.repo.fetch_branches()}
            items = []
            for pi in plan_items:
                if int(pi.get("allocated_qty") or 0) <= 0:
                    continue
                key = (str(pi["product_id"]), str(pi["branch_id"]))
                fc = forecasts.get(key, {})
                product = products.get(key[0], {})
                items.append({
                    "product_id": key[0],
                    "branch_id": key[1],
                    "sku": product.get("sku", LABEL_NOT_FOUND),
                    "product_name": product.get("name", LABEL_NOT_FOUND),
                    "branch": branches.get(key[1], LABEL_NOT_FOUND),
                    "rank": pi.get("rank"),
                    "allocated_qty": int(pi["allocated_qty"]),
                    "reference_price": pi.get("unit_price"),
                    "days_to_stockout": fc.get("days_to_stockout"),
                    "risk_level": pi.get("risk_level"),
                })
            items.sort(key=lambda i: i["rank"] or 0)
            rec["output"] = (
                f"plan {plan['id']} status {plan['status']}: {len(items)} SKU-cabang dialokasikan, "
                f"total plan Rp {float(plan.get('proposed_total') or 0):,.0f}"
            )
        status = "loaded" if items else "no_action"
        return {"plan": plan, "items": items, "branches": branches, "purchaser_run_id": run, "status": status}

    def node_search_suppliers(self, state: NegotiatorState) -> NegotiatorState:
        self.deadline.check("search_suppliers")
        with self.tracer.step("cari_supplier", f"{len(state['items'])} SKU; sumber Supabase contracts") as rec:
            offers = build_offers(
                self.repo.fetch_reference_contracts(),
                self.repo.fetch_suppliers(),
                date.fromisoformat(state["as_of"]),
            )
            needed = {i["product_id"] for i in state["items"]}
            counts = [sum(o.status == "valid" for o in offers.get(pid, [])) for pid in needed]
            short = sum(c < MIN_SUPPLIERS for c in counts)
            rec["output"] = (
                f"{sum(len(offers.get(pid, [])) for pid in needed)} penawaran untuk {len(needed)} SKU; "
                f"{short} SKU punya < {MIN_SUPPLIERS} supplier valid"
            )
        return {"offers": {pid: [o.to_dict() for o in offers.get(pid, [])] for pid in needed}}

    def node_retrieve_contracts(self, state: NegotiatorState) -> NegotiatorState:
        self.deadline.check("retrieve_contracts")
        tr = self.tracer
        warnings = list(state.get("warnings", []))
        citations: dict[str, list[dict]] = {}
        verifications: dict[str, dict] = {}
        try:
            with tr.step("rag_kontrak_supplier", "metadata filter + BM25 atas chunk kontrak") as rec:
                documents = self.repo.fetch_supplier_documents()
                chunks = self.repo.fetch_document_chunks([str(d["id"]) for d in documents])
                by_doc = defaultdict(list)
                for c in chunks:
                    by_doc[str(c["document_id"])].append(c)
                skus = {i["product_id"]: i for i in state["items"]}
                for pid, offers in state["offers"].items():
                    item = skus[pid]
                    for o in offers:
                        docs = latest_documents(documents, o["supplier_id"], state["as_of"])
                        sup_chunks = [c for d in docs for c in by_doc.get(str(d["id"]), [])]
                        citations[o["contract_id"]] = [
                            c.to_dict() for c in retrieve(
                                f"{item['sku']} {item['product_name']} harga diskon MOQ pack",
                                o["supplier_id"], documents, chunks, state["as_of"], top_k=1,
                            )
                        ] + [
                            c.to_dict() for c in retrieve(
                                "lead time pengiriman hari", o["supplier_id"], documents, chunks, state["as_of"], top_k=1,
                            )
                        ]
                        verifications[o["contract_id"]] = verify_offer_against_clauses(item["sku"], o, sup_chunks)
                stats = defaultdict(int)
                for v in verifications.values():
                    stats[v["status"]] += 1
                rec["output"] = (
                    f"{len(documents)} dokumen, {len(chunks)} chunk; verifikasi: "
                    + ", ".join(f"{k} {v}" for k, v in sorted(stats.items()))
                )
                if not documents:
                    rec["status"] = "fallback"
                    rec["error"] = "Belum ada dokumen kontrak; semua harga ditandai unverified"
        except Exception as exc:  # noqa: BLE001 - RAG gagal tidak mematikan alur, harga jadi unverified
            warnings.append(f"RAG kontrak gagal ({safe_error(exc)}); harga ditandai belum terverifikasi.")
        return {"citations": citations, "verifications": verifications, "warnings": warnings}

    def _compare(self, state: NegotiatorState, mode: str) -> list[dict]:
        from src.tools.supplier_search import SupplierOffer

        out = []
        for item in state["items"]:
            offers = [SupplierOffer(**o) for o in state["offers"].get(item["product_id"], [])]
            rows = compare_offers(item, offers, state.get("verifications", {}))
            out.append({"item": item, "rows": rows, **select_supplier(item, rows, mode=mode)})
        return out

    def node_compare_select(self, state: NegotiatorState) -> NegotiatorState:
        self.deadline.check("compare_select")
        mode = state.get("mode", "balanced")
        with self.tracer.step("bandingkan_dan_pilih_supplier", f"mode {mode}") as rec:
            comparisons = self._compare(state, mode)
            sel = sum(c["selection_status"] == "selected" for c in comparisons)
            review = sum(c["selection_status"] == "needs_review" for c in comparisons)
            none = sum(c["selection_status"] == "no_offer" for c in comparisons)
            rec["output"] = f"{sel} terpilih, {review} perlu review, {none} tanpa penawaran"
        total = round(sum(c["selected"]["total_cost"] for c in comparisons if c["selected"]), 2)
        return {"comparisons": comparisons, "po_total": total}

    def node_budget_recheck(self, state: NegotiatorState) -> NegotiatorState:
        self.deadline.check("budget_recheck")
        plan = state["plan"]
        with self.tracer.step("budget_recheck", f"periode {plan['period']}") as rec:
            snapshot = load_budget_snapshot(self.repo, plan["period"])
            reservation = self.repo.fetch_plan_reservation(plan["id"]) or {}
            own = float(reservation.get("amount") or 0) if reservation.get("status") == "active" else 0.0
            limit = float(snapshot.available_budget) + own
            total = float(state["po_total"])
            rec["output"] = (
                f"total draft PO Rp {total:,.0f} vs batas Rp {limit:,.0f} "
                f"(tersedia Rp {float(snapshot.available_budget):,.0f} + reservasi plan Rp {own:,.0f})"
            )
            if total > limit:
                rec["status"] = "failed"
        return {"budget": snapshot.to_dict(), "budget_limit": round(limit, 2)}

    def node_replan_cheapest(self, state: NegotiatorState) -> NegotiatorState:
        self.tracer.log(
            "replanning", "retried",
            input_summary=f"total Rp {state['po_total']:,.0f} > batas Rp {state['budget_limit']:,.0f}",
            output_summary="Pilih ulang supplier dengan total biaya terendah (SRS-F-33)",
        )
        return {"mode": "cheapest", "replanned": True}

    def node_draft_documents(self, state: NegotiatorState) -> NegotiatorState:
        self.deadline.check("draft_documents")
        as_of = date.fromisoformat(state["as_of"])
        with self.tracer.step("buat_draft_po_dan_email", "per supplier x gudang tujuan") as rec:
            pos = build_po_drafts(state["comparisons"], state["branches"], as_of, state["run_id"], state["plan"]["id"])
            for po in pos:
                po["emails"] = {
                    "purchase_order": draft_po_email(po),
                    "negotiation": draft_negotiation_email(po, state["comparisons"], state.get("citations", {}), as_of),
                }
            rec["output"] = f"{len(pos)} draft PO, {2 * len(pos)} draft email (belum dikirim)"
        return {"pos": pos}

    def node_validate(self, state: NegotiatorState) -> NegotiatorState:
        self.deadline.check("validate")
        with self.tracer.step("validasi_po", "qty vs plan, MOQ, pack, total, budget") as rec:
            plan_items = [{"product_id": i["product_id"], "branch_id": i["branch_id"],
                           "allocated_qty": i["allocated_qty"]} for i in state["items"]]
            validation = validate_po_drafts(state.get("pos", []), plan_items, state["budget_limit"])
            rec["output"] = f"valid={validation['valid']}; {len(validation['errors'])} error, {len(validation['warnings'])} catatan"
            if not validation["valid"]:
                rec["status"] = "failed"
        if not validation["valid"]:
            status = "failed"
        elif any(c["selection_status"] != "selected" for c in state["comparisons"]):
            status = "needs_review"
        else:
            status = "completed"
        return {"validation": validation, "status": status}

    def node_persist(self, state: NegotiatorState) -> NegotiatorState:
        tr = self.tracer
        warnings = list(state.get("warnings", []))
        status = state.get("status") or "failed"
        if status == "budget_exceeded":
            status = "failed"
            warnings.append("Draft PO tetap melebihi budget setelah replanning; jalankan ulang Purchaser.")
        persisted, negotiation_id = False, None
        # Tanpa draft PO jangan panggil RPC: reservasi budget plan tidak boleh dilepas diam-diam.
        can_store = (self.persist_enabled and state.get("plan") and state.get("pos")
                     and status in ("completed", "needs_review"))
        if self.persist_enabled and state.get("plan") and not state.get("pos") and status != "failed":
            warnings.append(f"Tidak ada draft PO yang bisa dibuat (penawaran {LABEL_NOT_FOUND}); tidak disimpan.")
        if can_store:
            try:
                with tr.step("update_database", "negotiation_runs + supplier_comparisons + purchase_orders draft") as rec:
                    negotiation_id = self.repo.persist_negotiation(*self._payload(state, status))
                    rec["output"] = f"negotiation {negotiation_id}; {len(state.get('pos', []))} draft PO"
                persisted = True
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"Simpan hasil Negotiator gagal ({safe_error(exc)}); lihat log server.")
        tr.log("selesai", "success" if status in ("completed", "needs_review", "no_action") else "failed",
               input_summary=f"run {state['run_id']}",
               output_summary=f"status {status}; total draft PO Rp {float(state.get('po_total') or 0):,.0f}; persisted={persisted}")
        if self.persist_enabled:
            try:
                audit = self.actor.audit_fields()
                self.repo.insert_agent_logs([{**s.to_db_row(), **audit} for s in tr.steps])
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"Simpan agent_logs gagal ({safe_error(exc)})")
        return {"status": status, "persisted": persisted, "negotiation_id": negotiation_id, "warnings": warnings}

    def _payload(self, state: NegotiatorState, status: str) -> tuple[dict, list[dict], list[dict]]:
        run = {
            "run_id": state["run_id"],
            "plan_id": state["plan"]["id"],
            "status": status,
            "po_total": round(sum(po["total_amount"] for po in state.get("pos", [])), 2),
            "validation": state.get("validation", {}),
            "replanned": bool(state.get("replanned")),
            "requested_by": None if self.actor.is_system else self.actor.user_id,
            "requested_by_role": self.actor.role,
        }
        comparisons = []
        for c in state["comparisons"]:
            sel = c["selected"]
            comparisons.append({
                "product_id": c["item"]["product_id"],
                "branch_id": c["item"]["branch_id"],
                "order_qty": sel["order_qty"] if sel else 0,
                "offers": c["rows"],
                "valid_supplier_count": c["valid_supplier_count"],
                "selected_supplier_id": sel["supplier_id"] if sel else None,
                "selected_contract_id": sel["contract_id"] if sel else None,
                "selection_status": c["selection_status"],
                "citations": state.get("citations", {}).get(sel["contract_id"], []) if sel else [],
                "reason_trace": build_reason_trace(c, state),
            })
        pos = [{
            "supplier_id": po["supplier_id"],
            "branch_id": po["branch_id"],
            "po_number": po["po_number"],
            "total_amount": po["total_amount"],
            "expected_arrival": po["expected_arrival"],
            "idempotency_key": po["idempotency_key"],
            "draft": po,
            "items": [{k: i[k] for k in ("product_id", "qty", "unit_price", "discount_pct", "line_total",
                                         "contract_id", "moq", "pack_size")} for i in po["items"]],
        } for po in state.get("pos", [])]
        return run, comparisons, pos

    # ------------------------------------------------------------------ graph
    def build_graph(self):
        g = StateGraph(NegotiatorState)
        for name in ("load_plan", "search_suppliers", "retrieve_contracts", "compare_select",
                     "budget_recheck", "replan_cheapest", "draft_documents", "validate", "persist"):
            g.add_node(name, getattr(self, f"node_{name}"))
        g.add_edge(START, "load_plan")
        g.add_conditional_edges("load_plan", lambda s: "go" if s.get("status") == "loaded" else "stop",
                                {"go": "search_suppliers", "stop": "persist"})
        g.add_edge("search_suppliers", "retrieve_contracts")
        g.add_edge("retrieve_contracts", "compare_select")
        g.add_edge("compare_select", "budget_recheck")
        g.add_conditional_edges("budget_recheck", _route_budget,
                                {"ok": "draft_documents", "replan": "replan_cheapest", "fail": "fail"})
        g.add_node("fail", lambda s: {"status": "budget_exceeded"})
        g.add_edge("fail", "persist")
        g.add_edge("replan_cheapest", "compare_select")
        g.add_edge("draft_documents", "validate")
        g.add_edge("validate", "persist")
        g.add_edge("persist", END)
        return g.compile()

    def run(self, purchaser_run_id: str | None = None, run_id: str | None = None, as_of: date | None = None) -> dict:
        authorize(self.actor, NEGOTIATOR_RUN_ROLES, "menjalankan Agent Negotiator")
        run_id = run_id or new_run_id("NEG")
        self.tracer = ExecutionTracer(AGENT_NAME, run_id)
        self.deadline = WorkflowDeadline()
        initial: NegotiatorState = {
            "run_id": run_id,
            "as_of": (as_of or datetime.now(WIB).date()).isoformat(),
            "mode": "balanced",
            "replanned": False,
            "warnings": [],
        }
        if purchaser_run_id:
            initial["purchaser_run_id"] = purchaser_run_id
        result = self.build_graph().invoke(initial)
        result["execution_trace"] = [s.to_dict() for s in self.tracer.steps]
        return result


def _route_budget(state: NegotiatorState) -> str:
    if float(state["po_total"]) <= float(state["budget_limit"]):
        return "ok"
    return "fail" if state.get("replanned") else "replan"


def build_reason_trace(comparison: dict, state: NegotiatorState) -> dict:
    """Reason Trace Negotiator: ringkasan + evidence bersumber + asumsi (tanpa chain-of-thought)."""
    item, sel = comparison["item"], comparison["selected"]
    evidence = [
        {"label": "Qty alokasi plan", "value": item["allocated_qty"], "unit": "unit",
         "source": f"Supabase procurement_plan_items (plan {state['plan']['id']})"},
        {"label": "Days-to-stockout", "value": None if item["days_to_stockout"] is None
         else round(float(item["days_to_stockout"]), 2), "unit": "hari",
         "source": f"Analyst {state['plan'].get('analyst_run_id')}"},
    ]
    for r in comparison["rows"]:
        evidence.append({
            "label": f"Penawaran {r['supplier_name']}",
            "value": f"{r['effective_price'] or LABEL_NOT_FOUND} Rp/unit; lead {r['lead_time_days']} hari; "
                     f"performa {r['performance_score']}; {r['status']}; dokumen {r['verification'].get('status')}",
            "source": r["source"],
        })
    if sel:
        for c in state.get("citations", {}).get(sel["contract_id"], []):
            evidence.append({"label": f"Kutipan {c['clause_ref']}", "value": c["quote"],
                             "source": f"{c['document_title']} v{c['version']} hal. {c['page']}"})
    return {
        "summary": comparison["reason"],
        "decision": comparison["selection_status"],
        "scores": comparison.get("scores", {}),
        "evidence": evidence,
        "assumptions": [
            "Penawaran berasal dari kontrak tersimpan di Supabase (contracts), belum dari API/scraping supplier.",
            "Skor = 40% total biaya + 25% performa + 25% kecocokan lead time + 10% verifikasi dokumen.",
            "Supplier yang tiba sebelum stok habis didahulukan; mode replanning memilih biaya terendah.",
            "Draft PO dan email belum dikirim; pengiriman hanya setelah approval manager (P4).",
        ],
        "mode": state.get("mode", "balanced"),
    }


def run_negotiator(
    *,
    actor: ActorContext,
    persist: bool = True,
    purchaser_run_id: str | None = None,
    read_client=None,
) -> dict:
    from src.services.auth import AuthenticationRequired
    from src.services.supabase_client import SupabaseRepository, get_client

    authorize(actor, NEGOTIATOR_RUN_ROLES, "menjalankan Agent Negotiator")
    if read_client is None:
        if not actor.is_system:
            raise AuthenticationRequired("Browser run wajib memakai Supabase client user terverifikasi")
        read_client = get_client(write=True)
    return NegotiatorAgent(SupabaseRepository(read_client=read_client), persist=persist, actor=actor).run(
        purchaser_run_id=purchaser_run_id
    )
