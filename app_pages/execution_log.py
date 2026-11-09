"""Execution Log (F-25, SRS-F-38/60, NF-04/08): tool, status, retry, error, durasi per agent."""
from __future__ import annotations

import streamlit as st

from src.config import LABEL_NOT_FOUND
from src.services.auth import ALL_USER_ROLES
from src.ui.components import TRACE_RUN_KEY, logs_frame
from src.ui.session import page_context
from src.utils.errors import safe_error

actor, user_client, repository = page_context(ALL_USER_ROLES, "melihat execution log")
st.title("Execution log")
st.caption("Jejak eksekusi nyata setiap agent: kapan, tool apa, berhasil/gagal/di-retry, durasi, dan actor.")

with st.container(horizontal=True):
    agent = st.selectbox("Agent", ["Semua", "Orchestrator", "Analyst", "Purchaser", "Negotiator"])
    preset = st.session_state.pop(TRACE_RUN_KEY, None)
    if preset:  # datang dari tombol telusur Reason Trace; tetap terisi saat filter lain diubah
        st.session_state["exec_run_filter"] = preset
    run_filter = st.text_input("Run ID", key="exec_run_filter", placeholder="ANL-... / PUR-... / NEG-... / WF-...")
    limit = st.select_slider("Jumlah log", [100, 250, 500, 1000], value=250)

try:
    logs = repository.fetch_recent_agent_logs(limit=limit, agent=None if agent == "Semua" else agent,
                                              run_id=run_filter.strip() or None)
except Exception as exc:  # noqa: BLE001
    st.error(f"{LABEL_NOT_FOUND} - agent_logs tidak bisa dibaca ({safe_error(exc)}).")
    st.stop()
if not logs:
    st.info(f"{LABEL_NOT_FOUND} - belum ada log untuk filter ini.")
    st.stop()

df = logs_frame(logs)
failed = df["Status"].isin(["failed"]).sum()
with st.container(horizontal=True):
    st.metric("Langkah", len(df), border=True)
    st.metric("Run", df["Run"].nunique(), border=True)
    st.metric("Gagal", int(failed), border=True)
    st.metric("Retry / fallback", int(df["Status"].isin(["retried", "fallback"]).sum()), border=True)
    med = df["Durasi (ms)"].dropna()
    st.metric("Durasi median", f"{int(med.median())} ms" if len(med) else LABEL_NOT_FOUND, border=True)

status_pick = st.pills("Status", sorted(df["Status"].dropna().unique()), selection_mode="multi")
view = df[df["Status"].isin(status_pick)] if status_pick else df
st.dataframe(view, hide_index=True, width="stretch", column_config={
    "Durasi (ms)": st.column_config.NumberColumn(format="%d"),
    "Output": st.column_config.TextColumn(width="large"),
})

st.subheader("Durasi per tool")
per_tool = (df.dropna(subset=["Durasi (ms)"]).groupby(["Agent", "Tool"], as_index=False)["Durasi (ms)"]
            .median().sort_values("Durasi (ms)", ascending=False).head(15))
if not per_tool.empty:
    st.bar_chart(per_tool, x="Tool", y="Durasi (ms)", color="Agent", horizontal=True)
else:
    st.caption(f"Durasi: {LABEL_NOT_FOUND}")

st.download_button("Unduh log (CSV)", view.to_csv(index=False).encode("utf-8"), file_name="execution-log.csv",
                   mime="text/csv", icon=":material/download:")
