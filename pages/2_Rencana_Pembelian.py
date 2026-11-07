"""Halaman Rencana Pembelian - Agent 2 Purchaser (Roadmap P2, F-11..F-15)."""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from src.config import BUDGET_MONTHLY, LABEL_NOT_FOUND, LABEL_SIMULASI
from src.services.supabase_client import SupabaseRepository, has_write_key

TEAL, NAVY, MINT, WHITE = "#0E7C61", "#1E2A44", "#EFF4F1", "#FFFFFF"

st.set_page_config(page_title="StockQu - Rencana Pembelian", page_icon="📦", layout="wide")
st.title("Rencana Pembelian")
st.caption(
    "Agent 2 - The Purchaser | budget bulanan Rp 50.000.000 | "
    "prioritas per SKU-cabang | validasi budget, MOQ, pack, dan duplikasi"
)


@st.cache_resource
def repo() -> SupabaseRepository:
    return SupabaseRepository()


@st.cache_data(ttl=60)
def master_data():
    repository = repo()
    return repository.fetch_products(), repository.fetch_branches()


@st.cache_data(ttl=30)
def latest_plan():
    repository = repo()
    run_id = repository.latest_purchaser_run_id()
    if not run_id:
        return None, None, [], []
    plan = repository.fetch_procurement_plan(run_id)
    if not plan:
        return run_id, None, [], repository.fetch_agent_logs(run_id)
    return run_id, plan, repository.fetch_procurement_plan_items(plan["id"]), repository.fetch_agent_logs(run_id)


@st.cache_data(ttl=300)
def dummy_flag() -> bool:
    try:
        return repo().has_dummy_sales()
    except Exception:  # noqa: BLE001
        return False


def rupiah(value) -> str:
    return f"Rp {float(value or 0):,.0f}".replace(",", ".")


with st.sidebar:
    st.header("Agent Purchaser")
    period = st.text_input(
        "Periode budget",
        value=datetime.now(ZoneInfo("Asia/Jakarta")).strftime("%Y-%m"),
        help="Format YYYY-MM. Budget tetap dibatasi Rp 50.000.000 per bulan.",
    )
    can_write = has_write_key()
    if not can_write:
        st.caption("SUPABASE_SERVICE_ROLE_KEY belum diisi: hasil hanya preview dan tidak tersimpan.")
    if st.button("Jalankan Agent Purchaser", type="primary", width="stretch"):
        from src.agents.purchaser import run_purchaser

        try:
            datetime.strptime(period, "%Y-%m")
        except ValueError:
            st.error("Periode harus berformat YYYY-MM, contoh 2026-11.")
        else:
            with st.status("Purchaser membaca Analyst, menghitung budget, dan menjalankan optimizer...", expanded=False) as status:
                output = run_purchaser(persist=can_write, period=period)
                state = "complete" if output.get("status") != "failed" else "error"
                status.update(label=f"Selesai: status {output.get('status')}", state=state)
            st.session_state["purchaser_preview"] = None if output.get("persisted") else output
            for warning in output.get("warnings", []):
                st.warning(warning)
            latest_plan.clear()


try:
    products, branches = master_data()
    run_id, plan, items, logs = latest_plan()
except Exception as exc:  # noqa: BLE001
    message = str(exc)
    if "PGRST205" in message or "procurement_plans" in message or "persist_procurement_plan" in message:
        st.error(
            f"{LABEL_NOT_FOUND} - skema P2 belum tersedia. Jalankan migration "
            "`supabase/migrations/20261107111649_p2_purchaser.sql` di Supabase, lalu muat ulang halaman."
        )
    else:
        st.error(f"{LABEL_NOT_FOUND} - Supabase tidak bisa dibaca: {exc}")
    st.stop()

