"""Analitik enterprise (F-33, SRS-F-50, NF-12).

KPI dihitung dari tabel nyata. Label basis wajib: aktual / backtest / target SRS.
Target 80% stockout & 15 jam/minggu baru dibuktikan lewat simulasi replay (P6).
"""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

from src.config import LABEL_NOT_FOUND
from src.services.analytics import compute_kpis, risk_by_branch
from src.services.auth import ALL_USER_ROLES
from src.services.budget import load_budget_snapshot
from src.services.dashboard import sync_forecasts_with_inventory
from src.ui.components import dummy_banner
from src.ui.session import page_context
from src.utils.errors import safe_error

actor, user_client, repository = page_context(ALL_USER_ROLES, "melihat analitik")
st.title("Analitik enterprise")
st.caption("Angka dihitung dari Supabase. Setiap KPI menyebut basis dan metode perhitungannya.")
period = datetime.now(ZoneInfo("Asia/Jakarta")).strftime("%Y-%m")
issues: list[str] = []


def attempt(label, fn, default):
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001 - KPI lain tetap tampil
        issues.append(f"{label}: {safe_error(exc)}")
        return default


run = attempt("analyst", repository.latest_analyst_run_id, None)
raw_forecasts = attempt("forecasts", lambda: repository.fetch_forecasts(run), []) if run else []
products = attempt("products", repository.fetch_products, [])
branches_list = attempt("branches", repository.fetch_branches, [])
branches = {str(b["id"]): b["name"] for b in branches_list}
inventory = attempt("inventory", repository.fetch_inventory, [])
forecasts = sync_forecasts_with_inventory(raw_forecasts, inventory, products, branches_list) if raw_forecasts else []
contracts = attempt("contracts", repository.fetch_reference_contracts, [])
budget = attempt("budget", lambda: load_budget_snapshot(repository, period).to_dict(), None)
pos = attempt("purchase_orders", repository.fetch_recent_purchase_orders, [])
jobs = attempt("workflow_jobs", lambda: repository.fetch_workflow_jobs(50), [])
approvals = attempt("approval_requests", lambda: repository.fetch_approval_requests(50), [])
logs = attempt("agent_logs", lambda: repository.fetch_recent_agent_logs(1000), [])

kpis = compute_kpis(forecasts=forecasts, products=products, contracts=contracts, budget=budget,
                    purchase_orders=pos, jobs=jobs, approvals=approvals, logs=logs)
by_key = {k.key: k for k in kpis}


def show(key: str) -> None:
    k = by_key[key]
    if k.value is None:
        value = LABEL_NOT_FOUND
    elif k.unit == "Rp":
        value = f"Rp {k.value:,.0f}".replace(",", ".")
    else:
        value = f"{k.value:g} {k.unit}"
    st.metric(k.label, value, help=f"{k.basis.upper()} · {k.method} · n={k.sample}", border=True)


dummy_banner(repository)
st.subheader("Operasional (aktual)")
with st.container(horizontal=True):
    for key in ("stockout_risk", "inventory_turnover", "dead_stock_value", "budget_utilization"):
        show(key)
with st.container(horizontal=True):
    for key in ("procurement_cycle", "approval_latency", "po_sent", "tool_success"):
        show(key)

st.subheader("Kualitas forecast (backtest)")
with st.container(horizontal=True):
    show("forecast_mape")
    show("baseline_mape")
mape = pd.DataFrame([{"SKU-cabang": f"{r.get('product_id')}|{r.get('branch_id')}", "Model": r.get("model_mape"),
                      "Baseline": r.get("baseline_mape")} for r in forecasts
                     if r.get("model_mape") is not None and r.get("baseline_mape") is not None])
if not mape.empty:
    better = float((mape["Model"] <= mape["Baseline"]).mean() * 100)
    st.caption(f"Model tren x musiman lebih akurat dari baseline pada {better:.0f}% dari {len(mape)} SKU-cabang "
               "(holdout 14 hari).")

st.subheader("Risiko per cabang")
risk_rows = risk_by_branch(forecasts, branches)
if risk_rows:
    st.bar_chart(pd.DataFrame(risk_rows), x="Cabang", y=["URGENT", "RISIKO"], stack=True)
else:
    st.caption(f"Risiko per cabang: {LABEL_NOT_FOUND}")

st.subheader("Target SRS (belum diukur)")
st.caption("Ditampilkan sebagai target, bukan hasil. Bukti lewat simulasi replay Kebijakan A vs B (roadmap P6).")
with st.container(horizontal=True):
    for key in ("target_stockout", "target_hours", "target_dead_stock"):
        show(key)

with st.expander("Metode & basis semua KPI"):
    st.dataframe(pd.DataFrame([k.to_dict() for k in kpis]), hide_index=True, width="stretch")
for issue in issues:
    st.caption(f"Sumber tidak tersedia - {issue}")
