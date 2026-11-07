"""Konfigurasi StockQu.

Konstanta bisnis berasal dari AGENTS.md / stockqu-domain (jangan diubah tanpa konfirmasi).
Kredensial dibaca dari environment (.env) atau Streamlit secrets - tidak pernah di-hardcode.
"""
from __future__ import annotations

import os
from functools import lru_cache

from dotenv import load_dotenv

load_dotenv()

# --- Aturan bisnis (AGENTS.md) ---
BUDGET_MONTHLY = 50_000_000
HISTORY_DAYS = 180  # 6 bulan
SAFETY_DAYS_DEFAULT = 3
RISK_MAX_DAYS = 7  # berisiko habis dalam 4-7 hari
URGENT_MAX_DAYS = 4  # URGENT jika <= 4 hari
MIN_SUPPLIERS = 3
CRON_TIME_WIB = "07:00"
BRANCH_LABELS = ("Branch 1", "Branch 2", "Branch 3")

# --- Label anti-halusinasi ---
LABEL_SIMULASI = "CONTOH SIMULASI - BUKAN DATA REAL"
LABEL_NOT_FOUND = "DATA TIDAK DITEMUKAN"

# --- Google Sheets ---
SHEETS_STOCK_TAB_DEFAULT = "Stok"


def get_setting(name: str, default: str | None = None) -> str | None:
    """Ambil setting dari env, lalu dari Streamlit secrets (kalau berjalan di Streamlit)."""
    value = os.getenv(name)
    if value:
        return value
    try:
        import streamlit as st

        if name in st.secrets:
            return str(st.secrets[name])
    except Exception:  # noqa: BLE001 - secrets tidak tersedia di luar Streamlit
        pass
    return default


@lru_cache(maxsize=1)
def sheets_settings() -> dict:
    return {
        "spreadsheet_id": get_setting("GOOGLE_SHEETS_SPREADSHEET_ID"),
        "stock_tab": get_setting("GOOGLE_SHEETS_STOCK_TAB", SHEETS_STOCK_TAB_DEFAULT),
        # Path file JSON service account ATAU isi JSON langsung (untuk Streamlit Cloud secrets)
        "service_account_file": get_setting("GOOGLE_SERVICE_ACCOUNT_FILE"),
        "service_account_json": get_setting("GOOGLE_SERVICE_ACCOUNT_JSON"),
    }
