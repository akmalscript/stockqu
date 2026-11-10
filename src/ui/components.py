"""Komponen UI bersama StockQu (roadmap P5-21/23). Palet & tipografi dari .streamlit/config.toml."""
from __future__ import annotations

import math
import re
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

from src.config import LABEL_NOT_FOUND, LABEL_SIMULASI

WIB = ZoneInfo("Asia/Jakarta")
TRACE_RUN_KEY = "trace_selected_run"
META_RE = re.compile(r"\[durasi (\d+) ms, percobaan (\d+)\]")


def rupiah(value) -> str:
    try:
        return f"Rp {float(value):,.0f}".replace(",", ".")
    except (TypeError, ValueError):
        return LABEL_NOT_FOUND


def days(value) -> str:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return LABEL_NOT_FOUND
    return "tidak habis" if math.isinf(v) else f"{v:.1f} hari"


def local_time(value) -> str:
    if not value:
        return "-"
    try:
        ts = datetime.fromisoformat(str(value).replace("Z", "+00:00"))  # noqa: FURB162 - Python 3.10 compatible
    except ValueError:
        return str(value)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=WIB)
    return ts.astimezone(WIB).strftime("%d %b %Y %H:%M WIB")


def dummy_banner(repository) -> bool:
    """Label wajib di atas tabel bila data berasal dari seed dummy."""
    try:
        is_dummy = repository.has_dummy_sales()
    except Exception:  # noqa: BLE001
        is_dummy = False
    if is_dummy:
        st.warning(LABEL_SIMULASI, icon=":material/science:")
    return is_dummy


def log_meta(output_summary: str | None) -> tuple[int | None, int | None]:
    """Durasi & percobaan disisipkan TraceStep.to_db_row di output_summary."""
    m = META_RE.search(output_summary or "")
    return (int(m.group(1)), int(m.group(2))) if m else (None, None)


def logs_frame(logs: list[dict]) -> pd.DataFrame:
    rows = []
    for log in logs:
        dur, attempts = log_meta(log.get("output_summary"))
        rows.append({
            "Waktu": local_time(log.get("created_at")), "Agent": log.get("agent_name"),
            "Run": log.get("run_id"), "Tool": log.get("tool_name"), "Status": log.get("status"),
            "Durasi (ms)": dur, "Percobaan": attempts,
            "Output": META_RE.sub("", log.get("output_summary") or "").strip(),
            "Error": log.get("error_message") or "", "Actor": log.get("actor_role") or "",
        })
    return pd.DataFrame(rows)


def open_execution_log(run_id: str, key: str) -> None:
    """Tombol telusur: dari keputusan ke langkah eksekusi sumbernya (DESIGN 14.3)."""
    if st.button("Lihat Execution Log run ini", key=key, icon=":material/manage_search:"):
        st.session_state[TRACE_RUN_KEY] = run_id
        st.switch_page("app_pages/execution_log.py")


def reason_trace_panel(trace: dict, *, run_id: str | None = None, key: str = "") -> None:
    """Panel Reason Trace generik untuk Analyst, Purchaser, dan Negotiator."""
    trace = trace or {}
    st.markdown(f"**Ringkasan:** {trace.get('summary') or LABEL_NOT_FOUND}")
    for field, title in (("demand_trend", "Tren permintaan"), ("stockout_prediction", "Prediksi stockout"),
                         ("restock_recommendation", "Rekomendasi restock"), ("decision", "Keputusan")):
        if trace.get(field):
            st.markdown(f"**{title}:** {trace[field]}")
    evidence = trace.get("evidence") or []
    if evidence:
        frame = pd.DataFrame(evidence)
        cols = [c for c in ("label", "value", "unit", "source") if c in frame.columns]
        st.dataframe(frame[cols].astype(str), hide_index=True, width="stretch",
                     column_config={"label": "Faktor", "value": "Nilai", "unit": "Satuan", "source": "Sumber"})
    else:
        st.caption(f"Evidence: {LABEL_NOT_FOUND}")
    if trace.get("scores"):
        st.caption("Skor: " + ", ".join(f"{k[:8]}={v}" for k, v in trace["scores"].items()))
    if trace.get("assumptions"):
        st.markdown("**Asumsi & kebijakan:**\n" + "\n".join(f"- {a}" for a in trace["assumptions"]))
    if run_id:
        open_execution_log(run_id, key=f"trace-log-{key}")


