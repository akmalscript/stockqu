"""Reason Trace Panel (F-10, F-13, F-32, SRS-F-49, NF-06).

Pilih keputusan dari ketiga agent; setiap faktor menampilkan nilai + sumbernya
(Sheets / Supabase / Analyst / kontrak) dan bisa ditelusuri ke Execution Log run-nya.
"""
from __future__ import annotations

import streamlit as st

from src.config import LABEL_NOT_FOUND
from src.services.auth import ALL_USER_ROLES
from src.ui.components import days, dummy_banner, reason_trace_panel, rupiah
from src.ui.session import page_context
from src.utils.errors import safe_error

actor, user_client, repository = page_context(ALL_USER_ROLES, "melihat reason trace")
st.title("Reason trace")
st.caption("Alasan setiap keputusan agent berbasis evidence. Bukan chain-of-thought LLM; semua angka punya sumber.")

agent = st.segmented_control("Agent", ["Analyst", "Purchaser", "Negotiator"], default="Analyst")
products, branches, suppliers = {}, {}, {}
try:
    products = {str(p["id"]): p for p in repository.fetch_products()}
    branches = {str(b["id"]): b["name"] for b in repository.fetch_branches()}
    suppliers = {str(s["id"]): s["name"] for s in repository.fetch_suppliers()}
except Exception as exc:  # noqa: BLE001
    st.error(f"{LABEL_NOT_FOUND} - master data tidak bisa dibaca ({safe_error(exc)}).")
    st.stop()


def label(row: dict) -> str:
    p = products.get(str(row.get("product_id")), {})
    return f"{p.get('sku', LABEL_NOT_FOUND)} · {branches.get(str(row.get('branch_id')), LABEL_NOT_FOUND)}"


dummy_banner(repository)
try:
    if agent == "Analyst":
        run_id = repository.latest_analyst_run_id()
        rows = repository.fetch_forecasts(run_id) if run_id else []
        only_risk = st.toggle("Hanya SKU berisiko", value=True)
        if only_risk:
            rows = [r for r in rows if r.get("risk_level") in ("URGENT", "RISIKO")]
        rows.sort(key=lambda r: float(r.get("days_to_stockout") or 1e9))
        items = {f"[{r['risk_level']}] {label(r)} - {days(r.get('days_to_stockout'))}": r for r in rows}
    elif agent == "Purchaser":
        plans = repository.fetch_procurement_plans(10)
        plan = st.selectbox("Rencana pembelian", plans, format_func=lambda p: f"{p['run_id']} · {p['status']} · "
                            f"{rupiah(p['proposed_total'])}") if plans else None
        run_id = plan["run_id"] if plan else None
        rows = repository.fetch_procurement_plan_items(plan["id"]) if plan else []
        items = {f"#{r['rank']} [{r['decision']}] {label(r)} - {rupiah(r.get('line_total'))}": r for r in rows}
    else:
        runs = repository.fetch_negotiation_runs(10)
        run = st.selectbox("Run Negotiator", runs, format_func=lambda r: f"{r['run_id']} · {r['status']} · "
                           f"{rupiah(r['po_total'])}") if runs else None
        run_id = run["run_id"] if run else None
        rows = repository.fetch_supplier_comparisons(run["id"]) if run else []
        items = {f"[{r['selection_status']}] {label(r)} -> {suppliers.get(str(r.get('selected_supplier_id')), 'tidak ada')}": r
                 for r in rows}
except Exception as exc:  # noqa: BLE001
    st.error(f"{LABEL_NOT_FOUND} - data {agent} belum tersedia ({safe_error(exc)}). Jalankan agent / migration terkait.")
    st.stop()

if not items:
    st.info(f"{LABEL_NOT_FOUND} - belum ada keputusan {agent} tersimpan.")
    st.stop()

choice = st.selectbox("Keputusan", list(items), key=f"trace-pick-{agent}")
row = items[choice]
with st.container(border=True):
    st.subheader(choice)
    st.caption(f"Run `{run_id}`")
    reason_trace_panel(row.get("reason_trace") or {}, run_id=run_id, key=f"{agent}-{choice}")
    if agent == "Negotiator" and row.get("citations"):
        st.markdown("**Kutipan kontrak:**")
        for c in row["citations"]:
            st.markdown(f"> \"{c['quote']}\"  \n> — {c['document_title']} v{c['version']}, {c['clause_ref']}, "
                        f"hal. {c['page']}, berlaku s/d {c.get('valid_until') or LABEL_NOT_FOUND}")
