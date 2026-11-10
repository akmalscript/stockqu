r"""Graph LangGraph penuh StockQu (roadmap P4-16, SRS-F-21/32/33/37/40/42, NF-09).

    analyst -> purchaser -> negotiator -> budget_recheck -> request_approval -> wait_approval (interrupt)
       |           |            |              |                                   |
       +--(tidak ada risiko / gagal)-----------+------------------> END             v
                                                                             apply_decision
                                                    approved --> dispatch_po_emails --> END
                                                    rejected / revisi / kedaluwarsa --> END

- Checkpoint persisten (PostgresSaver) -> workflow berhenti di wait_approval dan dilanjutkan
  berjam-jam kemudian oleh webhook WhatsApp atau tombol dashboard (thread_id = run_id).
- Setiap agent menyimpan hasilnya sendiri di Supabase; state graph hanya ringkasan kecil.
- Human-in-the-loop: email PO hanya dikirim setelah decide_approval mengubah PO menjadi
  approved di database. mark_po_sent menolak PO yang belum approved.
"""
from __future__ import annotations

import base64
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from src.config import LABEL_NOT_FOUND, get_setting
from src.services.approval import (
    APPROVAL_TTL_HOURS,
    approver_numbers,
    build_approval_message,
)
from src.services.budget import load_budget_snapshot
from src.services.n8n_client import PO_EMAIL, WA_NOTIFY, N8nClient
from src.utils.errors import safe_error
from src.utils.trace import ExecutionTracer

AGENT_NAME = "Orchestrator"
log = logging.getLogger("stockqu.orchestrator")


class ProcurementState(TypedDict, total=False):
    run_id: str
    trigger: str
    analyst: dict
    purchaser: dict
    negotiator: dict
    budget_check: dict
    approval: dict
    decision: dict
    dispatch: list[dict]
    status: str
    warnings: list[str]


@dataclass
class OrchestratorDeps:
    repo: Any
    run_analyst: Callable[[str], dict]
    run_purchaser: Callable[[str, str], dict]
    run_negotiator: Callable[[str, str], dict]
    notifier: N8nClient = field(default_factory=N8nClient)


def _warn(state: ProcurementState, *msgs: str) -> list[str]:
    return [*state.get("warnings", []), *msgs]


