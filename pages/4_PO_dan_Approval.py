"""Halaman PO & Approval (roadmap P4-18..20, SRS-F-40/42/44/64).

- Status workflow harian (cron 07:00 WIB) dan trigger manual (Super Admin).
- Approval request WhatsApp + fallback approve/tolak/revisi lewat dashboard.
- Lifecycle PO: draft -> waiting_approval -> approved -> sent (+ stok in-transit).
Semua aksi dikirim ke Agent Service; database menegakkan state machine dan idempotensi.
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

from src.config import LABEL_NOT_FOUND
from src.services import agent_service_client as agent_service
from src.services.approval import APPROVER_ROLES_DASHBOARD
from src.services.auth import ALL_USER_ROLES, ROLE_SUPER_ADMIN, require_streamlit_access
from src.services.supabase_client import SupabaseRepository
from src.utils.errors import safe_error

st.set_page_config(page_title="StockQu - PO & Approval", page_icon="📦", layout="wide")
actor, user_client = require_streamlit_access(ALL_USER_ROLES, "melihat PO dan approval")
repository = SupabaseRepository(read_client=user_client)
st.title("PO & Approval")
st.caption("Human-in-the-loop: tanpa OK manager (WhatsApp atau dashboard), PO tidak dikirim dan stok tidak di-update.")

STATUS_FLOW = ["draft", "waiting_approval", "approved", "sent"]


def rupiah(value) -> str:
    return LABEL_NOT_FOUND if value is None else f"Rp {float(value):,.0f}".replace(",", ".")


with st.sidebar:
    st.header("Workflow harian")
    st.caption("Cron n8n W1 setiap hari 07:00 WIB.")
    can_trigger = actor.role == ROLE_SUPER_ADMIN and agent_service.configured()
    if not agent_service.configured():
        st.caption("STOCKQU_AGENT_SERVICE_URL belum diisi: trigger manual & approval dashboard nonaktif.")
    if st.button("Jalankan workflow sekarang", type="primary", width="stretch", disabled=not can_trigger):
        try:
            res = agent_service.trigger_manual_run(actor.user_id)
            if res.get("duplicate"):
                st.warning(f"Masih ada workflow berjalan: {res['run_id']} ({res['status']}).")
            else:
                st.success(f"Workflow {res['run_id']} dimulai.")
        except agent_service.AgentServiceUnavailable as exc:
            st.error(str(exc))

try:
    jobs = repository.fetch_workflow_jobs()
    requests_ = repository.fetch_approval_requests()
    pos = repository.fetch_recent_purchase_orders()
    suppliers = {str(s["id"]): s["name"] for s in repository.fetch_suppliers()}
    branches = {str(b["id"]): b["name"] for b in repository.fetch_branches()}
except Exception as exc:  # noqa: BLE001
    if "PGRST205" in str(exc) or "workflow_jobs" in str(exc) or "approval_requests" in str(exc):
        st.error(f"{LABEL_NOT_FOUND} - skema P4 belum tersedia. Jalankan migration "
                 "`supabase/migrations/20261109200000_p4_orchestration_approval.sql`.")
    else:
        st.error(f"{LABEL_NOT_FOUND} - Supabase tidak bisa dibaca ({safe_error(exc)}).")
    st.stop()

pending = [r for r in requests_ if r["status"] == "pending"]
c1, c2, c3, c4 = st.columns(4)
c1.metric("Menunggu approval", len(pending))
c2.metric("PO approved (belum terkirim)", sum(p["status"] == "approved" for p in pos))
c3.metric("PO terkirim", sum(p["status"] == "sent" for p in pos))
c4.metric("Workflow terakhir", jobs[0]["status"] if jobs else LABEL_NOT_FOUND)

# ------------------------------------------------------------------ approval
st.subheader("Approval request")
can_decide = actor.role in APPROVER_ROLES_DASHBOARD and agent_service.configured()
if not requests_:
    st.caption(f"{LABEL_NOT_FOUND} - belum ada permintaan approval.")
for req in requests_[:10]:
    label = f"{req['approval_code']} | {rupiah(req['total_amount'])} | {req['item_count']} item | {req['status']}"
    with st.expander(label, expanded=req["status"] == "pending"):
        st.code(req["message"], language="text")
        st.caption(f"Notifikasi WA: {req['notification_status']} · berlaku s/d {req['expires_at']} · "
                   f"diputuskan: {req.get('decided_via') or '-'} {req.get('decided_at') or ''}")
        if req["status"] != "pending":
            continue
        if not can_decide:
            st.caption("Role ini hanya melihat; approval oleh Warehouse/Procurement Manager atau Super Admin.")
            continue
        note = st.text_input("Catatan (opsional)", key=f"note-{req['approval_code']}", max_chars=500)
        reviewed = st.checkbox("Saya sudah meninjau draft PO dan perbandingan supplier",
                               key=f"rev-{req['approval_code']}")
        b1, b2, b3 = st.columns(3)
        choice = None
        if b1.button("Setujui (OK)", key=f"ok-{req['approval_code']}", type="primary", disabled=not reviewed):
            choice = "approved"
        if b2.button("Tolak", key=f"no-{req['approval_code']}", disabled=not reviewed):
            choice = "rejected"
        if b3.button("Minta revisi", key=f"rv-{req['approval_code']}", disabled=not reviewed):
            choice = "revision_requested"
        if choice:
            tokens = st.session_state.get("stockqu_auth_tokens") or {}
            try:
                res = agent_service.submit_dashboard_decision(req["approval_code"], choice,
                                                             tokens.get("access_token", ""), note)
                st.success(res.get("reply", res.get("status")))
                for d in res.get("dispatch", []):
                    (st.info if d["status"] == "sent" else st.warning)(f"{d['po_number']}: {d['status']} - {d['detail']}")
            except agent_service.AgentServiceUnavailable as exc:
                st.error(str(exc))

# ------------------------------------------------------------------ PO lifecycle
st.subheader("Lifecycle Purchase Order")
if pos:
    st.dataframe(pd.DataFrame([{
        "No PO": p["po_number"], "Supplier": suppliers.get(str(p["supplier_id"]), LABEL_NOT_FOUND),
        "Gudang": branches.get(str(p.get("branch_id")), LABEL_NOT_FOUND), "Total": p["total_amount"],
        "Status": p["status"], "Tahap": (STATUS_FLOW.index(p["status"]) + 1 if p["status"] in STATUS_FLOW else 0),
        "Kode approval": p.get("approval_code") or "-", "Approved": p.get("approved_at") or "-",
        "Terkirim": p.get("sent_at") or "-", "Estimasi tiba": p.get("expected_arrival") or "-",
    } for p in pos]), hide_index=True, width="stretch", column_config={
        "Total": st.column_config.NumberColumn(format="%.0f"),
        "Tahap": st.column_config.ProgressColumn("Tahap", min_value=0, max_value=4, format="%d/4"),
    })
    st.caption("Barang PO terkirim dicatat sebagai incoming (in-transit), bukan stok fisik, sampai diterima gudang.")
else:
    st.caption(f"{LABEL_NOT_FOUND} - belum ada PO dari Negotiator.")

# ------------------------------------------------------------------ jobs
st.subheader("Riwayat workflow")
if jobs:
    st.dataframe(pd.DataFrame([{
        "Run": j["run_id"], "Trigger": j["trigger"], "Status": j["status"], "Mulai": j["started_at"],
        "Selesai": j.get("finished_at") or "-", "Approval": j.get("approval_code") or "-",
        "Ringkasan": ", ".join(f"{k}={v}" for k, v in (j.get("summary") or {}).items()
                               if k in ("risk_count", "po_count", "po_total", "notification")),
        "Error": j.get("error_message") or "",
    } for j in jobs]), hide_index=True, width="stretch")
    run = st.selectbox("Execution Trace orchestrator", [j["run_id"] for j in jobs])
    logs = repository.fetch_agent_logs(run)
    if logs:
        df = pd.DataFrame(logs)
        st.dataframe(df[[c for c in ("created_at", "agent_name", "tool_name", "status", "output_summary", "error_message")
                         if c in df.columns]], hide_index=True, width="stretch")
else:
    st.caption(f"{LABEL_NOT_FOUND} - workflow belum pernah berjalan.")
