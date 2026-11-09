"""Halaman Prediksi & Risiko (Roadmap P1-9, F-07/F-08/F-09/F-10, SRS-F-11..15, F-49, F-60).

Menampilkan hasil Agent Analyst TERAKHIR yang tersimpan di Supabase (bukan session state):
daftar risiko 4-7 hari, grafik forecast, days-to-stockout, Reason Trace, dan Execution Trace.
"""
from __future__ import annotations

import math
from datetime import date, timedelta

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from src.config import LABEL_NOT_FOUND, LABEL_SIMULASI, RISK_MAX_DAYS, URGENT_MAX_DAYS
from src.services.auth import (
    ANALYST_RUN_ROLES,
    ANALYST_VIEW_ROLES,
)
from src.services.supabase_client import has_write_key
from src.services.work_admission import (
    WorkflowDeadlineExceeded,
    WorkRejected,
    work_admission,
)
from src.ui.session import page_context
from src.utils import formulas as F

TEAL, NAVY, MINT = "#0E7C61", "#1E2A44", "#EFF4F1"

actor, user_client, repository = page_context(ANALYST_VIEW_ROLES, "melihat prediksi dan risiko")
st.title("Prediksi & Risiko Stockout")
st.caption("Agent 1 - The Analyst | histori 6 bulan | deteksi risiko habis 4-7 hari | stok per cabang, tidak dijumlahkan")


def master():
    return repository.fetch_products(), repository.fetch_branches()


def latest_run():
    run_id = repository.latest_analyst_run_id()
    if not run_id:
        return None, [], []
    return run_id, repository.fetch_forecasts(run_id), repository.fetch_agent_logs(run_id)


def sales_series(product_id: str, branch_id: str, start: date, end: date):
    return repository.fetch_sales_series(product_id, branch_id, start, end)


def dummy_flag() -> bool:
    try:
        return repository.has_dummy_sales()
    except Exception:  # noqa: BLE001
        return False


def _num(x):
    return None if x is None else float(x)


def _days_txt(x) -> str:
    return "-" if x is None or (isinstance(x, float) and math.isinf(x)) else f"{x:.1f}"


# ------------------------------------------------------------------ sidebar: jalankan agent
with st.sidebar:
    st.header("Agent Analyst")
    can_write = has_write_key()
    can_run = actor.role in ANALYST_RUN_ROLES
    if not can_run:
        st.caption("Role ini hanya boleh melihat hasil Analyst.")
    if not can_write:
        st.caption("SUPABASE_SERVICE_ROLE_KEY belum diisi: hasil run tidak tersimpan (hanya tampil sementara).")
    if st.button("Jalankan Agent Analyst", type="primary", width="stretch", disabled=not can_run):
        from src.agents.analyst import run_analyst

        try:
            with work_admission.admit(actor, "analyst"), st.status(
                "Analyst berjalan: sinkron stok, baca histori, forecast, deteksi risiko...",
                expanded=False,
            ) as s:
                out = run_analyst(actor=actor, persist=can_write, read_client=user_client)
                s.update(
                    label=f"Selesai: status {out['status']}",
                    state="complete" if out["status"] == "completed" else "error",
                )
            st.session_state["analyst_preview"] = None if out.get("persisted") else out
            for warning in out.get("warnings", []):
                st.warning(warning)
        except (WorkRejected, WorkflowDeadlineExceeded, PermissionError) as exc:
            st.error(str(exc))

# ------------------------------------------------------------------ load data
try:
    products, branches = master()
    run_id, forecasts, logs = latest_run()
except Exception as exc:  # noqa: BLE001
    if "PGRST205" in str(exc):
        st.error(
            f"{LABEL_NOT_FOUND} - tabel P1 belum dibuat di Supabase. Jalankan di Supabase SQL Editor, berurutan:\n\n"
            "1. `supabase/migrations/002_p1_analyst.sql`\n"
            "2. `supabase/seed/002_sales_history_dummy.sql`\n\n"
            "Lalu muat ulang halaman ini."
        )
    else:
        st.error(f"{LABEL_NOT_FOUND} - Supabase tidak bisa dibaca. Periksa konfigurasi dan log server.")
    st.stop()

