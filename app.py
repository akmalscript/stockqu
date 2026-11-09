"""StockQu - entry point Streamlit (roadmap P5-21/22).

Satu gerbang login Supabase Auth, lalu navigasi 11 halaman sesuai role.
Menu yang disembunyikan hanya UX: akses data tetap ditegakkan RLS (query memakai JWT user).

Jalankan:  streamlit run app.py
"""
from __future__ import annotations

import streamlit as st

from src.services.auth import (
    ALL_USER_ROLES,
    PURCHASER_VIEW_ROLES,
    SUPPLIER_DOCUMENT_ADMIN_ROLES,
    require_streamlit_access,
)
from src.ui.session import remember

st.set_page_config(page_title="StockQu", page_icon=":material/inventory_2:", layout="wide")

actor, user_client = require_streamlit_access(ALL_USER_ROLES, "membuka StockQu")
remember(actor, user_client)


def page(path: str, title: str, icon: str, default: bool = False) -> st.Page:
    return st.Page(path, title=title, icon=f":material/{icon}:", default=default)


sections: dict[str, list] = {
    "Operasional": [
        page("app_pages/command_center.py", "Command center", "space_dashboard", default=True),
        page("app_pages/inventori.py", "Inventori", "warehouse"),
    ],
    "Agent": [page("app_pages/prediksi.py", "Prediksi & risiko", "trending_down")],
    "Jejak & laporan": [
        page("app_pages/reason_trace.py", "Reason trace", "psychology"),
        page("app_pages/execution_log.py", "Execution log", "manage_search"),
        page("app_pages/analitik.py", "Analitik", "monitoring"),
        page("app_pages/ekspor.py", "Ekspor", "download"),
    ],
}
if actor.role in PURCHASER_VIEW_ROLES:
    sections["Agent"] += [
        page("app_pages/rencana.py", "Rencana pembelian", "account_balance_wallet"),
        page("app_pages/perbandingan.py", "Perbandingan supplier", "compare_arrows"),
    ]
sections["Agent"].append(page("app_pages/po_approval.py", "PO & approval", "task_alt"))
if actor.role in SUPPLIER_DOCUMENT_ADMIN_ROLES:
    sections["Pengaturan"] = [page("app_pages/admin.py", "Admin", "admin_panel_settings")]

st.navigation(sections, position="sidebar").run()
