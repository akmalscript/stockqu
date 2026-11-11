# 📦 StockQu - Multi-Agent Inventory & Procurement System

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

Gunakan Python 3.10 atau lebih baru.

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

Untuk menjalankan sistem StockQu secara penuh (Dashboard UI + Agent Service + Webhook WhatsApp Approval):

### 1. Jalankan Dashboard Streamlit (Frontend)

```bash
streamlit run app.py
```

Buka di browser di `http://localhost:8501`.

### 2. Jalankan Agent Service (Backend LangGraph & Webhook)

Agent Service menangani eksekusi LangGraph (Analyst, Purchaser, Negotiator) serta webhook persetujuan WhatsApp:

```bash
# Windows:
.\.venv\Scripts\python -m scripts.run_agent_service

# Mac / Linux:
python -m scripts.run_agent_service
```

Service berjalan di `http://localhost:8000`.

### 3. Jalankan Tunnel Publik ngrok (Integrasi Webhook Eksternal)

Dibutuhkan agar n8n Cloud dan Fonnte dapat mengirimkan callback webhook ke Agent Service lokal:

```bash
ngrok http 8000 --url=<domain-ngrok-anda>
# Contoh:
# ngrok http 8000 --url=turret-backlands-overtime.ngrok-free.dev
```

### 4. n8n + Otomasi Alur (W1 - W5)

Jalankan instance n8n lokal (Docker) atau gunakan n8n Cloud yang sudah dipin ke versi stabil dan memakai timezone Asia/Jakarta:

```bash
cp n8n/.env.example n8n/.env
docker compose --env-file n8n/.env -f n8n/docker-compose.yml up -d
```

Buka `http://localhost:5678` (atau dashboard n8n Cloud Anda). Panduan lengkap pembuatan Gmail OAuth2, credential Supabase, migration outbox, import workflow, dan pengujian approval ada di [`n8n/README.md`](n8n/README.md). Workflow Gmail tidak mengirim PO tanpa status `approved` dan approval WhatsApp terbaru `OK`.

Status integrasi terakhir dan blocker sebelum demo end-to-end dicatat di [`docs/e2e-readiness-audit.md`](docs/e2e-readiness-audit.md). Jangan menyatakan demo penuh siap hanya berdasarkan unit test; cron, WhatsApp, approval, Gmail, dan update stok harus dibuktikan pada environment deployment yang sama.

## Konfigurasi Production

`localhost`, `host.docker.internal`, dan URL tunnel sementara hanya untuk
development/demo lokal. Production memakai tiga process terpisah yang semuanya
dapat saling menjangkau melalui HTTPS publik:

```text
Browser -> Streamlit HTTPS -> Agent Service HTTPS -> n8n HTTPS
Fonnte -------------------------------> n8n webhook HTTPS
n8n ---------------------------------> Agent Service callback HTTPS
Semua service -----------------------> Supabase cloud
```

1. **Streamlit Community Cloud:** salin hanya nilai dari
   `.streamlit/secrets.toml.example` ke App settings -> Secrets. Jangan masukkan
   service-role Supabase, database URL, atau credential Fonnte ke process
   Streamlit.
2. **Agent Service:** salin `.env.production.example` ke secret manager platform
   backend, ganti seluruh domain `*.invalid`, isi secret, lalu jalankan
   `python -m scripts.run_agent_service`. Endpoint `/ready` hanya mengembalikan
   `ready` setelah konfigurasi production valid.
3. **n8n self-hosted:** salin `n8n/.env.production.example` menjadi file secret
   di host, ganti domain publik, dan isi `N8N_ENCRYPTION_KEY` acak. Jalankan base
   compose bersama override `n8n/docker-compose.prod.yml`. Reverse proxy harus
   meneruskan domain n8n ke `127.0.0.1:5678`; `WEBHOOK_URL` wajib menunjuk origin
   HTTPS publik n8n.
4. Arahkan webhook inbound Fonnte ke
   `https://<domain-n8n>/webhook/stockqu-wa-inbound?secret=<FONNTE_WEBHOOK_SECRET>`
   dan gunakan URL production yang ditampilkan n8n, bukan URL test.

Contoh pemeriksaan setelah deployment:

```bash
curl --fail https://<domain-agent>/health
curl --fail https://<domain-agent>/ready
curl --fail https://<domain-n8n>/healthz
```