pmap = {p["id"]: p for p in products}
bmap = {b["id"]: b["name"] for b in branches}
preview = st.session_state.get("analyst_preview")

if preview and preview.get("results"):
    run_id = preview["run_id"]
    rows = [r for r in preview["results"] if r.get("risk_level") != F.RISK_NO_DATA]
    logs = preview["execution_trace"]
    st.info(f"Menampilkan run {run_id} yang BELUM tersimpan (tanpa service role key).")
    df = pd.DataFrame([{
        "product_id": r["product_id"], "branch_id": r["branch_id"], "sku": r["sku"], "nama": r["product_name"],
        "cabang": r["branch"], "stok": r["stock_qty"], "avg_harian": r["avg_daily_sales"],
        "forecast_harian": r["forecast_daily_demand"], "dts": r["days_to_stockout"],
        "dts_forecast": r["days_to_stockout_forecast"], "dts_low": r["days_to_stockout_low"],
        "dts_high": r["days_to_stockout_high"], "safety_stock": r["safety_stock"], "restock": r["restock_qty"],
        "lead_time": r["lead_time_days"], "risiko": r["risk_level"], "as_of": r["as_of_date"],
        "reason_trace": {**r["reason_trace"], "projection": r["forecast"]["projection"],
                         "projection_start": r["forecast"]["projection_start"]},
    } for r in rows])
elif forecasts:
    df = pd.DataFrame([{
        "product_id": f["product_id"], "branch_id": f["branch_id"], "sku": pmap.get(f["product_id"], {}).get("sku"),
        "nama": pmap.get(f["product_id"], {}).get("name"), "cabang": bmap.get(f["branch_id"]),
        "stok": _num(f["stock_qty"]), "avg_harian": _num(f["avg_daily_sales"]),
        "forecast_harian": _num(f["forecast_daily_demand"]), "dts": _num(f["days_to_stockout"]),
        "dts_forecast": _num(f["days_to_stockout_forecast"]), "dts_low": _num(f["days_to_stockout_low"]),
        "dts_high": _num(f["days_to_stockout_high"]), "safety_stock": _num(f["safety_stock"]),
        "restock": _num(f["restock_qty"]), "lead_time": f["lead_time_days"], "risiko": f["risk_level"],
        "as_of": f["as_of_date"], "reason_trace": f["reason_trace"],
    } for f in forecasts])
else:
    st.warning(
        f"{LABEL_NOT_FOUND} - belum ada hasil Agent Analyst. Pastikan migrasi "
        "`supabase/migrations/002_p1_analyst.sql` + seed `supabase/seed/002_sales_history_dummy.sql` sudah dijalankan, "
        "lalu klik **Jalankan Agent Analyst** di sidebar."
    )
    if logs:
        st.dataframe(pd.DataFrame(logs), hide_index=True, width="stretch")
    st.stop()

is_dummy = dummy_flag()

# ------------------------------------------------------------------ KPI
risk_df = df[df["risiko"].isin([F.RISK_URGENT, F.RISK_WARNING])].sort_values("dts")
c1, c2, c3, c4 = st.columns(4)
c1.metric("Berisiko", len(risk_df), help="SKU-cabang dengan days-to-stockout <= 7 hari")
c2.metric("URGENT", int((risk_df["risiko"] == F.RISK_URGENT).sum()), help="<= 4 hari, atau habis sebelum lead time")
c3.metric("Cabang", f"{risk_df['cabang'].nunique()}/{len(bmap)}", help="Cabang yang punya SKU berisiko")
c4.metric("Data s/d", date.fromisoformat(str(df["as_of"].iloc[0])).strftime("%d %b"), help="Tanggal histori terakhir")
st.caption(f"Run: `{run_id}` | Sumber: Supabase sales_history (180 hari), inventory / Google Sheets, products")