def trigger_sheets_sync_ui(repository, actor, clear_cache_fn=None) -> bool:
    """Jalankan sinkronisasi dua arah Google Sheets ↔ Supabase dari komponen UI.

    Mengembalikan True jika sinkronisasi berhasil dan halaman perlu di-rerun.
    """
    from datetime import datetime, timezone
    from src.config import sheets_configured, sheets_settings
    from src.services import agent_service_client
    from src.services.inventory_sync import sync_inventory_two_way
    from src.services.sheets_client import SheetsClient
    from src.services.supabase_client import has_write_key

    if sheets_configured():
        try:
            with st.spinner("Menyinkronkan Google Sheets ↔ Supabase..."):
                sheets = SheetsClient()
                can_write = has_write_key()
                report = sync_inventory_two_way(
                    repository,
                    sheets,
                    run_id=f"manual-sync-{int(datetime.now(timezone.utc).timestamp())}",
                    write_enabled=can_write,
                    actor_id=actor.user_id,
                    actor_role=actor.role,
                )
                if clear_cache_fn:
                    clear_cache_fn()
                if report.status == "failed":
                    st.error(f"Sinkronisasi gagal: {'; '.join(report.errors)}")
                    return False
                msg = (
                    f"Sinkronisasi berhasil ({report.status}): "
                    f"{report.rows_written_supabase} baris ke Supabase, "
                    f"{report.rows_written_sheets} ke Sheets."
                )
                if report.conflicts:
                    msg += f" (Terdeteksi {report.conflicts} perbedaan stok yang diselaraskan)"
                st.success(msg)
                return True
        except Exception as exc:  # noqa: BLE001
            st.error(f"Gagal sinkronisasi ({type(exc).__name__}): {exc}")
            return False
    elif agent_service_client.configured():
        try:
            with st.spinner("Menyinkronkan Google Sheets ↔ Supabase via Agent Service..."):
                import importlib
                importlib.reload(agent_service_client)
                res = agent_service_client.trigger_inventory_sync(actor.user_id)
                if clear_cache_fn:
                    clear_cache_fn()
                status = res.get("status", "success")
                if status == "failed":
                    st.error(f"Sinkronisasi via Agent Service gagal: {'; '.join(res.get('errors', []))}")
                    return False
                supa_rows = res.get("rows_written_supabase", 0)
                sheet_rows = res.get("rows_written_sheets", 0)
                msg = (
                    f"Sinkronisasi via Agent Service berhasil ({status}): "
                    f"{supa_rows} baris ke Supabase, {sheet_rows} ke Sheets."
                )
                conflicts = res.get("conflicts", 0)
                if conflicts:
                    msg += f" (Terdeteksi {conflicts} perbedaan stok yang diselaraskan)"
                st.success(msg)
                return True
        except Exception as exc:  # noqa: BLE001
            st.error(f"Gagal sinkronisasi via Agent Service ({type(exc).__name__}): {exc}")
            return False
    else:
        cfg = sheets_settings()
        missing = []
        if not cfg.get("spreadsheet_id"):
            missing.append("GOOGLE_SHEETS_SPREADSHEET_ID")
        if not (cfg.get("service_account_file") or cfg.get("service_account_json")):
            missing.append("GOOGLE_SERVICE_ACCOUNT_JSON")
        missing_str = ", ".join(missing) if missing else "GOOGLE_SHEETS_SPREADSHEET_ID / GOOGLE_SERVICE_ACCOUNT_JSON"
        st.warning(
            f"Google Sheets belum dikonfigurasi di environment ini ({missing_str}). "
            "Tambahkan secret tersebut di Streamlit Cloud (App settings -> Secrets) atau konfigurasi di Agent Service."
        )
        return False