Perintah compose production:

```bash
docker compose --env-file n8n/.env.production \
  -f n8n/docker-compose.yml -f n8n/docker-compose.prod.yml up -d
```

Mode `STOCKQU_ENV=production` menolak HTTP, localhost, private IP, domain
placeholder, token pendek, URL callback yang berbeda, dan memory checkpoint.
Nilai URL asli tidak dapat diisi sebelum domain/hosting Agent Service dan n8n
tersedia.

## P1 - Data & Analyst

### 1. Database

Jalankan di Supabase SQL Editor, berurutan:

1. `supabase/migrations/001_init_stockqu.sql` - skema dasar P0 (jika belum diterapkan)
2. `supabase/migrations/002_p1_analyst.sql` - tabel `sales_history`, `sync_logs`, `analyst_forecasts` + RLS
3. `supabase/migrations/20261107111649_p2_purchaser.sql` - skema dan RPC Purchaser
4. `supabase/migrations/20261107190000_p1_p2_security_hardening.sql` - Auth/RLS, audit actor, dan reservasi budget atomik
5. `supabase/seed/002_sales_history_prototype.sql` - histori prototype transparan 180 hari
   (5 item x 3 cabang, deterministik, 2.700 baris). Dataset ini bukan data bisnis nyata;
   pengiriman email hanya boleh menuju `PO_EMAIL_TEST_RECIPIENT` saat `STOCKQU_ENV=prototype`.

Isi `SUPABASE_SERVICE_ROLE_KEY` di `.env` supaya Agent bisa menyimpan hasil (`analyst_forecasts`, `agent_logs`, `sync_logs`).
Key ini hanya untuk CLI/cron dan write backend; jangan pernah masukkan ke browser, UI, atau commit Git.

### Supabase Auth dan role

UI wajib login melalui Supabase Auth. Admin menetapkan role dan cabang di `app_metadata`, bukan `user_metadata`:

```json
{
  "role": "warehouse_manager",
  "branch_ids": ["<uuid-branch-yang-diizinkan>"]
}
```

Role yang didukung: `super_admin`, `warehouse_manager`, `purchasing_staff`, dan
`procurement_manager`. RLS memakai JWT user untuk read; write agent memakai
service role setelah pemeriksaan role di backend. Akses anonymous dicabut.

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

Gunakan `--dry-run` untuk membaca dan menghitung tanpa menulis inventory,
Google Sheets, forecast, ataupun log. Atau jalankan dari halaman **Prediksi &
Risiko** di Streamlit (tombol di sidebar). Tes:

CLI/cron tetap memerlukan `SUPABASE_SERVICE_ROLE_KEY` untuk read server-side;
`--dry-run` hanya menonaktifkan seluruh write. UI membaca memakai JWT user dan
RLS, bukan service role.

```bash
python -m pytest
```

### Arsitektur Analyst (LangGraph)

`sync_stock -> load_history -> forecast -> detect_risk -> explain -> persist`

- Rumus resmi ada di satu tempat: `src/utils/formulas.py`.
- Forecast (`src/tools/forecasting.py`): tren linear 8 minggu x musiman mingguan, dibandingkan dengan baseline rata-rata 180 hari lewat backtest 14 hari. Status risiko tetap memakai rumus resmi; forecast jadi evidence + peringatan dini + rentang ketidakpastian.
- Setiap angka di Reason Trace menyebut sumbernya (Sheets / Supabase / Analyst).

## P2 - Purchaser

Jalankan migration P2 setelah migration P0 dan P1:

```text
supabase/migrations/20261107111649_p2_purchaser.sql
```

Migration P2 menambah MOQ/pack pada kontrak, `procurement_plans`, dan
`procurement_plan_items`. Migration hardening mencabut akses anonymous,
membatasi UI dengan Supabase Auth/RLS, menyimpan identitas requester, serta
menambah `procurement_budget_reservations`. RPC transaksional
`persist_procurement_plan` hanya dapat dipanggil `service_role`; RPC mengunci
periode, menghitung ulang budget dari tabel tepercaya, memvalidasi MOQ/pack dan
line total, lalu menyimpan plan dan reservasinya dalam satu transaksi.

Jalankan Purchaser dari CLI atau halaman **Rencana Pembelian**:

```bash
python -m scripts.run_purchaser --period 2026-11
```