branch_opt = st.selectbox("Filter cabang", ["Semua cabang (ditampilkan per cabang)"] + sorted(bmap.values()))
view = risk_df if branch_opt.startswith("Semua") else risk_df[risk_df["cabang"] == branch_opt]

# ------------------------------------------------------------------ daftar risiko
st.subheader("Daftar risiko stockout")
if is_dummy:
    st.warning(LABEL_SIMULASI)
if view.empty:
    st.success("Tidak ada SKU berisiko habis dalam 7 hari untuk filter ini.")
else:
    table = view.assign(
        rentang_forecast=view.apply(lambda r: f"{_days_txt(r['dts_low'])} - {_days_txt(r['dts_high'])}", axis=1)
    )[["risiko", "sku", "nama", "cabang", "stok", "avg_harian", "dts", "dts_forecast", "rentang_forecast",
       "safety_stock", "restock", "lead_time"]]
    st.dataframe(
        table,
        hide_index=True,
        width="stretch",
        column_config={
            "avg_harian": st.column_config.NumberColumn("Rata-rata/hari (total/180)", format="%.2f"),
            "dts": st.column_config.NumberColumn("Days-to-stockout (rumus)", format="%.2f"),
            "dts_forecast": st.column_config.NumberColumn("Days-to-stockout (forecast)", format="%.2f"),
            "rentang_forecast": "Rentang ketidakpastian (hari)",
            "safety_stock": st.column_config.NumberColumn("Safety stock", format="%.1f"),
            "restock": st.column_config.NumberColumn("Kebutuhan restock (unit)", format="%d"),
            "lead_time": "Lead time (hari)",
        },
    )

    # days-to-stockout chart
    labels = [f"{r.sku} | {r.cabang}" for r in view.itertuples()]
    fig = go.Figure()
    fig.add_bar(y=labels, x=view["dts"], orientation="h", name="Rumus resmi", marker_color=TEAL)
    fig.add_bar(y=labels, x=view["dts_forecast"], orientation="h", name="Forecast tren", marker_color=NAVY)
    for x, txt in ((URGENT_MAX_DAYS, "URGENT 4 hari"), (RISK_MAX_DAYS, "Batas risiko 7 hari")):
        fig.add_vline(x=x, line_dash="dash", line_color=NAVY, annotation_text=txt, annotation_position="top")
    fig.update_layout(barmode="group", height=120 + 45 * len(view), margin={"l": 10, "r": 10, "t": 30, "b": 10},
                      xaxis_title="Hari sampai stok habis", yaxis={"autorange": "reversed"},
                      plot_bgcolor="#FFFFFF", legend={"orientation": "h", "y": -0.2})
    st.plotly_chart(fig, width="stretch")

# ------------------------------------------------------------------ grafik forecast per item
st.subheader("Grafik forecast")
pick_from = view if not view.empty else df.sort_values("dts").head(10)
options = {f"{r.sku} - {r.nama} | {r.cabang} ({r.risiko})": r for r in pick_from.itertuples()}
choice = st.selectbox("Pilih SKU-cabang", list(options))
item = options[choice]
rt = item.reason_trace or {}
as_of = date.fromisoformat(str(item.as_of))
hist = sales_series(item.product_id, item.branch_id, as_of - timedelta(days=59), as_of)
projection = rt.get("projection") or []
proj_start = date.fromisoformat(rt["projection_start"]) if rt.get("projection_start") else as_of + timedelta(days=1)

g1, g2 = st.columns(2)
with g1:
    fig = go.Figure()
    if hist:
        fig.add_scatter(x=[h["sale_date"] for h in hist], y=[h["qty"] for h in hist], mode="lines",
                        name="Penjualan aktual (60 hari)", line={"color": TEAL})
    else:
        st.caption(f"Histori: {LABEL_NOT_FOUND}")
    fig.add_scatter(x=[proj_start + timedelta(days=i) for i in range(len(projection))], y=projection, mode="lines",
                    name="Forecast", line={"color": NAVY, "dash": "dash"})
    fig.add_hline(y=item.avg_harian, line_dash="dot", line_color=NAVY, annotation_text="Rata-rata 180 hari")
    fig.update_layout(title="Permintaan harian: aktual vs forecast", height=360, plot_bgcolor="#FFFFFF",
                      margin={"l": 10, "r": 10, "t": 40, "b": 10}, legend={"orientation": "h", "y": -0.25})
    st.plotly_chart(fig, width="stretch")
