"""Runtime Agent Service: menyambungkan graph ke agent asli, job, approval, dan update stok.

Dipakai oleh FastAPI (n8n) dan CLI. Streamlit tidak menjalankan workflow langsung;
UI memanggil Agent Service lewat HTTP (SRS-NF-16/18/28).
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

from src.config import LABEL_NOT_FOUND
from src.orchestration.procurement_graph import (
    OrchestratorDeps,
    ProcurementOrchestrator,
    apply_decision,
    dispatch_po_emails,
)
from src.services.auth import system_actor
from src.services.n8n_client import N8nClient
from src.utils.errors import safe_error

log = logging.getLogger("stockqu.runtime")

WIB = ZoneInfo("Asia/Jakarta")
FINAL_JOB_STATUS = {
    "waiting_approval": "waiting_approval", "no_action": "no_action", "failed": "failed",
    "approved": "completed", "completed": "completed", "dispatch_incomplete": "completed",
    "rejected": "rejected", "revision_requested": "revision_requested", "expired": "expired",
}


def _now() -> str:
    return datetime.now(WIB).isoformat()


def build_real_deps(repo=None) -> OrchestratorDeps:
    from src.agents.analyst import AnalystAgent
    from src.agents.negotiator import NegotiatorAgent
    from src.agents.purchaser import PurchaserAgent
    from src.services.sheets_client import SheetsClient, sheets_configured
    from src.services.supabase_client import SupabaseRepository, get_client

    client = get_client(write=True)
    repo = repo or SupabaseRepository(read_client=client)
    actor = system_actor("orchestrator")

    def analyst(run_id: str) -> dict:
        factory = SheetsClient if sheets_configured() else None
        return AnalystAgent(SupabaseRepository(read_client=client), sheets_factory=factory, persist=True,
                            actor=actor).run(run_id=run_id)

    def purchaser(analyst_run_id: str, run_id: str) -> dict:
        return PurchaserAgent(SupabaseRepository(read_client=client), persist=True, actor=actor).run(
            analyst_run_id=analyst_run_id, run_id=run_id)

    def negotiator(purchaser_run_id: str, run_id: str) -> dict:
        return NegotiatorAgent(SupabaseRepository(read_client=client), persist=True, actor=actor).run(
            purchaser_run_id=purchaser_run_id, run_id=run_id)

    return OrchestratorDeps(repo=repo, run_analyst=analyst, run_purchaser=purchaser, run_negotiator=negotiator,
                            notifier=N8nClient())


class ProcurementRuntime:
    def __init__(self, deps: OrchestratorDeps, checkpointer, sheets_factory=None):
        self.deps = deps
        self.repo = deps.repo
        self.orchestrator = ProcurementOrchestrator(deps, checkpointer)
        self.sheets_factory = sheets_factory

    # ------------------------------------------------------------------ W1: cron / manual
    def _cancel_stale_pending_approvals(self, new_run_id: str) -> None:
        """Batalkan request approval sebelumnya yang masih pending agar budget tidak terkunci saat manual run baru."""
        try:
            reqs_fn = getattr(self.repo, "fetch_approval_requests", None)
            pending = [r for r in reqs_fn() if r.get("status") == "pending"] if callable(reqs_fn) else []
            for req in pending:
                code = req.get("approval_code")
                if code:
                    apply_decision(self.repo, code, {
                        "decision": "rejected",
                        "via": "dashboard",
                        "decision_ref": f"superseded:{new_run_id}:{code}",
                        "decided_by": "system:manual-run-superseded",
                        "approver_role": "manager",
                        "note": f"Dibatalkan otomatis karena ada workflow manual baru ({new_run_id})",
                    })
        except Exception as exc:  # noqa: BLE001
            log.warning("Gagal membatalkan approval pending lama: %s", safe_error(exc))

    def create_job(self, trigger: str, requested_by: str, day: str | None = None) -> tuple[dict, bool]:
        day = day or datetime.now(WIB).strftime("%Y-%m-%d")
        job_key = f"daily:{day}" if trigger == "cron" else f"manual:{uuid.uuid4().hex[:12]}"
        run_id = f"WF-{day.replace('-', '')}-{uuid.uuid4().hex[:6].upper()}"
        if trigger == "manual":
            self._cancel_stale_pending_approvals(run_id)
        return self.repo.create_workflow_job({
            "job_key": job_key, "trigger": trigger, "run_id": run_id, "status": "running",
            "requested_by": requested_by, "started_at": _now(),
        })

    def run_job(self, job: dict) -> dict:
        run_id = job["run_id"]
        try:
            out = self.orchestrator.start(run_id, job["trigger"])
        except Exception as exc:
            self.repo.update_workflow_job(run_id, {"status": "failed", "error_message": safe_error(exc),
                                                   "finished_at": _now()})
            raise
        status = FINAL_JOB_STATUS.get(out.get("status"), "failed")
        self.repo.update_workflow_job(run_id, {
            "status": status,
            "analyst_run_id": out.get("analyst", {}).get("run_id"),
            "purchaser_run_id": out.get("purchaser", {}).get("run_id"),
            "negotiator_run_id": out.get("negotiator", {}).get("run_id"),
            "approval_code": out.get("approval", {}).get("approval_code"),
            "summary": _summary(out),
            "finished_at": None if status == "waiting_approval" else _now(),
        })
        return out

    # ------------------------------------------------------------------ W3 / dashboard: keputusan
    def submit_decision(self, approval_code: str, decision: dict) -> dict:
        req = self.repo.fetch_approval_request(approval_code)
        if not req:
            return {"status": "not_found", "reply": f"Kode {approval_code}: {LABEL_NOT_FOUND}."}
        run_id = req["workflow_run_id"]
        if req["status"] == "pending" and self.orchestrator.is_waiting(run_id, approval_code):
            out = self.orchestrator.resume(run_id, decision)
            status = out.get("approval", {}).get("status", "failed")
            dispatch = out.get("dispatch", [])
            self.repo.update_workflow_job(run_id, {"status": FINAL_JOB_STATUS.get(out.get("status"), "failed"),
                                                   "summary": _summary(out), "finished_at": _now()})
        else:
            # Checkpoint tidak menunggu (replay webhook / thread hilang): DB tetap jadi penjaga idempotensi.
            res = apply_decision(self.repo, approval_code, decision)
            status = res["status"]
            dispatch = (dispatch_po_emails(self.repo, self.deps.notifier, res["po_ids"])
                        if status == "approved" and not res.get("replay") else [])
        return {"status": status, "approval_code": approval_code, "dispatch": dispatch,
                "reply": _reply_text(approval_code, status, dispatch)}

    # ------------------------------------------------------------------ W4/W5: PO terkirim -> stok in-transit
    def mark_sent(self, po_number: str, email_ref: str, actor: str = "n8n:gmail") -> dict:
        po = self.repo.fetch_purchase_order_by_number(po_number)
        if not po:
            return {"status": "not_found", "po_number": po_number}
        res = self.repo.mark_po_sent(po["id"], email_ref, actor)
        sheets = {"status": "skipped", "detail": "Google Sheets belum dikonfigurasi"}
        if self.sheets_factory is not None:  # upsert idempotent: callback ulang memperbaiki Sheets yang gagal
            sheets = update_incoming_sheet(self.sheets_factory, po)
        return {**res, "sheets": sheets}


def update_incoming_sheet(sheets_factory, po: dict) -> dict:
    from src.services.incoming_sync import upsert_incoming_rows

    draft = po.get("draft") or {}
    rows = [{"po_number": po["po_number"], "sku": i["sku"], "product_name": i["product_name"],
             "branch": draft.get("branch", LABEL_NOT_FOUND), "qty_incoming": i["qty"],
             "supplier": draft.get("supplier_name", LABEL_NOT_FOUND),
             "expected_arrival": po.get("expected_arrival") or draft.get("expected_arrival", ""),
             "status": "in_transit"} for i in draft.get("items", [])]
    try:
        return {"status": "success", **upsert_incoming_rows(sheets_factory(), rows)}
    except Exception as exc:  # noqa: BLE001 - Supabase sudah tercatat; Sheets bisa disinkron ulang
        return {"status": "failed", "detail": safe_error(exc)}


def _summary(out: dict) -> dict:
    return {
        "status": out.get("status"),
        "risk_count": out.get("analyst", {}).get("risk_count"),
        "plan_total": out.get("purchaser", {}).get("proposed_total"),
        "po_total": out.get("negotiator", {}).get("po_total"),
        "po_count": len(out.get("negotiator", {}).get("pos", [])),
        "approval_code": out.get("approval", {}).get("approval_code"),
        "approval_status": out.get("approval", {}).get("status"),
        "notification": out.get("approval", {}).get("notification"),
        "dispatch": out.get("dispatch", []),
        "warnings": out.get("warnings", [])[-10:],
    }


def _reply_text(code: str, status: str, dispatch: list[dict]) -> str:
    if status == "approved":
        sent = sum(d["status"] == "sent" for d in dispatch)
        return f"{code} DISETUJUI. {sent}/{len(dispatch)} PO diproses kirim email; stok in-transit di-update setelah terkirim."
    return {
        "rejected": f"{code} DITOLAK. PO dibatalkan, tidak ada email dan update stok.",
        "revision_requested": f"{code} diminta REVISI. PO kembali ke draft; jalankan ulang Negotiator.",
        "expired": f"{code} sudah KEDALUWARSA. PO kembali ke draft; minta approval baru.",
    }.get(status, f"{code}: status {status}.")


def get_runtime() -> ProcurementRuntime:
    from src.orchestration.checkpoint import get_checkpointer
    from src.services.sheets_client import SheetsClient, sheets_configured

    return ProcurementRuntime(build_real_deps(), get_checkpointer(),
                              sheets_factory=SheetsClient if sheets_configured() else None)
