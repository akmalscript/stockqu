# 📦 StockQu - Multi-Agent Procurement System

AI Agent otonom untuk distributor FMCG dengan 3 cabang gudang di Jawa Tengah.
Bukan dashboard BI biasa. StockQu memprediksi stok akan habis, memutuskan pembelian dengan budget terbatas, membandingkan 3 supplier, membuat draft PO, meminta approval via WhatsApp, dan mengupdate stok otomatis.

Target: **GTNIC Enterprise dan Industry**
Demo: Deteksi sampai draft PO dalam **15 menit** (dari proses manual 2 hari).

## Stack

- **Frontend:** Streamlit saja, bukan fullstack React / Next.js. Fokus ke AI agent.
- **Backend:** Supabase (Postgres, Auth, RLS)
- **Agent:** LangGraph (Python) - Analyst, Purchaser, Negotiator
- **Otomasi:** n8n (cron jam 7 pagi, WhatsApp approval, Email PO, update stok)
- **Data:** Google Sheets sebagai sumber stok real-time, sinkron dua arah dengan Supabase
- **Export:** PDF dan Excel untuk PO dan perbandingan supplier

## Aturan Bisnis Inti

- Budget bulanan Purchaser: **Rp 50.000.000**
- Deteksi stockout: barang berisiko habis dalam **4 sampai 7 hari**
- Minimal supplier dibandingkan: **3 supplier** per item
- Histori prediksi: **6 bulan** terakhir
- Human-in-the-loop: Tanpa balas **OK** di WhatsApp, PO tidak dikirim dan stok tidak diubah

Rumus:

- `days-to-stockout = stok_saat_ini / rata_rata_penjualan_harian`
- `rata_rata_penjualan_harian = total_penjualan_6_bulan / 180`
- `kebutuhan_restock = (rata_rata_penjualan_harian x (lead_time_hari + safety_hari)) - stok_saat_ini`

## Cara Install

```bash
# 1. Clone dan masuk folder
cd stockqu

# 2. Buat virtual env (disarankan)
python -m venv .venv
# Windows:
.venv\Scripts\activate
# Mac / Linux:
# source .venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Copy env template dan isi nilai aslinya
# Windows PowerShell:
copy .env.example .env
# Mac / Linux:
# cp .env.example .env
```

Jangan pernah commit file `.env` atau `.streamlit/secrets.toml`.

## Cara Menjalankan

```bash
streamlit run app.py
```

Buka di browser yang muncul, biasanya `http://localhost:8501`.

## P1 - Data & Analyst

### 1. Database

Jalankan di Supabase SQL Editor, berurutan:

1. `supabase/migrations/002_p1_analyst.sql` - tabel `sales_history`, `sync_logs`, `analyst_forecasts` + RLS
2. `supabase/seed/002_sales_history_dummy.sql` - histori penjualan 180 hari (DUMMY, deterministik, 27.000 baris).
   Konsisten dengan seed P0: 5 item kritis (Item A-E di Branch 1).

Isi `SUPABASE_SERVICE_ROLE_KEY` di `.env` supaya Agent bisa menyimpan hasil (`analyst_forecasts`, `agent_logs`, `sync_logs`).

### 2. Google Sheets (opsional, sinkron dua arah)

1. Buat Service Account di Google Cloud, aktifkan Google Sheets API, download JSON key (simpan di luar repo).
2. Buat spreadsheet, share ke email service account sebagai **Editor**.
3. Isi `GOOGLE_SHEETS_SPREADSHEET_ID` dan `GOOGLE_SERVICE_ACCOUNT_FILE` (atau `GOOGLE_SERVICE_ACCOUNT_JSON` untuk Streamlit Cloud).
4. Tab `Stok` dibuat otomatis saat run pertama, kolom: `sku | product_name | branch | stock_qty | updated_at | source`.
   Edit stok langsung di Sheets; run berikutnya menyinkronkan ke Supabase (yang `updated_at`-nya lebih baru menang,
   baris tanpa `updated_at` dianggap edit manual terbaru). Baris invalid ditolak dan dicatat di `sync_logs`.

