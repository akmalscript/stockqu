"""Dashboard utama StockQu dengan data Supabase yang dibatasi RLS."""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

from src.config import (
    BUDGET_MONTHLY,
    CRON_TIME_WIB,
    LABEL_NOT_FOUND,
)
from src.services.auth import ALL_USER_ROLES
from src.services.dashboard import load_dashboard_snapshot
from src.ui.components import auto_sync_inventory_on_load
from src.ui.session import page_context

actor, user_client, repository = page_context(ALL_USER_ROLES, "membuka dashboard StockQu")
auto_sync_inventory_on_load(repository, actor, "command_center")
now_wib = datetime.now(ZoneInfo("Asia/Jakarta"))
period = now_wib.strftime("%Y-%m")
snapshot = load_dashboard_snapshot(repository, period)


def rupiah(value) -> str:
    if value is None:
        return LABEL_NOT_FOUND
    try:
        return f"Rp {float(value):,.0f}".replace(",", ".")
    except (TypeError, ValueError):
        return LABEL_NOT_FOUND


def number(value, digits: int = 2) -> str:
    if value is None:
        return LABEL_NOT_FOUND
    try:
        formatted = f"{float(value):,.{digits}f}"
        return formatted.replace(",", "_").replace(".", ",").replace("_", ".")
    except (TypeError, ValueError):
        return LABEL_NOT_FOUND


def percentage(value) -> str:
    if value is None:
        return LABEL_NOT_FOUND
    try:
        amount = float(value)
    except (TypeError, ValueError):
        return LABEL_NOT_FOUND
    amount = amount * 100 if amount <= 1 else amount
    return f"{number(amount)}%"


def local_time(value) -> str:
    if not value:
        return LABEL_NOT_FOUND
    try:
        raw_timestamp = str(value)
        if raw_timestamp.endswith("Z"):
            raw_timestamp = raw_timestamp[:-1] + "+00:00"
        timestamp = datetime.fromisoformat(raw_timestamp)
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=ZoneInfo("Asia/Jakarta"))
        return timestamp.astimezone(ZoneInfo("Asia/Jakarta")).strftime("%d %b %Y %H:%M WIB")
    except ValueError:
        return str(value)


def has_issue(source: str) -> bool:
    return any(issue.startswith(f"{source}:") for issue in snapshot.issues)


def priority_frame(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "Risiko": row.get("risk_level") or LABEL_NOT_FOUND,
                "SKU": row.get("sku") or LABEL_NOT_FOUND,
                "Item": row.get("product_name") or LABEL_NOT_FOUND,
                "Cabang": row.get("branch") or LABEL_NOT_FOUND,
                "Stok": row.get("stock_qty"),
                "Rata-rata/hari": row.get("avg_daily_sales"),
                "Days-to-stockout": row.get("days_to_stockout"),
                "Restock": row.get("restock_qty"),
            }
            for row in rows
        ]
    )


def render_reason_trace(item: dict, label: str) -> None:
    trace = item.get("reason_trace") or {}
    with st.expander(label):
        st.markdown(f"**Ringkasan:** {trace.get('summary') or LABEL_NOT_FOUND}")
        for key, title in (
            ("demand_trend", "Tren permintaan"),
            ("stockout_prediction", "Prediksi stockout"),
            ("restock_recommendation", "Rekomendasi restock"),
        ):
            if trace.get(key):
                st.markdown(f"**{title}:** {trace[key]}")
        evidence = trace.get("evidence") or item.get("evidence") or []
        if evidence:
            st.markdown("**Evidence dan sumber:**")
            st.dataframe(
                pd.DataFrame(evidence).astype(str),
                hide_index=True,
                width="stretch",
            )
        else:
            st.caption(f"Evidence: {LABEL_NOT_FOUND}")
        assumptions = trace.get("assumptions") or []
        if assumptions:
            st.markdown(
                "**Asumsi/kebijakan:**\n"
                + "\n".join(f"- {text}" for text in assumptions)
            )