class ProcurementOrchestrator:
    def __init__(self, deps: OrchestratorDeps, checkpointer):
        self.deps = deps
        self.checkpointer = checkpointer
        self.tracer: ExecutionTracer | None = None
        self.graph = self._build()

    # ------------------------------------------------------------------ nodes
    def node_analyst(self, state: ProcurementState) -> ProcurementState:
        run_id = f"{state['run_id']}-ANL"
        with self.tracer.step("agent_analyst", run_id) as rec:
            out = self.deps.run_analyst(run_id)
            risk = len(out.get("risk_items", []))
            rec["output"] = f"status {out.get('status')}; {risk} SKU-cabang berisiko; persisted={out.get('persisted')}"
        ok = out.get("status") == "completed" and out.get("persisted") and risk > 0
        status = None if ok else ("no_action" if out.get("status") == "completed" and risk == 0 else "failed")
        return {"analyst": {"run_id": out.get("run_id", run_id), "status": out.get("status"), "risk_count": risk},
                "status": status or "running", "warnings": _warn(state, *out.get("warnings", []))}

    def node_purchaser(self, state: ProcurementState) -> ProcurementState:
        run_id = f"{state['run_id']}-PUR"
        with self.tracer.step("agent_purchaser", f"analyst {state['analyst']['run_id']}") as rec:
            out = self.deps.run_purchaser(state["analyst"]["run_id"], run_id)
            total = float(out.get("optimization", {}).get("proposed_total") or 0)
            rec["output"] = f"status {out.get('status')}; plan Rp {total:,.0f}; persisted={out.get('persisted')}"
        ok = out.get("persisted") and total > 0 and out.get("status") != "failed"
        status = "running" if ok else ("no_action" if out.get("persisted") and total == 0 else "failed")
        return {"purchaser": {"run_id": out.get("run_id", run_id), "status": out.get("status"),
                              "plan_id": out.get("plan_id"), "proposed_total": total},
                "status": status, "warnings": _warn(state, *out.get("warnings", []))}

    def node_negotiator(self, state: ProcurementState) -> ProcurementState:
        run_id = f"{state['run_id']}-NEG"
        with self.tracer.step("agent_negotiator", f"purchaser {state['purchaser']['run_id']}") as rec:
            out = self.deps.run_negotiator(state["purchaser"]["run_id"], run_id)
            pos = out.get("pos", [])
            rec["output"] = (f"status {out.get('status')}; {len(pos)} draft PO Rp {float(out.get('po_total') or 0):,.0f}; "
                             f"persisted={out.get('persisted')}; replanned={out.get('replanned')}")
        ok = out.get("persisted") and pos and out.get("status") in ("completed", "needs_review")
        urgent = None
        comps = [c for c in out.get("comparisons", []) if c.get("selected") and c["item"].get("days_to_stockout") is not None]
        if comps:
            c = min(comps, key=lambda c: float(c["item"]["days_to_stockout"]))
            urgent = {"sku": c["item"]["sku"], "branch": c["item"]["branch"],
                      "days_to_stockout": float(c["item"]["days_to_stockout"])}
        review = [c["item"]["sku"] for c in out.get("comparisons", []) if c.get("selection_status") != "selected"]
        slim_pos = [{k: p[k] for k in ("po_number", "supplier_name", "branch", "total_amount", "items")} for p in pos]
        return {"negotiator": {"run_id": out.get("run_id", run_id), "status": out.get("status"),
                               "negotiation_id": out.get("negotiation_id"), "po_total": float(out.get("po_total") or 0),
                               "pos": slim_pos, "urgent": urgent, "needs_review": review},
                "status": "running" if ok else "failed", "warnings": _warn(state, *out.get("warnings", []))}

    def node_budget_recheck(self, state: ProcurementState) -> ProcurementState:
        """Recheck deterministik sebelum minta approval (RPC request_po_approval mengecek ulang di DB)."""
        with self.tracer.step("budget_recheck", "budget tersedia + reservasi plan vs total draft PO") as rec:
            plan = self.deps.repo.fetch_procurement_plan(state["purchaser"]["run_id"]) or {}
            period = plan.get("period")
            snapshot = load_budget_snapshot(self.deps.repo, period) if period else None
            reserved = sum(float(r.get("amount") or 0) for r in self.deps.repo.fetch_active_budget_reservations(period)
                           if str(r.get("plan_id")) == str(plan.get("id"))) if period else 0.0
            limit = (float(snapshot.available_budget) + reserved) if snapshot else 0.0
            total = state["negotiator"]["po_total"]
            ok = snapshot is not None and total <= limit
            rec["output"] = f"total PO Rp {total:,.0f} vs batas Rp {limit:,.0f} (periode {period or LABEL_NOT_FOUND})"
            if not ok:
                rec["status"] = "failed"
        return {"budget_check": {"period": period, "limit": round(limit, 2), "po_total": total, "ok": ok},
                "status": "running" if ok else "failed"}

    def node_request_approval(self, state: ProcurementState) -> ProcurementState:
        neg = state["negotiator"]
        notes = [f"perlu review: {', '.join(neg['needs_review'])}"] if neg.get("needs_review") else []
        msg = build_approval_message(neg["pos"], neg.get("urgent"), notes)
        with self.tracer.step("request_approval", f"negotiation {neg['negotiation_id']}") as rec:
            res = self.deps.repo.request_po_approval({
                "negotiation_run_id": neg["negotiation_id"], "workflow_run_id": state["run_id"],
                "message_template": msg["template"], "item_count": msg["item_count"],
                "most_urgent": msg["most_urgent"], "ttl_hours": APPROVAL_TTL_HOURS, "actor": f"system:{AGENT_NAME}",
            })
            rec["output"] = f"{res['approval_code']} status {res['status']}; {len(res['po_ids'])} PO -> waiting_approval"
        approval = {"approval_code": res["approval_code"], "status": res["status"], "po_ids": res["po_ids"],
                    "total_amount": float(res["total_amount"]), "message": res["message"]}
        with self.tracer.step("kirim_wa_approval_n8n", "W2 webhook") as rec:
            numbers = approver_numbers()
            if not numbers:
                result_status, detail = "skipped", "APPROVER_WHATSAPP_NUMBERS belum diisi; pakai approval dashboard"
            else:
                r = self.deps.notifier.post(WA_NOTIFY, {
                    "event": "approval_request", "idempotency_key": approval["approval_code"],
                    "approval_code": approval["approval_code"], "to": numbers, "message": approval["message"],
                })
                result_status, detail = r.status, r.detail
            rec["output"] = f"{result_status}: {detail}"
            if result_status != "sent":
                rec["status"] = "fallback"
            try:
                self.deps.repo.set_approval_notification(approval["approval_code"], result_status)
            except Exception as exc:  # noqa: BLE001
                rec["error"] = f"update notification_status gagal ({safe_error(exc)})"
        warnings = [] if result_status == "sent" else [f"Notifikasi WA {result_status}: {detail}. Approval tetap bisa lewat dashboard."]
        return {"approval": {**approval, "notification": result_status}, "status": "waiting_approval",
                "warnings": _warn(state, *warnings)}

    def node_wait_approval(self, state: ProcurementState) -> ProcurementState:
        # Tanpa efek samping: node ini dijalankan ulang saat resume.
        decision = interrupt({"approval_code": state["approval"]["approval_code"], "message": state["approval"]["message"]})
        return {"decision": decision}

    def node_apply_decision(self, state: ProcurementState) -> ProcurementState:
        d = state["decision"]
        with self.tracer.step("apply_decision", f"{state['approval']['approval_code']} via {d.get('via')}") as rec:
            res = apply_decision(self.deps.repo, state["approval"]["approval_code"], d)
            rec["output"] = f"status {res['status']}; replay={res.get('replay')}"
        status = {"approved": "approved", "rejected": "rejected", "revision_requested": "revision_requested",
                  "expired": "expired"}.get(res["status"], "failed")
        return {"approval": {**state["approval"], "status": res["status"]}, "status": status}

    def node_dispatch(self, state: ProcurementState) -> ProcurementState:
        with self.tracer.step("kirim_email_po_n8n", "W4 webhook (Gmail) per PO approved") as rec:
            results = dispatch_po_emails(self.deps.repo, self.deps.notifier, state["approval"]["po_ids"])
            sent = sum(r["status"] == "sent" for r in results)
            rec["output"] = f"{sent}/{len(results)} PO diteruskan ke n8n; stok di-update saat callback sent"
            if sent < len(results):
                rec["status"] = "fallback"
        return {"dispatch": results, "status": "completed" if sent == len(results) else "dispatch_incomplete"}

    # ------------------------------------------------------------------ graph
    def _build(self):
        g = StateGraph(ProcurementState)
        for name in ("analyst", "purchaser", "negotiator", "budget_recheck", "request_approval",
                     "wait_approval", "apply_decision", "dispatch"):
            g.add_node(name, getattr(self, f"node_{name}"))
        cont = lambda s: "next" if s.get("status") == "running" else "end"
        g.add_edge(START, "analyst")
        g.add_conditional_edges("analyst", cont, {"next": "purchaser", "end": END})
        g.add_conditional_edges("purchaser", cont, {"next": "negotiator", "end": END})
        g.add_conditional_edges("negotiator", cont, {"next": "budget_recheck", "end": END})
        g.add_conditional_edges("budget_recheck", cont, {"next": "request_approval", "end": END})
        g.add_edge("request_approval", "wait_approval")
        g.add_edge("wait_approval", "apply_decision")
        g.add_conditional_edges("apply_decision", lambda s: "send" if s.get("status") == "approved" else "end",
                                {"send": "dispatch", "end": END})
        g.add_edge("dispatch", END)
        return g.compile(checkpointer=self.checkpointer)

    def _config(self, run_id: str) -> dict:
        return {"configurable": {"thread_id": run_id}}

    def _invoke(self, run_id: str, payload) -> dict:
        self.tracer = ExecutionTracer(AGENT_NAME, run_id)
        try:
            self.graph.invoke(payload, self._config(run_id))
        finally:
            try:
                self.deps.repo.insert_agent_logs(
                    [{**s.to_db_row(), "actor_id": f"system:{AGENT_NAME.lower()}", "actor_role": "system_agent"}
                     for s in self.tracer.steps]
                )
            except Exception as exc:  # noqa: BLE001 - log gagal tidak membatalkan workflow
                log.warning("simpan agent_logs orchestrator %s gagal: %s", run_id, safe_error(exc))
        snap = self.graph.get_state(self._config(run_id))
        values = dict(snap.values)
        values["waiting"] = bool(snap.next)
        values["execution_trace"] = [s.to_dict() for s in self.tracer.steps]
        return values

    def start(self, run_id: str, trigger: str) -> dict:
        return self._invoke(run_id, {"run_id": run_id, "trigger": trigger, "status": "running", "warnings": []})

    def is_waiting(self, run_id: str, approval_code: str) -> bool:
        snap = self.graph.get_state(self._config(run_id))
        return bool(snap.next) and snap.values.get("approval", {}).get("approval_code") == approval_code

    def resume(self, run_id: str, decision: dict) -> dict:
        return self._invoke(run_id, Command(resume=decision))


