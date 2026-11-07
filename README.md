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