def nav_link(path: str, label: str, icon: str, width: str = "content") -> None:
    """Tautan hanya untuk halaman yang ada di menu role ini (lihat app.py)."""
    from src.services.auth import PURCHASER_VIEW_ROLES

    if path.endswith(("rencana.py", "perbandingan.py")) and actor.role not in PURCHASER_VIEW_ROLES:
        return
    st.page_link(path, label=label, icon=icon, width=width)


WORKFLOW_STAGES = (
    ("Analyst", "analyst_run_id"), ("Purchaser", "purchaser_run_id"), ("Negotiator", "negotiator_run_id"),
    ("Approval WA", "approval_code"),
)


@st.fragment(run_every="10s")
def workflow_status() -> None:
    """Status agent per tahap dari workflow terakhir (F-31). Refresh tanpa rerun seluruh halaman."""
    from src.services import agent_service_client as agent_service

    try:
        jobs = repository.fetch_workflow_jobs(1)
    except Exception:  # noqa: BLE001 - tabel P4 belum ada
        st.caption(f"Status workflow: {LABEL_NOT_FOUND} (jalankan migration P4).")
        return
    with st.container(border=True):
        job = jobs[0] if jobs else None
        with st.container(horizontal=True, vertical_alignment="center"):
            st.markdown(f"**Workflow terakhir:** `{job['run_id']}` · {job['trigger']} · **{job['status']}** · "
                        f"mulai {local_time(job['started_at'])}" if job else "**Workflow:**")
            if st.button("Jalankan", icon=":material/play_arrow:", width="content",
                         disabled=not agent_service.configured(),
                         help="Memanggil Agent Service; cron otomatis tetap jalan 07:00 WIB"):
                try:
                    res = agent_service.trigger_manual_run(actor.user_id)
                    st.toast(f"Workflow {res['run_id']}: {'sudah berjalan' if res.get('duplicate') else 'dimulai'}")
                except agent_service.AgentServiceUnavailable as exc:
                    st.error(str(exc))
        if not job:
            return
        summary = job.get("summary") or {}
        with st.container(horizontal=True):
            for name, field in WORKFLOW_STAGES:
                ref = job.get(field)
                if ref:
                    st.badge(f"{name}: {ref}", icon=":material/check_circle:", color="green")
                elif job["status"] == "running":
                    st.badge(f"{name}: berjalan", icon=":material/progress_activity:", color="blue")
                else:
                    st.badge(f"{name}: -", color="gray")
            sent = sum(d.get("status") == "sent" for d in summary.get("dispatch", []))
            st.badge(f"Email PO: {sent} terkirim" if summary.get("dispatch") else "Email PO: -",
                     icon=":material/mail:", color="green" if sent else "gray")
        if job.get("error_message"):
            st.error(f"Error: {job['error_message']}")