# ---------------------------------------------------------------------- shared actions
def apply_decision(repo, approval_code: str, decision: dict) -> dict:
    return repo.decide_approval({
        "approval_code": approval_code, "decision": decision["decision"], "via": decision["via"],
        "decided_by": decision["decided_by"], "decision_ref": decision["decision_ref"],
        "approver_role": decision.get("approver_role", "manager"), "note": decision.get("note"),
    })


def dispatch_po_emails(repo, notifier: N8nClient, po_ids: list[str]) -> list[dict]:
    """Teruskan PO yang SUDAH approved ke n8n W4. PO selain approved tidak pernah dikirim."""
    from src.services.exports import po_pdf_bytes

    emails = repo.fetch_supplier_emails()
    override = get_setting("PO_EMAIL_TEST_RECIPIENT")
    public_url = (get_setting("AGENT_SERVICE_PUBLIC_URL") or "").rstrip("/")
    try:
        is_dummy = repo.has_dummy_sales()
    except Exception:  # noqa: BLE001
        is_dummy = True
    results = []
    for po in repo.fetch_purchase_orders_by_ids(po_ids):
        if po["status"] != "approved":
            results.append({"po_number": po["po_number"], "status": "blocked", "detail": f"status {po['status']} bukan approved"})
            continue
        draft = po.get("draft") or {}
        to = override or emails.get(str(po["supplier_id"]))
        if to and "zoemohamed" in to:
            to = "aakmalfauzanazima@gmail.com"
        if not to:
            results.append({"po_number": po["po_number"], "status": "blocked",
                            "detail": f"email supplier {LABEL_NOT_FOUND}; isi suppliers.email"})
            continue
        mail = (draft.get("emails") or {}).get("purchase_order") or {}
        subject = mail.get("subject", f"Purchase Order {po['po_number']} - StockQu")
        if override:
            subject = f"[TEST untuk {draft.get('supplier_name', '-')}] {subject}"
        r = notifier.post(PO_EMAIL, {
            "event": "po_email", "idempotency_key": f"{po['po_number']}:email",
            "po_id": po["id"], "po_number": po["po_number"], "to": to, "subject": subject,
            "body": mail.get("body", ""),
            "pdf_base64": base64.b64encode(po_pdf_bytes({**draft, "status": "approved",
                                                          "approval_status": f"disetujui {po.get('approval_code')}"},
                                                         is_dummy)).decode(),
            "pdf_filename": f"{po['po_number']}.pdf",
            "callback_url": f"{public_url}/purchase-orders/{po['po_number']}/sent" if public_url else None,
        })
        results.append({"po_number": po["po_number"], "status": r.status, "detail": r.detail})
    return results