with g2:
    remaining, days_axis, level = [], [], float(item.stok)
    fc_days = item.dts_forecast
    horizon = 21 if fc_days is None or pd.isna(fc_days) or math.isinf(fc_days) else min(21, math.ceil(fc_days) + 4)
    for i, demand in enumerate([0.0, *projection[:horizon]]):
        level = max(level - demand, 0.0)
        remaining.append(level)
        days_axis.append(as_of + timedelta(days=i))
    fig = go.Figure()
    fig.add_scatter(x=days_axis, y=remaining, mode="lines+markers", name="Proyeksi stok", line={"color": TEAL},
                    fill="tozeroy", fillcolor=MINT)
    fig.add_hline(y=item.safety_stock, line_dash="dash", line_color=NAVY, annotation_text="Safety stock")
    fig.update_layout(title=f"Proyeksi sisa stok {item.cabang} (tanpa restock)", height=360, plot_bgcolor="#FFFFFF",
                      margin={"l": 10, "r": 10, "t": 40, "b": 10}, showlegend=False)
    st.plotly_chart(fig, width="stretch")

# ------------------------------------------------------------------ Reason Trace
st.subheader("Reason Trace - Analyst")
if is_dummy:
    st.caption(LABEL_SIMULASI)
targets = view if not view.empty else pd.DataFrame([item._asdict()])
for r in targets.itertuples():
    trace = r.reason_trace or {}
    with st.expander(f"[{r.risiko}] {r.sku} - {r.nama} | {r.cabang} - {_days_txt(r.dts)} hari", expanded=r.sku == item.sku and r.cabang == item.cabang):
        st.markdown(f"**Ringkasan:** {trace.get('summary', LABEL_NOT_FOUND)}")
        st.markdown(f"**Tren permintaan:** {trace.get('demand_trend', LABEL_NOT_FOUND)}")
        st.markdown(f"**Prediksi stockout:** {trace.get('stockout_prediction', LABEL_NOT_FOUND)}")
        st.markdown(f"**Rekomendasi restock:** {trace.get('restock_recommendation', LABEL_NOT_FOUND)}")
        if trace.get("evidence"):
            st.markdown("**Evidence (angka + sumber):**")
            st.dataframe(pd.DataFrame(trace["evidence"]).astype(str), hide_index=True, width="stretch")
        if trace.get("assumptions"):
            st.markdown("**Asumsi:**\n" + "\n".join(f"- {a}" for a in trace["assumptions"]))
        dq = trace.get("data_quality") or {}
        if dq:
            note = "; ".join(dq.get("notes", [])) or "tidak ada catatan"
            flag = "PERLU VALIDASI" if dq.get("needs_validation") else "OK"
            st.caption(f"Kualitas data: {flag} | {dq.get('observed_days')} hari terisi, {dq.get('missing_days')} hilang | {note}")

# ------------------------------------------------------------------ Execution Trace
st.subheader("Execution Trace - Analyst")
if logs:
    log_df = pd.DataFrame(logs)
    cols = [c for c in ["created_at", "tool_name", "status", "input_summary", "output_summary", "error_message",
                        "duration_ms", "attempts"] if c in log_df.columns]
    st.dataframe(log_df[cols], hide_index=True, width="stretch")
else:
    st.caption(f"Execution trace: {LABEL_NOT_FOUND}")

with st.expander("Log sinkron Google Sheets <-> Supabase"):
    try:
        sync = repository.fetch_sync_logs()
        st.dataframe(pd.DataFrame(sync), hide_index=True, width="stretch") if sync else st.caption(LABEL_NOT_FOUND)
    except Exception:  # noqa: BLE001
        st.caption(f"{LABEL_NOT_FOUND}: histori tidak dapat dimuat")