Alur LangGraph: `load_inputs -> prioritize -> optimize -> explain -> validate -> persist`.
Budget tersedia dihitung ulang dari `total - used - PO committed - plan allocated`, tetap dibatasi Rp 50.000.000. Optimizer MILP
mematuhi budget, MOQ, pack size, dan unit keputusan SKU-cabang. Jika solver gagal, fallback greedy dicatat di
Execution Trace. Item tanpa harga kontrak tidak diberi harga buatan: keputusan menjadi `deferred` dengan label
`DATA TIDAK DITEMUKAN`.

## P3 - Negotiator

1. Jalankan migration `supabase/migrations/20261109100000_p3_negotiator.sql` (setelah hardening P1/P2).
   Isinya: `supplier_documents` + `supplier_document_chunks` (RAG kontrak), `negotiation_runs`,
   `supplier_comparisons`, kolom draft PO di `purchase_orders`/`po_items`, bucket Storage privat
   `supplier-documents`, dan RPC `persist_negotiation` (khusus `service_role`).
2. Buat dan indeks kontrak prototype per supplier. Isi PDF hanya dari tabel `contracts`, `suppliers`,
   `products` dan berlabel `CONTOH SIMULASI - BUKAN DATA REAL`:

   ```bash
   python -m scripts.seed_supplier_documents
   ```

   Super Admin juga bisa upload PDF kontrak lain dari halaman **Perbandingan Supplier**.

3. Jalankan Negotiator (setelah Purchaser punya plan tersimpan):

   ```bash
   python -m scripts.run_negotiator
   ```

Alur LangGraph: `load_plan -> search_suppliers -> retrieve_contracts -> compare_select -> budget_recheck
-> (replan_cheapest -> compare_select) -> draft_documents -> validate -> persist`.

- Penawaran: minimal 3 supplier per SKU dari kontrak tersimpan; kurang dari 3, kontrak kedaluwarsa, atau
  data tidak lengkap ditandai `needs_review` dan tidak dikarang.
- Pemilihan: supplier yang tiba sebelum stok habis didahulukan, lalu skor 40% biaya + 25% performa +
  25% lead time + 10% verifikasi dokumen. Alasan dan evidence tersimpan di `supplier_comparisons.reason_trace`.
- RAG: chunk per Pasal + metadata filter + BM25, kutipan menyebut dokumen, pasal, halaman, dan masa berlaku.
  Angka harga/diskon/MOQ/pack di klausul dicocokkan dengan tabel `contracts` (`verified`/`mismatch`).
  Semantic vector search (pgvector) belum dipakai karena provider embedding belum dipilih.
- Budget recheck: total draft PO dibandingkan dengan budget tersedia + reservasi plan. Jika lewat, satu kali
  replanning ke supplier termurah; jika tetap lewat, run `failed` dan Purchaser harus dijalankan ulang.
  RPC memvalidasi ulang semuanya di database dengan advisory lock periode.
- Output: draft PO per supplier x gudang tujuan, draft email PO dan draft email negosiasi, ekspor PDF/Excel.
  **Tidak ada yang dikirim**; status PO tetap `draft` sampai approval manager (P4).

## P4 - Orkestrasi & Aksi Nyata

1. Jalankan migration `supabase/migrations/20261109200000_p4_orchestration_approval.sql` (setelah P3).
   Isinya: `workflow_jobs` (anti duplikat job harian), `approval_requests` (kode `APR-YYYYMMDD-NN`),
   `po_status_history`, `inventory.incoming_qty` + `inventory_transactions`, kolom `suppliers.email`,
   schema `langgraph` untuk checkpoint, dan RPC state machine `request_po_approval`, `decide_approval`,
   `mark_po_sent` (khusus `service_role`).
2. Jalankan migration
   `supabase/migrations/20261110110000_add_po_email_delivery_outbox.sql` untuk claim Gmail idempoten,
   audit provider message ID, dan pencegahan email ganda.
3. Isi variabel P4 di `.env` (lihat `.env.example`), termasuk `SUPABASE_DB_URL` untuk checkpoint Postgres.
   Untuk production, isi `suppliers.email` dengan alamat resmi supplier. Untuk prototype dengan data simulasi,
   isi `PO_EMAIL_TEST_RECIPIENT`; sistem hanya mengirim jika seluruh email supplier diarahkan ke inbox demo itu.
   Mode production tetap memblokir seluruh pengiriman dari data simulasi.