Jika Sheets belum dikonfigurasi atau gagal dibaca (retry 3x), Analyst otomatis fallback ke stok Supabase dan mencatatnya di Execution Trace.

### 3. Jalankan Analyst

```bash
python -m scripts.run_analyst
```

Atau dari halaman **Prediksi & Risiko** di Streamlit (tombol di sidebar). Tes:

```bash
python -m pytest
```

### Arsitektur Analyst (LangGraph)

`sync_stock -> load_history -> forecast -> detect_risk -> explain -> persist`

- Rumus resmi ada di satu tempat: `src/utils/formulas.py`.
- Forecast (`src/tools/forecasting.py`): tren linear 8 minggu x musiman mingguan, dibandingkan dengan baseline rata-rata 180 hari lewat backtest 14 hari. Status risiko tetap memakai rumus resmi; forecast jadi evidence + peringatan dini + rentang ketidakpastian.
- Setiap angka di Reason Trace menyebut sumbernya (Sheets / Supabase / Analyst).

## Git

Ikuti `Git Commit Message Conventions` & `Git Branching Workflow` di `AGENTS.md`. Jangan commit langsung ke `main` kecuali initial commit.

Format: `<type>: <deskripsi imperative lowercase>` tanpa scope.
Contoh: `feat: add analyst forecast for days-to-stockout`

Jangan pernah commit: `.env`, `.venv/`, `.streamlit/secrets.toml`

## Struktur Folder

```
stockqu/
  AGENTS.md
  app.py
  pages/         # Halaman Streamlit (1_Prediksi_dan_Risiko.py)
  scripts/       # CLI: run_analyst.py
  tests/         # pytest (tanpa jaringan)
  requirements.txt
  .env.example
  .gitignore
  src/
    agents/      # Analyst, Purchaser, Negotiator (LangGraph)
    tools/       # Tool Use nyata: Sheets, Supplier, Notify, Database
    services/    # Supabase client, Sheets client, budget
    utils/       # Rumus days-to-stockout, formatting, logger
  supabase/
    migrations/  # SQL skema + RLS
  n8n/           # File JSON workflow n8n
  data/          # Data dummy untuk demo (beri label DUMMY)
  docs/          # Proposal dan diagram
```

## Alur Demo Grand Final

1. Jam 7 pagi cron jalan otomatis ala Hermes
2. Agent cek stok di Supabase dan Google Sheets untuk 3 cabang
3. Terdeteksi barang berisiko habis dalam 4 sampai 7 hari
4. Prediksi kebutuhan: butuh 100 unit dalam 4 hari, dengan safety stock
5. Purchaser alokasikan budget dari total Rp 50.000.000 dan prioritaskan 5 item
6. Negotiator bandingkan 3 supplier dan buat matriks harga, lead time, diskon, performa
7. Dibuat 3 draft PO plus perbandingan harga dan draft email negosiasi
8. Kirim notif WA ke manager: Pak, butuh approval Rp 12jt untuk 5 item, paling urgent Item X
9. Manager balas OK di WhatsApp. Sistem kirim PO via Email otomatis
10. Stok otomatis ter-update di Google Sheets dan Supabase

## Aturan Anti-Halusinasi

- Jika data tidak ada, tulis `DATA TIDAK DITEMUKAN`. Jangan mengarang harga, diskon, atau stok.
- Jika user tidak kasih nama item, pakai Item A, Item B, dst dan tulis di atas tabel: `CONTOH SIMULASI - BUKAN DATA REAL`.
- Jangan pakai nama kota dari lokasi user sebagai nama cabang. Pakai Branch 1, Branch 2, Branch 3 kecuali user kasih nama resmi.
- Setiap angka di Reasoning Trace harus bisa ditelusur sumbernya: Sheets, Supabase, hasil Analyst, atau kontrak supplier.

Aturan lengkap ada di `AGENTS.md` dan `.agents/skills/stockqu-domain/SKILL.md`.

## Metrik Enterprise

- Pengurangan stockout: 80%
- Hemat waktu cek stok manual: 15 jam per minggu
- Pengurangan dead stock: 30%
- Siklus pengadaan: dari 2 hari menjadi 15 menit untuk draft PO
