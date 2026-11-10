"""Inventori multi-gudang (F-02, F-03, SRS-F-02/03/07).

Stok per SKU per cabang - TIDAK PERNAH dijumlahkan antar cabang. Stok fisik (on-hand)
dan barang dalam perjalanan (incoming dari PO terkirim) ditampilkan terpisah.
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

from src.config import LABEL_NOT_FOUND
from src.services.auth import ALL_USER_ROLES
from src.ui.components import (
    auto_sync_inventory_on_load,
    dummy_banner,
    local_time,
    trigger_sheets_sync_ui,
)
from src.ui.session import page_context
from src.utils import formulas as F
from src.utils.errors import safe_error

actor, user_client, repository = page_context(ALL_USER_ROLES, "melihat inventori")
auto_sync_inventory_on_load(repository, actor, "inventori")


def load(_repo, user_key: str) -> dict:
    """Muat data inventori terkini per user (RLS bisa berbeda per role/cabang)."""
    out = {"products": _repo.fetch_products(), "branches": _repo.fetch_branches(),
           "contracts": _repo.fetch_reference_contracts(), "suppliers": _repo.fetch_suppliers(), "issues": []}
    try:
        out["inventory"] = _repo.fetch_inventory_detailed()
    except Exception as exc:  # noqa: BLE001 - kolom incoming_qty baru ada setelah migration P4
        out["issues"].append(f"incoming_qty: {safe_error(exc)}")
        out["inventory"] = _repo.fetch_inventory()
    run = _repo.latest_analyst_run_id()
    out["forecasts"] = _repo.fetch_forecasts(run) if run else []
    out["analyst_run"] = run
    return out


col_title, col_actions = st.columns([3, 2])
with col_title:
    st.title("Inventori multi-gudang")
    st.caption("Stok per cabang, terpisah. Sumber: Supabase inventory (hasil sinkron Google Sheets) + forecast Analyst terakhir.")
with col_actions:
    st.write("")
    btn_col1, btn_col2 = st.columns(2)
    with btn_col1:
        sync_clicked = st.button("🔄 Sinkron Sheets", type="primary", use_container_width=True,
                                 help="Tarik perubahan stok dari Google Sheets ke Supabase dan sebaliknya.")
    with btn_col2:
        refresh_clicked = st.button("🔃 Refresh", use_container_width=True,
                                    help="Muat ulang data inventori dan selaraskan dengan Sheets.")

if refresh_clicked:
    st.session_state.pop("_last_auto_sync_inventori", None)
    auto_sync_inventory_on_load(repository, actor, "inventori", min_interval_seconds=0)
    st.rerun()

if sync_clicked:
    if trigger_sheets_sync_ui(repository, actor):
        st.rerun()

try:
    data = load(repository, actor.user_id)
except Exception as exc:  # noqa: BLE001
    st.error(f"{LABEL_NOT_FOUND} - Supabase tidak bisa dibaca ({safe_error(exc)}).")
    st.stop()

products = {str(p["id"]): p for p in data["products"]}
branches = {str(b["id"]): b["name"] for b in data["branches"]}
suppliers = {str(s["id"]): s["name"] for s in data["suppliers"]}
risk = {(str(f["product_id"]), str(f["branch_id"])): f for f in data["forecasts"]}
supplier_count: dict[str, set] = {}
for c in data["contracts"]:
    supplier_count.setdefault(str(c["product_id"]), set()).add(suppliers.get(str(c["supplier_id"]), "?"))

rows = []
for inv in data["inventory"]:
    pid, bid = str(inv["product_id"]), str(inv["branch_id"])
    p, f = products.get(pid, {}), risk.get((pid, str(inv["branch_id"])), {})
    current_stock = inv.get("stock_qty")
    avg_daily = f.get("avg_daily_sales")
    lead_time = p.get("lead_time_days")

    if current_stock is not None and avg_daily is not None and float(avg_daily) > 0:
        dts = round(F.days_to_stockout(float(current_stock), float(avg_daily)), 1)
        risk_lvl = F.classify_risk(dts, int(lead_time) if lead_time is not None else None)
    elif f.get("days_to_stockout") is not None:
        dts = f.get("days_to_stockout")
        risk_lvl = f.get("risk_level") or LABEL_NOT_FOUND
    else:
        dts = None
        risk_lvl = LABEL_NOT_FOUND

    rows.append({
        "Risiko": risk_lvl,
        "SKU": p.get("sku", LABEL_NOT_FOUND),
        "Nama": p.get("name", LABEL_NOT_FOUND),
        "Kategori": p.get("category") or "-",
        "Cabang": branches.get(bid, LABEL_NOT_FOUND),
        "Stok fisik": current_stock,
        "In-transit": inv.get("incoming_qty"),
        "Days-to-stockout": dts,
        "Lead time": lead_time,
        "Margin %": p.get("margin"),
        "Turnover": p.get("turnover_rate"),
        "Supplier kontrak": ", ".join(sorted(supplier_count.get(pid, []))) or LABEL_NOT_FOUND,
        "Update": local_time(inv.get("updated_at")),
    })
df = pd.DataFrame(rows)

with st.container(horizontal=True):
    branch_pick = st.segmented_control("Cabang", ["Semua", *sorted(branches.values())], default="Semua")
    risk_pick = st.pills("Risiko", ["URGENT", "RISIKO", "AMAN", LABEL_NOT_FOUND], selection_mode="multi")
    query = st.text_input("Cari SKU / nama", placeholder="SKU-A")

view = df
if branch_pick and branch_pick != "Semua":
    view = view[view["Cabang"] == branch_pick]
if risk_pick:
    view = view[view["Risiko"].isin(risk_pick)]
if query:
    q = query.strip().lower()
    view = view[view["SKU"].str.lower().str.contains(q, regex=False) | view["Nama"].str.lower().str.contains(q, regex=False)]

with st.container(horizontal=True):
    for name in sorted(branches.values()):
        sub = df[df["Cabang"] == name]
        with st.container(border=True):
            st.metric(name, f"{len(sub)} SKU")
            st.caption(f"{int((sub['Risiko'] == 'URGENT').sum())} URGENT · {int((sub['Risiko'] == 'RISIKO').sum())} RISIKO")

st.info(
    "💡 **Tips:** Google Sheets adalah sumber stok real-time. Jika Anda mengedit stok di Google Sheets (seperti mengubah SKU-A Branch 1 menjadi 50), "
    "klik tombol **🔄 Sinkron Sheets** di atas untuk menyinkronkan data langsung ke web tanpa menunggu jadwal otomatis jam 07:00 WIB."
)
dummy_banner(repository)
st.dataframe(
    view.sort_values(["Cabang", "Days-to-stockout"], na_position="last"), hide_index=True, width="stretch",
    column_config={
        "Stok fisik": st.column_config.NumberColumn(format="%d"),
        "In-transit": st.column_config.NumberColumn(help="Barang PO terkirim, belum diterima gudang"),
        "Days-to-stockout": st.column_config.NumberColumn(format="%.1f"),
        "Margin %": st.column_config.NumberColumn(format="%.1f"),
    },
)
st.caption(f"{len(view)} baris · forecast dari run Analyst `{data['analyst_run'] or LABEL_NOT_FOUND}` · "
           "stok antar cabang tidak dijumlahkan.")
for issue in data["issues"]:
    st.caption(f"Catatan data: {issue} (jalankan migration P4 untuk kolom in-transit).")
