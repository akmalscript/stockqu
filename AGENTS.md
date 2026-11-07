# AGENTS.md - StockQu

## Project Overview

StockQu adalah AI Agent otonom untuk distributor FMCG dengan 3 cabang gudang di Jawa Tengah.
Bukan dashboard BI biasa. StockQu memprediksi stok akan habis, memutuskan pembelian dengan budget terbatas, membandingkan 3 supplier, membuat draft PO, meminta approval via WhatsApp, dan mengupdate stok otomatis.

Target: GTNIC Enterprise dan Industry. Demo end-to-end: deteksi sampai draft PO dalam 15 menit.

## Tech Stack - Wajib Dipakai

- Frontend: Streamlit saja, bukan fullstack React / Next.js. Fokus utama adalah AI agent, bukan UI.
- Backend: Supabase (Postgres, Auth, RLS, Storage)
- Orkestrasi Agent: LangGraph (Python) - Analyst, Purchaser, Negotiator
- Otomasi: n8n (cron jam 7 pagi, WhatsApp approval, Email PO, update stok)
- Data: Google Sheets sebagai sumber stok real-time, sinkron dua arah dengan Supabase
- Export: PDF dan Excel untuk PO dan perbandingan supplier

## Desain & Tema - Wajib Dipakai

- Palet StockQu: Primary teal #0E7C61, Navy #1E2A44, Background putih #FFFFFF, Secondary mint #EFF4F1. Jangan pakai warna lain untuk komponen utama.
- File tema yang dipakai Streamlit: `.streamlit/config.toml` harus pakai palet yang sama persis.
- Jangan ambil palet lain dari `ui-ux-pro-max`. Skill itu hanya untuk pola kartu KPI, spacing, dan tipografi.

## Skills yang Aktif

Antigravity sudah punya skill di `.agents/skills/`. Pakai skill ini sesuai tugas:

- `stockqu-domain` - WAJIB dibaca dulu untuk semua task StockQu. Isinya aturan bisnis, rumus, dan larangan.
- `supabase` + `supabase-postgres-best-practices` - untuk skema, RLS, Auth
- `langgraph-patterns` - untuk orkestrasi 3 agent
- `workflow-architect` - untuk workflow n8n
- `streamlit-expert` - untuk struktur dan komponen Streamlit
- `ui-ux-pro-max` - untuk tipografi, spacing, pola kartu KPI. Jangan pakai saran Tailwind / shadcn dari skill ini untuk Streamlit. Jangan pakai palet warna dari skill ini, pakai Palet StockQu di atas.
- `google-sheets` - untuk baca / tulis stok di Sheets
- `document-skills` (xlsx + pdf) - untuk export PO dan laporan
- `to-spec` - untuk ubah kebutuhan jadi spesifikasi teknis
- `diagram-design` - untuk diagram arsitektur dan sequence
- `security-audit` - untuk audit RLS dan role

Prioritas jika konflik: `stockqu-domain` menang atas semua skill lain.

## Aturan Bisnis - Jangan Dilanggar

- Budget bulanan Purchaser: Rp 50.000.000. Total PO tidak boleh melebihi sisa budget.
- Cabang: 3 cabang di Jawa Tengah, label Branch 1, Branch 2, Branch 3 kecuali user kasih nama resmi. Jangan pakai nama kota dari lokasi user sebagai nama cabang.
- Stok terpisah per cabang. Jangan pernah menjumlahkan stok antar cabang.
- Histori prediksi: 6 bulan terakhir.
- Deteksi stockout: barang berisiko habis dalam 4 sampai 7 hari.
- Minimal supplier dibandingkan: 3 supplier per item.
- Cron: setiap hari jam 7 pagi.
- Human-in-the-loop: Tanpa balas OK di WhatsApp, jangan kirim PO via Email dan jangan update stok.

## Rumus Inti

- days-to-stockout = stok_saat_ini / rata_rata_penjualan_harian
- rata_rata_penjualan_harian = total_penjualan_6_bulan / 180
- kebutuhan_restock = (rata_rata_penjualan_harian x (lead_time_hari + safety_hari)) - stok_saat_ini
- safety_stock = rata_rata_penjualan_harian x safety_hari (default 3)

## Aturan Anti-Halusinasi

- Jika user tidak kasih nama item, stok, harga, atau diskon, jangan buat nama produk FMCG asli atau angka diskon. Pakai Item A, Item B, dst dan tulis di atas tabel: CONTOH SIMULASI - BUKAN DATA REAL.
- Jika data tidak ada, tulis DATA TIDAK DITEMUKAN. Jangan mengarang.
- Setiap angka di Reasoning Trace harus bisa ditelusur sumbernya: Sheets, Supabase, hasil Analyst, atau kontrak supplier.
- Contoh di SKILL.md seperti Supplier B diskon 10% adalah contoh format saja, bukan fakta.

## Cara Menjalankan

- Install dependencies: `pip install -r requirements.txt`
- Jalankan Streamlit: `streamlit run app.py`
- Jalankan Supabase lokal (jika perlu): `npx supabase start`
- Jangan hardcode API key, password, atau service role key. Pakai `.env` dan Streamlit secrets.

## Struktur Folder

- `app.py` - entry point Streamlit
- `src/agents/` - Analyst, Purchaser, Negotiator (LangGraph)
- `src/tools/` - tool nyata: baca Sheets, cari supplier, kirim WA / Email, update DB
- `src/services/` - Supabase client, Sheets client, budget logic
- `src/utils/` - rumus days-to-stockout, formatting, logging
- `n8n/` - file JSON workflow n8n
- `supabase/migrations/` - file SQL migrasi
- `data/` - contoh data dummy untuk demo (beri label DUMMY)
- `docs/` - proposal dan diagram

## Workflow Kerja

1. Baca `stockqu-domain` dulu sebelum coding.
2. Jika task butuh spek, pakai `to-spec` dulu, baru implementasi.
3. Setiap keputusan pembelian harus ada Reasoning Trace dan Execution Trace.
4. Setelah selesai, cek: apakah angka bisa ditelusur? Apakah approval masih wajib? Apakah RLS masih aman?
5. Jangan menambah fitur di luar F-01 sampai F-35 tanpa konfirmasi user.
