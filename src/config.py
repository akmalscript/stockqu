"""Konfigurasi StockQu.

Konstanta bisnis berasal dari AGENTS.md / stockqu-domain (jangan diubah tanpa konfirmasi).
Kredensial dibaca dari environment (.env) atau Streamlit secrets - tidak pernah di-hardcode.
"""
from __future__ import annotations

import os
from collections.abc import Mapping
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

# --- Batas kerja agent (mencegah satu request menguasai worker/provider) ---
MAX_DATA_ROWS = 10_000
MAX_SALES_HISTORY_ROWS = 100_000
MAX_SHEET_STOCK_ROWS = 5_000
MAX_FORECAST_PAIRS = 3_000
MAX_PURCHASER_ITEMS = 1_000
AGENT_RUN_TIMEOUT_SECONDS = 120
AGENT_RATE_LIMIT_SECONDS = 30
MAX_CONCURRENT_AGENT_RUNS = 2
PROVIDER_REQUEST_TIMEOUT_SECONDS = 20

# --- Label anti-halusinasi ---
LABEL_SIMULASI = "CONTOH SIMULASI - BUKAN DATA REAL"
LABEL_NOT_FOUND = "DATA TIDAK DITEMUKAN"

# --- Google Sheets ---
SHEETS_STOCK_TAB_DEFAULT = "Stok"


def extract_spreadsheet_id(value: str | None) -> str:
    """Ekstrak ID spreadsheet dari ID mentah atau URL penuh Google Sheets."""
    if not value:
        return ""
    val = str(value).strip()
    if "/spreadsheets/d/" in val:
        return val.split("/spreadsheets/d/")[1].split("/")[0]
    return val


def get_setting(name: str, default: str | None = None) -> str | None:
    """Ambil setting dari env, lalu dari Streamlit secrets (kalau berjalan di Streamlit)."""
    value = os.getenv(name)
    if value:
        return value
    for alt in (name.lower(), name.upper()):
        alt_val = os.getenv(alt)
        if alt_val:
            return alt_val
    try:
        import json
        import streamlit as st

        for candidate in (name, name.lower(), name.upper()):
            if candidate in st.secrets:
                val = st.secrets[candidate]
                if isinstance(val, (dict, list, Mapping)):
                    return json.dumps(dict(val) if isinstance(val, Mapping) else val)
                return str(val)
        for section in ("google_sheets", "sheets", "google", "gcp", "service_account"):
            if section in st.secrets and isinstance(st.secrets[section], (dict, Mapping)):
                sec = st.secrets[section]
                for sub in (name, name.lower(), name.removeprefix("GOOGLE_SHEETS_").lower(), name.removeprefix("GOOGLE_").lower()):
                    if sub in sec:
                        val = sec[sub]
                        if isinstance(val, (dict, list, Mapping)):
                            return json.dumps(dict(val) if isinstance(val, Mapping) else val)
                        return str(val)
    except Exception:  # noqa: BLE001 - secrets tidak tersedia di luar Streamlit
        return default
    return default


def sheets_settings() -> dict:
    raw_sheet_id = (
        get_setting("GOOGLE_SHEETS_SPREADSHEET_ID")
        or get_setting("SPREADSHEET_ID")
        or get_setting("SPREADSHEET_URL")
        or get_setting("GOOGLE_SHEETS_SPREADSHEET_URL")
        or get_setting("GOOGLE_SHEETS_URL")
        or get_setting("SHEETS_URL")
        or get_setting("SPREADSHEET")
    )
    stock_tab = get_setting("GOOGLE_SHEETS_STOCK_TAB", SHEETS_STOCK_TAB_DEFAULT)
    sa_file = get_setting("GOOGLE_SERVICE_ACCOUNT_FILE") or get_setting("SERVICE_ACCOUNT_FILE")
    sa_json = get_setting("GOOGLE_SERVICE_ACCOUNT_JSON") or get_setting("SERVICE_ACCOUNT_JSON")

    try:
        import json
        import streamlit as st

        if hasattr(st, "secrets"):
            if not raw_sheet_id:
                for key in ("spreadsheet_id", "spreadsheet_url", "spreadsheet", "google_sheets_url", "sheet_url", "sheets_url"):
                    if key in st.secrets and isinstance(st.secrets[key], str) and st.secrets[key].strip():
                        raw_sheet_id = st.secrets[key]
                        break
            if not raw_sheet_id:
                for sec_name in ("google_sheets", "sheets", "google", "gcp"):
                    if sec_name in st.secrets and isinstance(st.secrets[sec_name], (dict, Mapping)):
                        sec = st.secrets[sec_name]
                        for sub in ("spreadsheet_id", "spreadsheet_url", "spreadsheet", "sheet_url", "url", "id"):
                            if sub in sec and isinstance(sec[sub], str) and sec[sub].strip():
                                raw_sheet_id = sec[sub]
                                break
                        if raw_sheet_id:
                            break
            if not raw_sheet_id and "connections" in st.secrets and isinstance(st.secrets["connections"], (dict, Mapping)):
                gs = st.secrets["connections"].get("gsheets")
                if isinstance(gs, (dict, Mapping)):
                    raw_sheet_id = gs.get("spreadsheet") or gs.get("spreadsheet_id") or gs.get("spreadsheet_url")

            if stock_tab == SHEETS_STOCK_TAB_DEFAULT:
                for sec_name in ("google_sheets", "sheets"):
                    if sec_name in st.secrets and isinstance(st.secrets[sec_name], (dict, Mapping)):
                        sec = st.secrets[sec_name]
                        if "stock_tab" in sec and isinstance(sec["stock_tab"], str):
                            stock_tab = sec["stock_tab"]
                            break

            if not sa_file and not sa_json:
                for sec_name in ("gcp_service_account", "google_service_account", "service_account", "gcp", "google_sheets", "sheets"):
                    if sec_name in st.secrets and isinstance(st.secrets[sec_name], (dict, Mapping)):
                        sec = st.secrets[sec_name]
                        if ("client_email" in sec and "private_key" in sec) or sec.get("type") == "service_account":
                            sa_json = json.dumps(dict(sec))
                            break
                if not sa_json and "connections" in st.secrets and isinstance(st.secrets["connections"], (dict, Mapping)):
                    gs = st.secrets["connections"].get("gsheets")
                    if isinstance(gs, (dict, Mapping)):
                        if ("client_email" in gs and "private_key" in gs) or gs.get("type") == "service_account":
                            sa_json = json.dumps(dict(gs))
    except Exception:  # noqa: BLE001
        pass

    clean_sheet_id = extract_spreadsheet_id(raw_sheet_id) if raw_sheet_id else None

    return {
        "spreadsheet_id": clean_sheet_id,
        "stock_tab": stock_tab or SHEETS_STOCK_TAB_DEFAULT,
        "service_account_file": sa_file,
        "service_account_json": sa_json,
    }
