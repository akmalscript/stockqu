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
