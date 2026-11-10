# Audit Kesiapan End-to-End StockQu

Tanggal audit ulang: 10 November 2026
Basis kode: `origin/main` pada commit `fbc5de5` dan branch `fix/production-gmail-delivery`
Sumber kebutuhan: SRS v5.0, roadmap StockQu, `AGENTS.md`, dan `stockqu-domain`

## Kesimpulan

Kode P0-P5 sudah memiliki jalur Analyst, Purchaser, Negotiator, approval,
Gmail outbox, dashboard, dan ekspor. Namun environment showcase **belum lulus
end-to-end penuh**. WhatsApp cloud lama merespons, tetapi workflow Fonnte hasil
perbaikan belum diimpor. Gmail production masih tidak aktif, migration outbox
belum ada di Supabase remote, dan data histori masih `DUMMY-SEED`.

## Yang Sudah Terverifikasi

| Area | Hasil |
|---|---|
| Unit/integration test lokal | 131 test lulus, termasuk Fonnte, approval, Gmail outbox, dan production config |
| Streamlit | Health endpoint lokal merespons HTTP 200 |
| Agent Service | `/health` lokal merespons `ok` |
| n8n WhatsApp cloud | W2 menerima bearer token (HTTP 200); W3 menolak payload tanpa approver secara aman |
| Workflow JSON | W1-W5 valid JSON dan tidak menyimpan credential inline |
| Supabase lokal | Delapan migration tercatat; security advisor tidak menemukan issue |
| Gmail outbox lokal | Uji SQL dalam transaksi lulus dan di-rollback |
| RLS remote | Akses anonymous ke tabel bisnis yang diuji ditolak |
| Google Sheets | Service account membaca 150 baris `Stok` dan 10 baris `Incoming` |
| Secret repository | Tidak ditemukan key aktif pada tracked worktree dengan signature scan |

## Blocker Demo End-to-End Nyata

1. **Gmail production belum aktif.** POST ke webhook
   `stockqu-po-email` mengembalikan 404 karena W4/W5 belum aktif di n8n Cloud.
2. **Supabase remote belum memiliki Gmail outbox.** Tabel
   `po_email_deliveries` dan RPC claim outbox tidak tersedia di schema cache.
   Terapkan `20261110110000_add_po_email_delivery_outbox.sql` dari akun pemilik
   project atau koneksi database yang berwenang.
3. **Master supplier belum siap email.** Tiga supplier remote belum memiliki
   alamat email, sehingga pengiriman PO wajib diblokir.
4. **Data histori belum production.** Remote masih memuat 27.300 baris
   `sales_history` dengan sumber `DUMMY-SEED`. Branch perbaikan memblokir email
   supplier selama sumber dummy masih ada.
5. **Fonnte belum terpasang di cloud.** Workflow W2/W3 Fonnte pada branch sudah
   menghapus token dari kode, memvalidasi webhook secret, whitelist nomor, kode
   APR, dan `inboxid`, tetapi workflow cloud masih versi lama dan perlu diimpor.
6. **Agent Service publik belum terverifikasi.** URL di `.env` lokal mengarah ke
   aplikasi lain. W3 cloud mampu mencapai suatu backend, tetapi URL deployment
   StockQu yang dipakai n8n harus dikonfirmasi sebelum showcase.
7. **Checkpoint deployment belum persisten.** `SUPABASE_DB_URL` belum diisi dan
   prototype lokal memakai memory checkpoint. Resume setelah restart belum
   memenuhi SRS-NF-09/SRS-F-37.
8. **Streamlit publik masih private.** URL deployment mengalihkan pengguna ke
   halaman login Streamlit Community Cloud.
9. **Demo lokal belum reproducible dari repo kosong.** Migration membentuk
   skema, tetapi repo belum menyediakan seed P0 untuk 3 branch, 50 produk,
   inventory, budget, supplier, dan kontrak. Seed histori saja tidak cukup.

## Gap Implementasi terhadap SRS v5.0

| SRS | Status/gap |
|---|---|
| F-34 | Dynamic LLM tool calling belum ada; graph saat ini memanggil Python tool secara deterministik |
| F-35, NF-25 | Retry/fallback agent ada; W2/W3 Fonnte sudah memiliki retry dan error branch, tetapi W1 serta recovery eksternal masih perlu dibuktikan di cloud |
| F-41, F-63 | Workflow Fonnte tersedia sebagai JSON, tetapi versi terbaru dan credential-nya belum aktif di n8n Cloud |
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
- Isi email resmi pada `suppliers.email` dan ganti histori `DUMMY-SEED` dengan data yang sah.
- Import W2/W3 Fonnte, pilih credential, isi webhook secret, lalu aktifkan.
- Import/aktifkan W4/W5 dengan Gmail OAuth scope minimum.
- Perbaiki URL Agent Service dan buka akses aplikasi Streamlit untuk juri.
- Isi `SUPABASE_DB_URL` dan buktikan workflow dapat resume setelah restart.
- Jalankan satu skenario resmi: cron -> draft PO -> WhatsApp `OK` nyata -> Gmail
  supplier -> `incoming_qty` Supabase + tab `Incoming`, lalu buktikan replay
  tidak mengirim email kedua.
- Simpan execution ID, approval ID, Gmail message ID, dan durasi sebagai bukti,
  tanpa memasukkan secret ke log atau repository.
