# Audit Kesiapan End-to-End StockQu

Tanggal audit: 10 November 2026  
Basis kode: `main` pada commit `abce5f0`  
Sumber kebutuhan: SRS v5.0, roadmap StockQu, `AGENTS.md`, dan `stockqu-domain`

## Kesimpulan

Kode P0-P5 sudah memiliki jalur utama Analyst, Purchaser, Negotiator,
approval, Gmail outbox, dashboard, dan ekspor. Namun environment saat audit
**belum siap untuk klaim end-to-end penuh**. Jalur aman berhenti di human
approval; cron dan WhatsApp belum aktif, sedangkan migration Gmail outbox belum
tersedia di Supabase remote.

## Yang Sudah Terverifikasi

| Area | Hasil |
|---|---|
| Unit/integration test lokal | 114 test lulus, termasuk regression test OAuth Gmail |
| Streamlit | Health endpoint lokal merespons HTTP 200 |
| Agent Service | `/health` lokal merespons `ok` |
| n8n | Health lokal merespons `ok`; workflow W4/W5 aktif |
| Workflow JSON | W1-W5 valid JSON dan tidak menyimpan credential inline |
| Supabase lokal | Delapan migration tercatat; security advisor tidak menemukan issue |
| Gmail outbox lokal | Uji SQL dalam transaksi lulus dan di-rollback |
| RLS remote | Akses anonymous ke tabel bisnis yang diuji ditolak |
| Google Sheets | Service account dapat membaca spreadsheet; tab `Stok` dan `Incoming` tersedia |
| Secret repository | Tidak ditemukan key aktif pada tracked worktree dengan signature scan |

## Blocker Demo End-to-End Nyata

1. **Supabase remote belum memiliki Gmail outbox.** Tabel
   `po_email_deliveries` dan RPC claim outbox tidak tersedia di schema cache.
   Terapkan `20261110110000_add_po_email_delivery_outbox.sql` dari akun pemilik
   project atau koneksi database yang berwenang.
2. **W1-W3 belum aktif di n8n lokal.** Hanya W4/W5 yang terpasang dan aktif.
   Import, pilih credential, uji negatif, lalu aktifkan cron dan WhatsApp.
3. **Credential WhatsApp belum lengkap.** `TWILIO_AUTH_TOKEN` dan
   `APPROVER_WHATSAPP_NUMBERS` belum dikonfigurasi. Twilio Sandbox juga
   membutuhkan sender/credential di n8n serta webhook HTTPS publik.
4. **Tidak ada transaksi yang memenuhi gate email.** Saat audit terdapat lima
   PO berstatus `draft`; approval yang ada masih `pending`. Jangan mengubahnya
   menjadi `OK` secara palsu hanya untuk melewati gate.
5. **Google Sheets belum berisi stok.** Tab `Stok` dan `Incoming` hanya memiliki
   header. Sinkronisasi nyata belum bisa dibuktikan sampai data yang sah tersedia.
6. **Checkpoint deployment belum persisten.** `SUPABASE_DB_URL` belum diisi dan
   prototype lokal memakai memory checkpoint. Resume setelah restart belum
   memenuhi SRS-NF-09/SRS-F-37.
7. **Belum ada deployment HTTPS publik.** URL yang tersedia masih lokal, sehingga
   callback Twilio, n8n, Agent Service, dan Streamlit belum bisa diuji sebagai
   satu deployment.
8. **Demo lokal belum reproducible dari repo kosong.** Migration membentuk
   skema, tetapi repo belum menyediakan seed P0 untuk 3 branch, 50 produk,
   inventory, budget, supplier, dan kontrak. Seed histori saja tidak cukup.

## Gap Implementasi terhadap SRS v5.0

| SRS | Status/gap |
|---|---|
| F-34 | Dynamic LLM tool calling belum ada; graph saat ini memanggil Python tool secara deterministik |
| F-35, NF-25 | Retry/fallback agent ada, tetapi W1-W3 belum memiliki error branch dan recovery eksternal selengkap W4/W5 |
| F-41, F-63 | Workflow Twilio tersedia sebagai JSON, tetapi runtime/credential belum aktif |
| F-43 | Gmail send tersedia, tetapi jalur PO approved sampai email belum dapat dibuktikan di remote |
| F-45 | Konfirmasi goods receipt dan perubahan incoming menjadi on-hand belum tersedia |
| F-47 | Notifikasi eskalasi approval tertunda/supplier gagal belum tersedia |
| F-52 | Human override supplier/qty dengan alasan dan audit trail belum tersedia |
| F-54 | Embedding dan penyimpanan vector pgvector belum tersedia |
| F-55 | Hybrid semantic + keyword retrieval belum tersedia; implementasi masih metadata + BM25 |
| F-58 | Tool registry formal dengan schema, timeout, hak akses, dan error contract belum tersedia |
| F-61, F-62 | Gmail inbox dan ekstraksi quotation/attachment supplier belum tersedia |
| NF-01, NF-19, NF-28 | Deployment publik HTTPS dan independensi service belum dibuktikan |
| NF-09 | Persistent checkpoint deployment belum aktif |
| NF-10, NF-29 | Skenario demo dari repo bersih belum reproducible karena seed P0 tidak lengkap |

## Temuan Non-Blocker

- Supabase local performance advisor melaporkan 33 warning: mayoritas pemanggilan
  fungsi auth pada RLS tanpa subquery `select` dan beberapa policy permissive
  ganda. Ini bukan kegagalan keamanan, tetapi perlu migration optimasi sebelum
  volume data besar.
- Branch lama Analyst dan Purchaser memiliki patch yang sudah ekuivalen di
  `main`, walau commit hash berbeda. PR seed demo user masih konflik dan belum
  mendapat review silang.

## Gate Sebelum Menyatakan Siap Demo

- Terapkan migration remote dan pastikan RPC outbox terlihat oleh PostgREST.
- Isi credential Twilio, whitelist nomor manager, dan pasang webhook HTTPS.
- Import/konfigurasi W1-W3; pertahankan W4/W5 dengan Gmail OAuth scope minimum.
- Isi atau sinkronkan data stok yang berlabel sumber dengan benar ke Sheets.
- Isi `SUPABASE_DB_URL` dan buktikan workflow dapat resume setelah restart.
- Jalankan satu skenario resmi: cron -> draft PO -> WhatsApp `OK` nyata -> Gmail
  test recipient -> `incoming_qty` Supabase + tab `Incoming`, lalu buktikan replay
  tidak mengirim email kedua.
- Simpan execution ID, approval ID, Gmail message ID, dan durasi sebagai bukti,
  tanpa memasukkan secret ke log atau repository.