4. Jalankan Agent Service di belakang HTTPS:

   ```bash
   python -m scripts.run_agent_service
   ```

5. Import dan konfigurasi workflow n8n W1-W5: lihat [n8n/README.md](n8n/README.md).

Graph penuh (`src/orchestration/procurement_graph.py`):
`analyst -> purchaser -> negotiator -> budget_recheck -> request_approval -> wait_approval (interrupt)
-> apply_decision -> dispatch_po_emails`. Checkpoint PostgresSaver menyimpan posisi graph, sehingga
balasan WhatsApp berjam-jam kemudian melanjutkan thread yang sama.

State machine PO dijaga di database:
`draft -> waiting_approval -> approved -> sent` (atau `rejected`, atau kembali `draft` saat revisi/kedaluwarsa).
`mark_po_sent` menolak PO yang belum `approved`; claim Gmail juga menolak jika approval WhatsApp terbaru
bukan `OK`, sehingga email dan update stok tidak bisa terjadi tanpa OK.
Barang terkirim dicatat sebagai `incoming_qty` (Supabase) dan tab `Incoming` (Google Sheets), bukan stok fisik.
Idempotensi: satu job cron per hari, satu approval per negotiation run, `decision_ref` unik per balasan
Fonnte/dashboard, dan ledger `inventory_transactions` unik per PO-SKU.

Approval: balas `OK APR-...`, `TOLAK APR-...`, atau `REVISI APR-...` dari nomor di `APPROVER_WHATSAPP_NUMBERS`.
Webhook Fonnte dilindungi secret di n8n, lalu nomor dan kode APR divalidasi Agent Service. Jika WhatsApp gagal,
manager bisa memutuskan di halaman **PO & Approval**.

## P5 - Dashboard & Laporan

Jalankan migration `supabase/migrations/20261110100000_p5_admin_audit.sql` (setelah P4): RLS tulis
Super Admin untuk produk/supplier/kontrak/budget, plafon budget Rp 50 jt di database, dan `audit_log`
append-only yang diisi trigger.

`app.py` adalah router `st.navigation`: login Supabase Auth sekali, lalu 11 halaman di `app_pages/`.
Menu mengikuti role (hanya UX), sedangkan data tetap disaring RLS dengan JWT user.

| Seksi           | Halaman                                                                                                                              | Role                                               |
| --------------- | ------------------------------------------------------------------------------------------------------------------------------------ | -------------------------------------------------- |
| Operasional     | Command center (status agent per tahap, jalankan sekarang), Inventori (stok fisik + in-transit per cabang)                           | semua                                              |
| Agent           | Prediksi & risiko, PO & approval                                                                                                     | semua                                              |
| Agent           | Rencana pembelian, Perbandingan supplier                                                                                             | Super Admin, Purchasing Staff, Procurement Manager |
| Jejak & laporan | Reason trace (faktor + sumber, tautan ke log run), Execution log, Analitik (KPI berlabel aktual/backtest/target), Ekspor (PDF/Excel) | semua                                              |
| Pengaturan      | Admin (CRUD master data, upload kontrak, audit log, panduan user/role)                                                               | Super Admin                                        |

Role user diatur di Supabase Dashboard -> Authentication -> Users -> `app_metadata`,
misalnya `{"role": "warehouse_manager", "branch_ids": ["<uuid Branch 1>"]}`.
Untuk tombol **Jalankan** dan approval dashboard, isi `STOCKQU_AGENT_SERVICE_URL` dan `N8N_TO_SERVICE_TOKEN`
di Streamlit secrets.

## Quality gate lokal

Test tidak memerlukan network atau kredensial live:

```bash
python -m pytest -q
ruff check app.py app_pages scripts src tests test_supabase.py
git diff --check
```

Suite mencakup formula StockQu, graph Analyst/Purchaser, dry-run tanpa write,
RBAC/admission control, reservasi budget, sinkronisasi Sheets atomik, dan
invariant migration security P1/P2. Validasi integrasi Supabase/Sheets live tetap
dijalankan terpisah pada project staging dengan kredensial yang sudah dirotasi.

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
  app_pages/     # 11 halaman Streamlit (router st.navigation di app.py)
  scripts/       # CLI: run_analyst.py + run_purchaser.py
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
