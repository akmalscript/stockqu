import os
import pandas as pd
import streamlit as st
from datetime import datetime

# StockQu - Streamlit Dashboard Skeleton
# Stack: Streamlit (frontend) + Supabase (backend) + LangGraph (agent) + n8n (otomasi)
# Aturan bisnis ada di .agents/skills/stockqu-domain/SKILL.md dan AGENTS.md
# JANGAN mengarang data. Jika data tidak ada, tulis DATA TIDAK DITEMUKAN.
# Jika ini data dummy, wajib tulis label: CONTOH SIMULASI - BUKAN DATA REAL

st.set_page_config(
    page_title="StockQu - Multi-Agent Procurement",
    page_icon="📦",
    layout="wide",
)

# --- Config & rumus StockQu: satu sumber di src/config.py dan src/utils/formulas.py ---
from src.config import BUDGET_MONTHLY
from src.config import RISK_MAX_DAYS as STOCKOUT_THRESHOLD_DAYS
from src.config import URGENT_MAX_DAYS as URGENT_THRESHOLD_DAYS
from src.utils.formulas import days_to_stockout, kebutuhan_restock


def load_dummy_data() -> pd.DataFrame:
    """Data dummy HANYA untuk skeleton. Ganti dengan baca Supabase / Sheets."""
    return pd.DataFrame(
        [
            {"sku": "Item A", "branch": "Branch 1", "stock": 100, "avg_daily_sales": 50, "lead_time_days": 2, "margin": 0.18, "turnover": 4.2, "harga_satuan": 36000},
            {"sku": "Item B", "branch": "Branch 1", "stock": 90, "avg_daily_sales": 30, "lead_time_days": 3, "margin": 0.22, "turnover": 3.8, "harga_satuan": 50000},
            {"sku": "Item C", "branch": "Branch 2", "stock": 80, "avg_daily_sales": 20, "lead_time_days": 2, "margin": 0.25, "turnover": 3.1, "harga_satuan": 50000},
            {"sku": "Item D", "branch": "Branch 2", "stock": 100, "avg_daily_sales": 20, "lead_time_days": 3, "margin": 0.30, "turnover": 2.5, "harga_satuan": 41428},
            {"sku": "Item E", "branch": "Branch 3", "stock": 120, "avg_daily_sales": 20, "lead_time_days": 4, "margin": 0.12, "turnover": 1.8, "harga_satuan": 55000},
        ]
    )