CARD_LINK_STYLE = """
<style>
/* Card container: lebar penuh */
[data-testid="stMain"] [data-testid="stPageLink"],
[data-testid="stMain"] .stPageLink,
[data-testid="stTabs"] [data-testid="stPageLink"],
[data-testid="stTabPanel"] [data-testid="stPageLink"],
.stTabs [data-testid="stPageLink"],
.stTabs .stPageLink,
div[role="tabpanel"] [data-testid="stPageLink"] {
    width: 100% !important;
    display: block !important;
}

[data-testid="stMain"] [data-testid="stPageLink"] > div,
[data-testid="stMain"] .stPageLink > div,
.stTabs [data-testid="stPageLink"] > div,
.stTabs .stPageLink > div {
    width: 100% !important;
}

/* Tombol Kartu Opsi 4: .v4 + .btn */
[data-testid="stMain"] [data-testid="stPageLink"] a,
[data-testid="stMain"] .stPageLink a,
[data-testid="stTabs"] [data-testid="stPageLink"] a,
[data-testid="stTabPanel"] [data-testid="stPageLink"] a,
.stTabs [data-testid="stPageLink"] a,
.stTabs .stPageLink a,
div[role="tabpanel"] [data-testid="stPageLink"] a {
    width: 100% !important;
    display: flex !important;
    align-items: center !important;
    justify-content: flex-start !important;
    gap: 0.6rem !important;
    font-weight: 600 !important;
    line-height: 1.2 !important;
    padding: 0.55rem 1rem !important;
    border-radius: 8px !important;
    cursor: pointer !important;
    text-decoration: none !important;
    background: #ffffff !important;
    border: 1px solid #cfd8d3 !important;
    box-shadow: 0 1px 3px rgba(30, 42, 59, 0.12) !important;
    box-sizing: border-box !important;
    margin-top: 0.25rem !important;
    margin-bottom: 0.5rem !important;
    color: #1E2A44 !important;
    transition: background 0.15s, box-shadow 0.15s, transform 0.1s, border-color 0.15s !important;
}

/* Hover: .v4:hover */
[data-testid="stMain"] [data-testid="stPageLink"] a:hover,
[data-testid="stMain"] .stPageLink a:hover,
[data-testid="stTabs"] [data-testid="stPageLink"] a:hover,
[data-testid="stTabPanel"] [data-testid="stPageLink"] a:hover,
.stTabs [data-testid="stPageLink"] a:hover,
.stTabs .stPageLink a:hover,
div[role="tabpanel"] [data-testid="stPageLink"] a:hover {
    border-color: #1b7f5c !important;
    box-shadow: 0 2px 6px rgba(27, 127, 92, 0.25) !important;
    background: #ffffff !important;
}

/* Active: .btn:active */
[data-testid="stMain"] [data-testid="stPageLink"] a:active,
[data-testid="stMain"] .stPageLink a:active,
[data-testid="stTabs"] [data-testid="stPageLink"] a:active,
[data-testid="stTabPanel"] [data-testid="stPageLink"] a:active,
.stTabs [data-testid="stPageLink"] a:active,
.stTabs .stPageLink a:active,
div[role="tabpanel"] [data-testid="stPageLink"] a:active {
    transform: translateY(1px) !important;
}

/* Chevron kanan: .v4 .chev */
[data-testid="stMain"] [data-testid="stPageLink"] a::after,
[data-testid="stMain"] .stPageLink a::after,
[data-testid="stTabs"] [data-testid="stPageLink"] a::after,
[data-testid="stTabPanel"] [data-testid="stPageLink"] a::after,
.stTabs [data-testid="stPageLink"] a::after,
.stTabs .stPageLink a::after,
div[role="tabpanel"] [data-testid="stPageLink"] a::after {
    content: "" !important;
    display: inline-block !important;
    margin-left: auto !important;
    width: 18px !important;
    height: 18px !important;
    min-width: 18px !important;
    background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='%231b7f5c' stroke-width='2.5' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpolyline points='9 18 15 12 9 6'%3E%3C/polyline%3E%3C/svg%3E") !important;
    background-repeat: no-repeat !important;
    background-position: center !important;
    background-size: 18px 18px !important;
}

/* Teks label di dalam kartu */
[data-testid="stMain"] [data-testid="stPageLink"] a p,
[data-testid="stMain"] .stPageLink a p,
.stTabs [data-testid="stPageLink"] a p,
.stTabs .stPageLink a p {
    margin: 0 !important;
    padding: 0 !important;
    font-weight: 600 !important;
    color: #1E2A44 !important;
}

/* Judul StockQu di command center: Stock (hitam) dan Qu (hijau tua) */
h1 {
    margin-top: 0 !important;
    padding-top: 0 !important;
}

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
"""