preview = st.session_state.get("purchaser_preview")
if preview and preview.get("analyst_run_id"):
    run_id = preview["run_id"]
    budget = preview["budget"]
    optimization = preview.get("optimization", {})
    plan = {
        "id": preview.get("plan_id"),
        "run_id": run_id,
        "analyst_run_id": preview["analyst_run_id"],
        "period": preview["period"],
        "budget_total": budget["total_budget"],
        "budget_used": budget["used_budget"],
        "budget_committed": budget["committed_budget"],
        "budget_available": budget["available_budget"],
        "proposed_total": optimization.get("proposed_total", 0),
        "remaining_after_plan": max(
            float(budget["available_budget"]) - float(optimization.get("proposed_total", 0)), 0
        ),
        "status": preview.get("status"),
        "optimization_method": optimization.get("method", "not_run"),
        "impact": preview.get("impact", {}),
        "validation": preview.get("validation", {}),
    }
    items = preview.get("items", [])
    logs = preview.get("execution_trace", [])
    st.info(f"Menampilkan preview run `{run_id}` yang belum tersimpan.")

if not plan:
    st.warning(
        f"{LABEL_NOT_FOUND} - belum ada rencana Purchaser. Jalankan migration P2, pastikan Agent Analyst sudah "
        "memiliki hasil tersimpan, lalu klik **Jalankan Agent Purchaser**."
    )
    st.stop()

if dummy_flag():
    st.warning(LABEL_SIMULASI)

if float(plan.get("budget_total") or 0) > BUDGET_MONTHLY:
    st.error("Budget plan melebihi plafon kebijakan Rp 50.000.000. Plan tidak boleh dipakai.")

st.caption(
    f"Run: `{run_id}` | Analyst: `{plan['analyst_run_id']}` | Periode: {plan['period']} | "
    f"Status: {plan['status']} | Optimizer: {plan['optimization_method']}"
)

# KPI mengikuti hierarki ringkas: sumber budget -> usulan -> sisa.
k1, k2, k3 = st.columns(3)
k1.metric("Budget bulanan", rupiah(plan["budget_total"]), help="Plafon keras maksimal Rp 50.000.000")
k2.metric("Sudah dipakai", rupiah(plan["budget_used"]), help="Sumber: Supabase budgets.used_budget")
k3.metric("PO committed", rupiah(plan["budget_committed"]), help="PO waiting approval bulan berjalan")
k4, k5, k6 = st.columns(3)
k4.metric("Tersedia sebelum plan", rupiah(plan["budget_available"]))
k5.metric("Usulan Purchaser", rupiah(plan["proposed_total"]))
k6.metric("Sisa setelah plan", rupiah(plan["remaining_after_plan"]))

used_after = float(plan["budget_total"]) - float(plan["remaining_after_plan"])
utilization = 0.0 if not float(plan["budget_total"]) else 100 * used_after / float(plan["budget_total"])
figure = go.Figure(go.Indicator(
    mode="gauge+number",
    value=utilization,
    number={"suffix": "%", "font": {"color": NAVY}},
    title={"text": "Utilisasi budget setelah usulan", "font": {"color": NAVY}},
    gauge={
        "axis": {"range": [0, 100], "ticksuffix": "%", "tickcolor": NAVY},
        "bar": {"color": TEAL},
        "bgcolor": MINT,
        "bordercolor": NAVY,
        "threshold": {"line": {"color": NAVY, "width": 3}, "value": 100},
    },
))
figure.update_layout(
    height=260,
    margin={"l": 30, "r": 30, "t": 50, "b": 10},
    paper_bgcolor=WHITE,
    font_color=NAVY,
)
st.plotly_chart(figure, width="stretch", config={"displayModeBar": False})

product_map = {str(row["id"]): row for row in products}
branch_map = {str(row["id"]): row["name"] for row in branches}


def display_item(item: dict) -> dict:
    product = product_map.get(str(item["product_id"]), {})
    return {
        "rank": item.get("rank"),
        "keputusan": str(item.get("decision", "")).upper(),
        "sku": item.get("sku") or product.get("sku") or LABEL_NOT_FOUND,
        "item": item.get("product_name") or product.get("name") or LABEL_NOT_FOUND,
        "cabang": item.get("branch") or branch_map.get(str(item["branch_id"]), LABEL_NOT_FOUND),
        "risiko": item.get("risk_level"),
        "diminta": item.get("requested_qty"),
        "dialokasikan": item.get("allocated_qty"),
        "harga_referensi": item.get("unit_price"),
        "moq": item.get("moq"),
        "pack": item.get("pack_size"),
        "total": item.get("line_total"),
        "skor": item.get("priority_score"),
    }