def main():
    st.title("📦 StockQu - Multi-Agent Procurement System")
    st.caption("Streamlit + Supabase + LangGraph + n8n | GTNIC Enterprise dan Industry")

    # Sidebar - Status Sistem
    with st.sidebar:
        st.header("Status Sistem")
        st.metric("Budget Bulanan", f"Rp {BUDGET_MONTHLY:,}")
        st.metric("Sisa Budget (Contoh)", "Rp 12.000.000")
        st.metric("Cron", "Setiap hari jam 7 pagi")
        st.divider()
        st.info(
            "Human-in-the-loop: Tanpa balas OK di WhatsApp, "
            "PO tidak dikirim dan stok tidak diubah."
        )
        if st.button("🔄 Jalankan Agent (Skeleton)", use_container_width=True):
            st.session_state["agent_run"] = True
            st.toast("Agent dijalankan. Hubungkan ke LangGraph di src/agents/graph.py")

    # Tabs utama sesuai F-31 sampai F-35
    tab_overview, tab_agent, tab_reason, tab_supplier, tab_approval, tab_report = st.tabs(
        [
            "📊 Overview",
            "🤖 Agent Run",
            "🧠 Reason Trace",
            "🏷️ Supplier Comparison",
            "✅ Approval",
            "📈 Enterprise Report",
        ]
    )

    df = load_dummy_data()
    df["days_to_stockout"] = df.apply(
        lambda r: days_to_stockout(r["stock"], r["avg_daily_sales"]), axis=1
    )
    df["kebutuhan_restock"] = df.apply(
        lambda r: kebutuhan_restock(r["avg_daily_sales"], r["lead_time_days"], r["stock"]), axis=1
    )
    df = df.sort_values("days_to_stockout")

    with tab_overview:
        st.warning("CONTOH SIMULASI - BUKAN DATA REAL. Ganti dengan baca Supabase / Google Sheets.")
        st.page_link("pages/1_Prediksi_dan_Risiko.py", label="Buka hasil Agent Analyst (Prediksi & Risiko, data Supabase)", icon="📈")
        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Item Berisiko Habis (4-7 hari)", f"{len(df[df['days_to_stockout'] <= STOCKOUT_THRESHOLD_DAYS])} item")
        col2.metric("Item URGENT (<= 4 hari)", f"{len(df[df['days_to_stockout'] <= URGENT_THRESHOLD_DAYS])} item")
        col3.metric("Sisa Budget", "Rp 12.000.000")
        col4.metric("Siklus Draft PO", "15 menit (dari 2 hari)")

        st.subheader("5 Item Prioritas")
        st.dataframe(
            df[["sku", "branch", "stock", "avg_daily_sales", "days_to_stockout", "kebutuhan_restock"]],
            use_container_width=True,
            hide_index=True,
        )
        st.caption("Urutkan berdasarkan days-to-stockout terkecil dulu, bukan margin terbesar.")

    with tab_agent:
        st.subheader("Agent Run - Analyst, Purchaser, Negotiator")
        st.write("**Analyst:** Hitung days-to-stockout dari histori 6 bulan.")
        st.write("**Purchaser:** Prioritaskan dengan budget Rp 50.000.000.")
        st.write("**Negotiator:** Bandingkan minimal 3 supplier per item.")
        if st.session_state.get("agent_run"):
            st.success("Agent selesai (skeleton). Execution Trace akan muncul di sini setelah LangGraph dihubungkan.")
            st.code(
                "2026-11-07 07:00:00 | Baca Sheets | OK\n"
                "2026-11-07 07:01:12 | Hitung days-to-stockout | OK\n"
                "2026-11-07 07:02:30 | Bandingkan 3 supplier | OK\n"
                "2026-11-07 07:03:45 | Buat draft PO | OK",
                language="text",
            )
        else:
            st.info("Klik tombol Jalankan Agent di sidebar untuk simulasi.")

    with tab_reason:
        st.subheader("Reason Trace Panel - Wajib Ada Angka Real")
        st.code(
            "[REASONING TRACE - THE PURCHASER]\n"
            "Constraint: Sisa budget Rp 12.000.000 (dari plafon Rp 50.000.000).\n\n"
            "- PRIORITAS 1 (Item A): Days-to-stockout 2 hari (URGENT). Alokasi Rp 3.600.000 disetujui.\n"
            "- PRIORITAS 2 (Item B): Days-to-stockout 3 hari, margin 22%, turnover 3.8x. Alokasi Rp 3.000.000 disetujui.\n"
            "- DITUNDA (Item E): Ditunda ke minggu depan karena sisa budget habis dan risiko terendah (6 hari).",
            language="text",
        )
        st.caption("Aturan: Jangan tulis kalimat generik seperti 'karena penting'. Harus ada angka dan sumbernya.")

    with tab_supplier:
        st.subheader("Perbandingan Supplier - Minimal 3 Supplier")
        st.info("DATA TIDAK DITEMUKAN - Hubungkan ke RAG kontrak lama dan API / scraping supplier.")
        supplier_df = pd.DataFrame(
            [
                {"supplier": "Supplier A", "harga": "DATA TIDAK DITEMUKAN", "lead_time": "DATA TIDAK DITEMUKAN", "diskon": "DATA TIDAK DITEMUKAN", "performa": "DATA TIDAK DITEMUKAN"},
                {"supplier": "Supplier B", "harga": "DATA TIDAK DITEMUKAN", "lead_time": "DATA TIDAK DITEMUKAN", "diskon": "DATA TIDAK DITEMUKAN", "performa": "DATA TIDAK DITEMUKAN"},
                {"supplier": "Supplier C", "harga": "DATA TIDAK DITEMUKAN", "lead_time": "DATA TIDAK DITEMUKAN", "diskon": "DATA TIDAK DITEMUKAN", "performa": "DATA TIDAK DITEMUKAN"},
            ]
        )
        st.dataframe(supplier_df, use_container_width=True, hide_index=True)

    with tab_approval:
        st.subheader("Approval WhatsApp - Human-in-the-Loop")
        st.code(
            "Pak, butuh approval Rp 12jt untuk 4 item (1 item ditunda budget), "
            "paling urgent Item A (habis 2 hari). Balas OK untuk kirim PO.",
            language="text",
        )
        col_a, col_b = st.columns(2)
        if col_a.button("✅ Simulasi Balas OK", use_container_width=True):
            st.success("OK diterima. PO dikirim via Email dan stok di-update di Sheets + Supabase. (Skeleton - hubungkan ke n8n)")
        if col_b.button("❌ Simulasi Tolak", use_container_width=True):
            st.error("PO ditolak. Tidak ada pengiriman dan tidak ada update stok.")

    with tab_report:
        st.subheader("Enterprise Report")
        col1, col2, col3 = st.columns(3)
        col1.metric("Pengurangan Stockout", "80%")
        col2.metric("Hemat Waktu Cek Stok", "15 jam / minggu")
        col3.metric("Pengurangan Dead Stock", "30%")
        st.divider()
        col_e1, col_e2 = st.columns(2)
        if col_e1.button("📄 Export PDF (Skeleton)", use_container_width=True):
            st.info("Hubungkan ke document-skills (pdf) untuk export PO dan laporan.")
        if col_e2.button("📊 Export Excel (Skeleton)", use_container_width=True):
            st.info("Hubungkan ke document-skills (xlsx) untuk export perbandingan supplier.")

    st.divider()
    st.caption(f"Terakhir di-update: {datetime.now().strftime('%d %b %Y %H:%M')} | StockQu - Jangan mengarang harga, diskon, atau stok.")


if __name__ == "__main__":
    main()