def main() -> None:
    st.title("Stock:green[Qu]")
    st.html(CARD_LINK_STYLE)

    latest_sync = snapshot.sync_logs[0] if snapshot.sync_logs else None
    budget_available = snapshot.budget.get("available_budget") if snapshot.budget else None

    with st.sidebar:
        st.header("Status Sistem")
        st.metric("Budget kebijakan", rupiah(BUDGET_MONTHLY))
        st.metric("Sisa budget aktual", rupiah(budget_available))
        st.metric("Cron", f"Setiap hari {CRON_TIME_WIB} WIB")
        if latest_sync:
            st.caption(
                f"Sinkron Sheets ↔ Supabase: {latest_sync.get('status', LABEL_NOT_FOUND)} · "
                f"{local_time(latest_sync.get('started_at'))}"
            )
        else:
            st.caption(f"Sinkron Sheets ↔ Supabase: {LABEL_NOT_FOUND}")
        st.divider()
        st.info(
            "Human-in-the-loop: tanpa balas OK di WhatsApp, PO tidak dikirim "
            "dan stok tidak diubah."
        )
        nav_link(
            "app_pages/prediksi.py",
            label="Jalankan Agent Analyst",
            icon=":material/trending_down:",
        )
        nav_link(
            "app_pages/rencana.py",
            label="Jalankan Agent Purchaser",
            icon=":material/account_balance_wallet:",
        )

    workflow_status()

    tabs = st.tabs(
        [
            "Overview",
            "Agent Run",
            "Reason Trace",
            "Supplier Comparison",
            "Approval",
            "Enterprise Report",
        ]
    )

    with tabs[0]:
        risk_rows = [
            row
            for row in snapshot.forecasts
            if row.get("risk_level") in {"URGENT", "RISIKO"}
        ]
        urgent_count = sum(row.get("risk_level") == "URGENT" for row in risk_rows)
        risk_metric = f"{len(risk_rows)} item" if snapshot.forecasts else LABEL_NOT_FOUND
        urgent_metric = f"{urgent_count} item" if snapshot.forecasts else LABEL_NOT_FOUND
        c1, c2, c3 = st.columns(3)
        c1.metric("Item berisiko (≤ 7 hari)", risk_metric)
        c2.metric("Item URGENT (≤ 4 hari)", urgent_metric)
        c3.metric("Sisa budget aktual", rupiah(budget_available))

        st.subheader("5 Item Prioritas")

        if risk_rows:
            st.dataframe(
                priority_frame(risk_rows[:5]),
                hide_index=True,
                width="stretch",
            )
            st.caption(
                f"Run Analyst: `{snapshot.analyst_run_id}` · stok disinkronkan real-time dengan Google Sheets · "
                "diurutkan berdasarkan days-to-stockout terkecil, per SKU-cabang."
            )
        elif snapshot.forecasts:
            st.success("Tidak ada SKU-cabang berisiko habis dalam 7 hari pada run Analyst terakhir.")
        elif snapshot.inventory:
            st.warning(
                f"{LABEL_NOT_FOUND} - belum ada hasil Analyst. Stok nyata tersedia, "
                "tetapi rata-rata penjualan dan days-to-stockout belum dihitung."
            )
            stock_rows = pd.DataFrame(
                [
                    {
                        "SKU": row.get("sku") or LABEL_NOT_FOUND,
                        "Item": row.get("product_name") or LABEL_NOT_FOUND,
                        "Cabang": row.get("branch") or LABEL_NOT_FOUND,
                        "Stok": row.get("stock_qty"),
                        "Diperbarui": local_time(row.get("updated_at")),
                    }
                    for row in snapshot.inventory
                ]
            )
            st.dataframe(stock_rows, hide_index=True, width="stretch")
        else:
            st.warning(
                f"{LABEL_NOT_FOUND} - inventory dan hasil Analyst belum tersedia "
                "untuk akses user ini."
            )

        nav_link(
            "app_pages/prediksi.py",
            label="Buka detail Prediksi & Risiko",
            icon=":material/trending_down:",
            width="stretch",
        )
        nav_link(
            "app_pages/rencana.py",
            label="Buka detail Rencana Pembelian",
            icon=":material/account_balance_wallet:",
            width="stretch",
        )

    with tabs[1]:
        st.subheader("Execution Trace - Agent Terakhir")
        st.write("**Analyst:** histori 6 bulan, forecast, dan deteksi stockout per cabang.")
        st.write("**Purchaser:** prioritas dan alokasi dengan plafon budget Rp 50.000.000.")
        st.write("**Negotiator:** memerlukan minimal 3 penawaran supplier nyata per item.")
        if snapshot.agent_logs:
            log_frame = pd.DataFrame(snapshot.agent_logs)
            columns = [
                column
                for column in (
                    "created_at",
                    "agent_name",
                    "tool_name",
                    "status",
                    "input_summary",
                    "output_summary",
                    "error_message",
                )
                if column in log_frame.columns
            ]
            st.dataframe(
                log_frame[columns],
                hide_index=True,
                width="stretch",
            )
            st.caption(
                f"Run: `{snapshot.purchaser_run_id or snapshot.analyst_run_id}` · "
                "sumber: Supabase agent_logs"
            )
        else:
            st.info(f"Execution Trace: {LABEL_NOT_FOUND}")

    with tabs[2]:
        st.subheader("Reason Trace - Angka dan Sumber Nyata")
        if snapshot.plan_items:
            st.caption(f"Sumber: Purchaser run `{snapshot.purchaser_run_id}`")
            for item in snapshot.plan_items[:20]:
                label = (
                    f"Rank {item.get('rank', '-')} · {item.get('sku') or LABEL_NOT_FOUND} · "
                    f"{item.get('branch') or LABEL_NOT_FOUND} · "
                    f"{item.get('decision') or LABEL_NOT_FOUND}"
                )
                render_reason_trace(item, label)
        elif snapshot.forecasts:
            st.caption(f"Sumber: Analyst run `{snapshot.analyst_run_id}`")
            for item in snapshot.forecasts[:20]:
                label = (
                    f"{item.get('risk_level') or LABEL_NOT_FOUND} · "
                    f"{item.get('sku') or LABEL_NOT_FOUND} · "
                    f"{item.get('branch') or LABEL_NOT_FOUND}"
                )
                render_reason_trace(item, label)
        else:
            st.info(f"Reason Trace: {LABEL_NOT_FOUND}")

    with tabs[3]:
        st.subheader("Perbandingan Supplier dari Kontrak Supabase")
        if snapshot.contracts:
            products = {}
            for contract in snapshot.contracts:
                product_id = str(contract.get("product_id"))
                products.setdefault(
                    product_id,
                    f"{contract.get('sku') or LABEL_NOT_FOUND} - "
                    f"{contract.get('product_name') or LABEL_NOT_FOUND}",
                )
            label_to_id = {label: product_id for product_id, label in products.items()}
            selected_label = st.selectbox("Pilih item", sorted(label_to_id))
            selected_id = label_to_id[selected_label]
            contracts = [
                row
                for row in snapshot.contracts
                if str(row.get("product_id")) == selected_id
            ]
            supplier_rows = pd.DataFrame(
                [
                    {
                        "Supplier": row.get("supplier_name") or LABEL_NOT_FOUND,
                        "Harga": rupiah(row.get("price")),
                        "Lead time": (
                            f"{row['supplier_lead_time_days']} hari"
                            if row.get("supplier_lead_time_days") is not None
                            else LABEL_NOT_FOUND
                        ),
                        "Diskon": percentage(row.get("discount")),
                        "Performa": number(row.get("performance_score")),
                        "MOQ": row.get("moq", LABEL_NOT_FOUND),
                        "Pack": row.get("pack_size", LABEL_NOT_FOUND),
                        "Valid sampai": row.get("valid_until") or LABEL_NOT_FOUND,
                    }
                    for row in contracts
                ]
            )
            st.dataframe(supplier_rows, hide_index=True, width="stretch")
            unique_suppliers = len(
                {row.get("supplier_id") for row in contracts if row.get("supplier_id")}
            )
            if unique_suppliers < 3:
                st.warning(
                    f"Baru ada {unique_suppliers} supplier nyata untuk item ini; "
                    "syarat minimal 3 supplier belum terpenuhi."
                )
            else:
                st.success(f"{unique_suppliers} supplier nyata tersedia untuk dibandingkan.")
        elif has_issue("contracts"):
            st.error(f"Kontrak supplier: {LABEL_NOT_FOUND} - Supabase tidak dapat dibaca.")
        else:
            st.info(f"Kontrak supplier: {LABEL_NOT_FOUND}")

    with tabs[4]:
        st.subheader("Approval WhatsApp - Human-in-the-Loop")
        pending = [
            row
            for row in snapshot.purchase_orders
            if str(row.get("status") or "").lower()
            in {"waiting_approval", "pending_approval"}
        ]
        if pending:
            supplier_map = {
                str(row.get("id")): row.get("name") for row in snapshot.suppliers
            }
            approval_rows = pd.DataFrame(
                [
                    {
                        "PO": row.get("id"),
                        "Supplier": supplier_map.get(
                            str(row.get("supplier_id")),
                            LABEL_NOT_FOUND,
                        ),
                        "Total": rupiah(row.get("total_amount")),
                        "Status": row.get("status"),
                        "Dibuat": local_time(row.get("created_at")),
                    }
                    for row in pending
                ]
            )
            st.dataframe(approval_rows, hide_index=True, width="stretch")
            st.metric(
                "Total menunggu approval",
                rupiah(sum(float(row.get("total_amount") or 0) for row in pending)),
            )
            st.warning(
                "PO tetap tertahan. Pengiriman Email dan update stok hanya boleh berjalan "
                "setelah manager membalas OK di WhatsApp."
            )
        elif has_issue("purchase_orders"):
            st.error(f"Status approval: {LABEL_NOT_FOUND} - Supabase tidak dapat dibaca.")
        else:
            st.info("Tidak ada PO yang menunggu approval pada periode berjalan.")

    with tabs[5]:
        st.subheader("Enterprise Report - Dampak Plan Terakhir")
        impact = (snapshot.procurement_plan or {}).get("impact") or {}
        if impact:
            e1, e2, e3 = st.columns(3)
            e1.metric("Item dipilih", impact.get("selected_items", LABEL_NOT_FOUND))
            e2.metric(
                "URGENT tercakup",
                impact.get("urgent_items_covered", LABEL_NOT_FOUND),
            )
            coverage = impact.get("additional_coverage_days")
            e3.metric(
                "Tambahan coverage",
                f"{number(coverage)} hari" if coverage is not None else LABEL_NOT_FOUND,
            )
            st.metric(
                "Estimasi margin terlindungi",
                rupiah(impact.get("estimated_margin_protected")),
            )
            st.caption(
                f"Sumber: procurement plan `{snapshot.purchaser_run_id}` · "
                f"basis: {impact.get('impact_basis') or LABEL_NOT_FOUND}"
            )
        else:
            st.info(f"Dampak enterprise plan: {LABEL_NOT_FOUND}")
        nav_link(
            "app_pages/rencana.py",
            label="Buka rencana pembelian dan laporan lengkap",
            icon=":material/account_balance_wallet:",
            width="stretch",
        )

    if snapshot.issues:
        st.divider()
        unavailable = sorted({issue.split(":", 1)[0] for issue in snapshot.issues})
        st.caption(
            "Sebagian sumber belum tersedia atau tidak diizinkan oleh RLS: "
            + ", ".join(unavailable)
        )
    st.caption(
        f"Dashboard dibaca {now_wib.strftime('%d %b %Y %H:%M WIB')} · "
        "angka berasal dari Supabase/hasil sinkron Google Sheets; data kosong tidak direka."
    )


main()
