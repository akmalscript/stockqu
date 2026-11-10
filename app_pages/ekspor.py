"""Ekspor laporan PDF & Excel (F-35, SRS-F-51): PO, perbandingan supplier, rencana, hasil Analyst."""
from __future__ import annotations

import streamlit as st

from src.config import LABEL_NOT_FOUND
from src.services.auth import ALL_USER_ROLES
from src.services.exports import (
    analyst_xlsx,
    comparison_pdf_bytes,
    comparison_xlsx_bytes,
    po_pdf_bytes,
    procurement_report_xlsx,
)
from src.services.dashboard import sync_forecasts_with_inventory
from src.ui.components import dummy_banner, rupiah
from src.ui.session import page_context
from src.utils.errors import safe_error

actor, user_client, repository = page_context(ALL_USER_ROLES, "mengekspor laporan")
st.title("Ekspor laporan")
st.caption("File dibuat dari data Supabase saat diunduh. Dokumen PO berlabel DRAFT sampai disetujui manager.")
is_dummy = dummy_banner(repository)

try:
    products = {str(p["id"]): p for p in repository.fetch_products()}
    branches = {str(b["id"]): b["name"] for b in repository.fetch_branches()}
    runs = repository.fetch_negotiation_runs(20)
except Exception as exc:  # noqa: BLE001
    st.error(f"{LABEL_NOT_FOUND} - data P3 belum tersedia ({safe_error(exc)}).")
    runs, products, branches = [], {}, {}

# ------------------------------------------------------------------ PO + perbandingan supplier
with st.container(border=True):
    st.subheader("Purchase order & perbandingan supplier")
    if not runs:
        st.caption(f"{LABEL_NOT_FOUND} - belum ada run Negotiator.")
    else:
        run = st.selectbox("Run Negotiator", runs, format_func=lambda r: f"{r['run_id']} · {r['status']} · {rupiah(r['po_total'])}")
        comps = [{**c, "sku": products.get(str(c["product_id"]), {}).get("sku", LABEL_NOT_FOUND),
                  "product_name": products.get(str(c["product_id"]), {}).get("name", LABEL_NOT_FOUND),
                  "branch": branches.get(str(c["branch_id"]), LABEL_NOT_FOUND)}
                 for c in repository.fetch_supplier_comparisons(run["id"])]
        pos = [{**po["draft"], "status": po["status"]} for po in repository.fetch_draft_purchase_orders(run["id"])]
        plan = next((p for p in repository.fetch_procurement_plans(50) if p["id"] == run["plan_id"]), None)
        plan_items = []
        if plan:
            plan_items = [{**i, "sku": products.get(str(i["product_id"]), {}).get("sku"),
                           "branch": branches.get(str(i["branch_id"]))}
                          for i in repository.fetch_procurement_plan_items(plan["id"])]
        with st.container(horizontal=True):
            st.download_button("Perbandingan supplier (PDF)", comparison_pdf_bytes(comps, is_dummy, run["run_id"]),
                               file_name=f"perbandingan-{run['run_id']}.pdf", mime="application/pdf",
                               icon=":material/picture_as_pdf:")
            st.download_button("Perbandingan + draft PO (Excel)", comparison_xlsx_bytes(comps, pos, is_dummy),
                               file_name=f"perbandingan-{run['run_id']}.xlsx", icon=":material/table_view:",
                               mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
            st.download_button("Laporan pengadaan lengkap (Excel)",
                               procurement_report_xlsx(plan=plan, plan_items=plan_items, comparisons=comps, pos=pos,
                                                       is_dummy=is_dummy),
                               file_name=f"laporan-pengadaan-{run['run_id']}.xlsx", icon=":material/summarize:",
                               mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        st.markdown("**PDF per Purchase Order**")
        for po in pos:
            st.download_button(f"{po['po_number']} · {po['supplier_name']} · {po['branch']} · {po['status']}",
                               po_pdf_bytes(po, is_dummy), file_name=f"{po['po_number']}.pdf", mime="application/pdf",
                               key=f"po-{po['po_number']}", icon=":material/description:")

# ------------------------------------------------------------------ Analyst
with st.container(border=True):
    st.subheader("Hasil Agent Analyst")
    try:
        run_id = repository.latest_analyst_run_id()
        rows = repository.fetch_forecasts(run_id) if run_id else []
        if rows:
            try:
                inv = repository.fetch_inventory()
                rows = sync_forecasts_with_inventory(rows, inv, list(products.values()))
            except Exception:  # noqa: BLE001
                pass
    except Exception as exc:  # noqa: BLE001
        rows, run_id = [], None
        st.caption(f"{LABEL_NOT_FOUND} ({safe_error(exc)})")
    if rows:
        rows = [{**f, "sku": products.get(str(f["product_id"]), {}).get("sku"),
                 "branch": branches.get(str(f["branch_id"]))} for f in rows]
        st.download_button("Prediksi & risiko (Excel)", analyst_xlsx(rows, is_dummy), file_name=f"prediksi-{run_id}.xlsx",
                           mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                           icon=":material/table_view:")
    else:
        st.caption(f"{LABEL_NOT_FOUND} - belum ada hasil Analyst.")
