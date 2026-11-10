"""StockQu - entry point Streamlit (roadmap P5-21/22).

Satu gerbang login Supabase Auth, lalu navigasi 11 halaman sesuai role.
Menu yang disembunyikan hanya UX: akses data tetap ditegakkan RLS (query memakai JWT user).

Jalankan:  streamlit run app.py
"""
from __future__ import annotations

from pathlib import Path

import streamlit as st

from src.services.auth import (
    ALL_USER_ROLES,
    PURCHASER_VIEW_ROLES,
    SUPPLIER_DOCUMENT_ADMIN_ROLES,
    require_streamlit_access,
)
from src.services.deployment import (
    DeploymentConfigurationError,
    assert_streamlit_production_ready,
)
from src.ui.session import remember

LOGO_PATH = str(Path(__file__).resolve().parent / "assets" / "StockQu Logo.png")

is_logged_in = bool(
    st.session_state.get("stockqu_auth_tokens")
    or st.query_params.get("_auth")
)
st.set_page_config(
    page_title="StockQu",
    page_icon=LOGO_PATH,
    layout="wide",
    initial_sidebar_state="expanded" if is_logged_in else "collapsed",
)
st.logo(LOGO_PATH, size="large")

# ── Loading overlay: spinner di tengah halaman saat script running ──
# Pakai CSS selector [data-test-script-state="running"] agar overlay
# otomatis muncul/hilang sesuai status rerun Streamlit (termasuk navigasi).
st.markdown("""
<style>
/* Sembunyikan status widget bawaan Streamlit (ikon running kanan atas) */
[data-testid="stStatusWidget"] {
    display: none !important;
}

/* Loading overlay: tersembunyi secara default */
#stockqu-loading-overlay {
    position: fixed;
    inset: 0;
    z-index: 999999;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    background: rgba(255, 255, 255, 0.88);
    opacity: 0;
    visibility: hidden;
    pointer-events: none;
    transition: opacity 0.3s ease, visibility 0.3s ease;
}

/* Tampilkan overlay saat Streamlit sedang running */
[data-test-script-state="running"] #stockqu-loading-overlay {
    opacity: 1;
    visibility: visible;
    pointer-events: auto;
}

.stockqu-spinner {
    width: 44px;
    height: 44px;
    border: 4px solid #EFF4F1;
    border-top: 4px solid #0E7C61;
    border-radius: 50%;
    animation: stockqu-spin 0.8s linear infinite;
}
@keyframes stockqu-spin {
    to { transform: rotate(360deg); }
}
.stockqu-loading-text {
    margin-top: 14px;
    font-family: "Source Sans Pro", "Segoe UI", sans-serif;
    font-size: 0.88rem;
    font-weight: 600;
    color: #1E2A44;
    letter-spacing: 0.02em;
}
</style>
<div id="stockqu-loading-overlay">
    <div class="stockqu-spinner"></div>
    <div class="stockqu-loading-text">Memuat StockQu…</div>
</div>
""", unsafe_allow_html=True)

st.markdown(
    """
    <style>
    /* Perbesar ukuran logo di pojok kiri atas */
    [data-testid="stLogo"],
    img[data-testid="stLogo"],
    .stLogo {
        height: 52px !important;
        max-height: 56px !important;
        width: auto !important;
        max-width: 240px !important;
        object-fit: contain !important;
    }

    [data-testid="stLogoLink"] {
        display: inline-flex !important;
        align-items: center !important;
        height: 100% !important;
    }

    [data-testid="stHeader"],
    .stAppHeader {
        height: 4.5rem !important;
    }

    /* Atur jarak agar konten pas di bawah header Streamlit */
    .block-container, [data-testid="stMainBlockContainer"], .main .block-container, div.stMainBlockContainer {
        padding-top: 2.5rem !important;
        padding-bottom: 2rem !important;
    }
    h1:first-child, .main h1:first-child, [data-testid="stHeading"]:first-child {
        padding-top: 0 !important;
        margin-top: 0 !important;
    }

    /* Tombol buka/tutup (collapse/expand) sidebar selalu terlihat tanpa harus hover */
    [data-testid="stSidebarCollapseButton"],
    div[data-testid="stSidebarCollapseButton"],
    button[data-testid="stSidebarCollapseButton"],
    [data-testid="stExpandSidebarButton"],
    div[data-testid="stExpandSidebarButton"],
    button[data-testid="stExpandSidebarButton"],
    [data-testid="collapsedControl"],
    div[data-testid="collapsedControl"],
    button[data-testid="collapsedControl"] {
        visibility: visible !important;
        opacity: 1 !important;
        display: inline-flex !important;
    }

    [data-testid="stSidebarCollapseButton"] button,
    [data-testid="stExpandSidebarButton"] button,
    [data-testid="collapsedControl"] button {
        visibility: visible !important;
        opacity: 1 !important;
    }

    /* Layout sidebar lebih kompak agar tombol logout selalu terlihat */
    [data-testid="stSidebarNav"] {
        padding-top: 0.25rem !important;
        padding-bottom: 0.25rem !important;
    }
    [data-testid="stSidebarNav"] li {
        margin-bottom: 0.1rem !important;
    }
    [data-testid="stSidebarNav"] a {
        padding-top: 0.25rem !important;
        padding-bottom: 0.25rem !important;
    }
    [data-testid="stSidebarUserContent"] {
        padding-top: 0.5rem !important;
        padding-bottom: 0.5rem !important;
    }

    /* Warna teks brand StockQu di judul: Stock (hitam) dan Qu (hijau tua #1e7e5c) */
    h1,
    h1 > span,
    h1 span:not(.stMarkdownColoredText) {
        color: #000000 !important;
    }

    h1 span.stMarkdownColoredText,
    h1 .stMarkdownColoredText,
    h1 span[style*="color"] {
        color: #1e7e5c !important;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

try:
    assert_streamlit_production_ready()
except DeploymentConfigurationError as exc:
    st.error(f"Konfigurasi production belum siap: {exc}")
    st.stop()

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