rows = [display_item(item) for item in items]
table = pd.DataFrame(rows)
st.subheader("Alokasi per SKU-cabang")
if table.empty:
    st.info("Tidak ada item berisiko yang membutuhkan pembelian pada run ini.")
else:
    st.dataframe(
        table,
        hide_index=True,
        width="stretch",
        column_config={
            "rank": st.column_config.NumberColumn("Rank", format="%d"),
            "harga_referensi": st.column_config.NumberColumn("Harga referensi", format="Rp %.0f"),
            "total": st.column_config.NumberColumn("Total alokasi", format="Rp %.0f"),
            "skor": st.column_config.NumberColumn("Priority score", format="%.4f"),
        },
    )
    st.caption("Tabel adalah alternatif aksesibel untuk gauge/visual; keputusan tidak dibedakan dengan warna saja.")

deferred = [item for item in items if item.get("decision") == "deferred"]
st.subheader("Item ditunda dan alasannya")
if not deferred:
    st.success("Tidak ada item yang ditunda pada run ini.")
else:
    for item in deferred:
        trace = item.get("reason_trace") or {}
        st.warning(trace.get("summary", f"{LABEL_NOT_FOUND} - alasan penundaan tidak tersedia."))

impact = plan.get("impact") or {}
st.subheader("Estimasi dampak berbasis data")
i1, i2, i3, i4 = st.columns(4)
i1.metric("Item dialokasikan", int(impact.get("selected_items") or 0))
i2.metric("Item ditunda", int(impact.get("deferred_items") or 0))
i3.metric("Tambahan cakupan", f"{float(impact.get('additional_coverage_days') or 0):.1f} hari")
i4.metric("Estimasi margin terlindungi", rupiah(impact.get("estimated_margin_protected")))
st.caption(f"Dasar hitung: {impact.get('impact_basis', LABEL_NOT_FOUND)}. Ini estimasi, bukan realisasi penjualan.")

validation = plan.get("validation") or {}
if validation.get("valid"):
    st.success(
        f"Validasi deterministik LULUS: total {rupiah(validation.get('calculated_total'))} tidak melebihi "
        f"budget tersedia {rupiah(validation.get('available_budget'))}; MOQ, pack, dan duplikasi sudah dicek."
    )
else:
    st.error("Validasi deterministik GAGAL: " + "; ".join(validation.get("errors") or [LABEL_NOT_FOUND]))

st.subheader("Reason Trace - Purchaser")
for item in items:
    shown = display_item(item)
    trace = item.get("reason_trace") or {}
    label = f"Rank {shown['rank']} | {shown['sku']} - {shown['item']} | {shown['cabang']} | {shown['keputusan']}"
    with st.expander(label):
        st.markdown(f"**Ringkasan:** {trace.get('summary', LABEL_NOT_FOUND)}")
        evidence = trace.get("evidence") or item.get("evidence") or []
        if evidence:
            st.markdown("**Evidence (nilai + sumber):**")
            st.dataframe(pd.DataFrame(evidence).astype(str), hide_index=True, width="stretch")
        else:
            st.caption(f"Evidence: {LABEL_NOT_FOUND}")
        assumptions = trace.get("assumptions") or []
        if assumptions:
            st.markdown("**Asumsi/kebijakan:**\n" + "\n".join(f"- {text}" for text in assumptions))

st.subheader("Execution Trace - Purchaser")
if logs:
    log_frame = pd.DataFrame(logs)
    columns = [
        column for column in (
            "created_at", "tool_name", "status", "input_summary", "output_summary",
            "error_message", "duration_ms", "attempts",
        ) if column in log_frame.columns
    ]
    st.dataframe(log_frame[columns], hide_index=True, width="stretch")
else:
    st.caption(f"Execution Trace: {LABEL_NOT_FOUND}")
