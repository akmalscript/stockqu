---
name: stockqu-domain
description: Aturan bisnis StockQu Multi-Agent Procurement System. Pakai skill ini setiap kali mengerjakan StockQu di Antigravity, baik coding, desain dashboard, nulis proposal, atau demo. Memuat budget, rumus stok, peran 3 agent LangGraph, alur approval WhatsApp, role login, format Reasoning Trace dan Execution Trace, serta mapping F-01 sampai F-35. Trigger saat user menyebut StockQu, Analyst, Purchaser, Negotiator, days-to-stockout, PO, approval, gudang, atau GTNIC.
---

# StockQu Domain Skill

## 1. Apa itu StockQu

StockQu adalah AI Agent otonom untuk distributor FMCG dengan 3 cabang gudang di Jawa Tengah dan manufaktur dengan raw material warehouse.

Bukan dashboard BI biasa. StockQu memprediksi stok akan habis, memutuskan apa yang harus dibeli dengan budget terbatas, membandingkan 3 supplier, membuat draft PO, meminta approval via WhatsApp, dan mengupdate stok secara otomatis.

Target GTNIC: Enterprise dan Industry. Deploy live: Streamlit (Frontend) + Supabase (Backend). Repo publik.

## 2. Aturan Bisnis yang Tidak Boleh Dilanggar

### 2.1 Konstanta Wajib

- Budget bulanan Purchaser: Rp 50.000.000. Angka ini adalah constraint keras. Total PO dalam satu siklus tidak boleh melebihi sisa budget bulan berjalan.
- Jumlah cabang: minimal 3 cabang di Jawa Tengah. Stok terpisah per lokasi. Jangan pernah menjumlahkan stok antar cabang seolah satu gudang.
- Nama cabang: pakai label Branch 1, Branch 2, Branch 3 kecuali user sudah memberikan nama resmi. Jangan pakai nama kota dari lokasi user sebagai nama cabang.
- Histori penjualan untuk prediksi: 6 bulan terakhir.
- Ambang deteksi stockout: barang berisiko habis dalam 4 sampai 7 hari ke depan.
- Minimal supplier yang dibandingkan: 3 supplier berbeda per item.
- Cron otonom: setiap hari jam 7 pagi, ala Hermes. Cek stok, prediksi, prioritas, bandingkan supplier, buat draft PO, minta approval.
- Siklus dari deteksi sampai draft PO: sekitar 15 menit. Bandingkan dengan proses manual 2 hari.
- Human-in-the-loop: Manager tetap memegang kendali di titik approval finansial. Tanpa balasan OK di WhatsApp, jangan kirim PO via Email dan jangan update stok.

### 2.2 Rumus Inti

Gunakan rumus ini, jangan mengarang rumus lain:

- days-to-stockout = stok_saat_ini / rata_rata_penjualan_harian
- rata_rata_penjualan_harian = total_penjualan_6_bulan / 180
- kebutuhan_restock = (rata_rata_penjualan_harian x (lead_time_hari + safety_hari)) - stok_saat_ini
- safety_stock = rata_rata_penjualan_harian x safety_hari (default safety_hari = 3 jika tidak ada data lain)
- Jika kebutuhan_restock <= 0, jangan usulkan pembelian.
- Jika days-to-stockout <= 7, masuk daftar prioritas. Jika <= 4, tandai URGENT.

Contoh acuan dari dokumen final: butuh 100 unit dalam 4 hari, dengan perhitungan safety stock.

### 2.3 Prioritas Purchaser

Urutkan pembelian dengan skor gabungan dari 4 faktor ini, dalam urutan ini:

1. Urgensi stockout (days-to-stockout paling kecil duluan)
2. Margin keuntungan (margin lebih tinggi didahulukan)
3. Turnover rate (barang yang perputarannya lebih cepat didahulukan)
4. Ketersediaan budget dan diskon supplier yang sedang berlaku

Jangan hanya pakai aturan sederhana seperti jika stok kurang dari 10 maka beli. Itu yang dilarang di F-07.

### 2.4 Metrik Keberhasilan Enterprise

Tampilkan metrik ini di laporan enterprise, jangan ubah targetnya:

- Pengurangan stockout: 80%
- Hemat waktu cek stok manual: 15 jam per minggu
- Pengurangan dead stock: 30%
- Siklus pengadaan: dari 2 hari menjadi 15 menit untuk draft PO
- Akurasi prediksi: berbasis histori 6 bulan, diukur dengan membandingkan prediksi days-to-stockout dengan realisasi

## 3. Arsitektur

- Orkestrasi: LangGraph. Alur dikontrol sebagai graph state, bukan sekadar prompt berantai. Setiap agent bisa dipanggil, diulang, atau dikoreksi jika tool gagal.
- Intelligence: LLM + Python Tool untuk forecasting time-series, reasoning, dan RAG untuk kontrak supplier.
- Data: Supabase sebagai backend utama, Google Sheets sebagai sumber stok real-time. Sinkron dua arah.
- Otomasi: n8n untuk cron, notifikasi WhatsApp, kirim email PO, dan trigger update stok.
- Frontend: Streamlit untuk dashboard agent, Reason Trace Panel, dan laporan enterprise.

## 4. Tiga Agent - Peran, Tool, Output

### Agent 1: The Analyst - Prediksi

- Tugas: Analisis histori penjualan 6 bulan memakai Python tool untuk time-series prediction dan LLM untuk interpretasi tren.
- Output: days-to-stockout per SKU, daftar barang berisiko habis 4 sampai 7 hari, jumlah restock yang dibutuhkan.
- Wajib sertakan Reasoning Trace. Contoh yang benar: Diprediksi habis karena penjualan naik 30% minggu lalu dan ada tren musiman.
- Jangan tampilkan hanya angka stok saat ini. Harus ada prediksi kapan habis dan berapa unit yang dibutuhkan.

### Agent 2: The Purchaser - Keputusan dengan Budget

- Tugas: Perencanaan dan pengambilan keputusan di bawah constraint budget Rp 50.000.000.
- Tool: Baca hasil Analyst, baca sisa budget, hitung prioritas dan optimasi alokasi budget.
- Output: Daftar prioritas pembelian, alokasi budget per item, total yang diajukan, sisa budget, dan estimasi dampak bisnis.
- Wajib sertakan Reasoning Trace. Contoh yang benar: Item A diprioritaskan karena turnover 3x lebih cepat, margin 30%, dan Supplier B diskon 10% sampai hari ini.
- Jika total usulan melebihi budget, tunda item dengan prioritas terendah ke minggu depan. Tulis alasannya secara eksplisit di Reasoning Trace.

### Agent 3: The Negotiator - Eksekusi Pengadaan

- Tugas: Baca kontrak supplier lama memakai RAG, cari dan bandingkan penawaran dari minimal 3 supplier via API atau web scraping, susun draft negosiasi dan draft PO.
- Matriks perbandingan supplier wajib berisi: harga, lead time, diskon, dan performa supplier.
- Output: Matriks perbandingan, draft email negosiasi, dan draft PO berdasarkan supplier terpilih.
- Kirim draft PO dan perbandingan harga ke sistem approval untuk review manager. Jangan langsung kirim ke supplier sebelum approval.

## 5. Alur Otonom End-to-End (Demo Grand Final)

Urutan ini adalah alur yang didemokan ke juri. Semua langkah tercatat di execution trace:

1. Jam 7 pagi cron jalan otomatis ala Hermes.
2. Agent cek stok di Supabase dan Google Sheets untuk 3 cabang gudang.
3. Terdeteksi barang yang berisiko habis dalam 4 sampai 7 hari.
4. Prediksi kebutuhan: butuh 100 unit dalam 4 hari, dengan perhitungan safety stock.
5. Purchaser alokasikan budget dari total Rp 50.000.000 dan prioritaskan 5 item tersebut.
6. Negotiator bandingkan 3 supplier dan buat matriks harga, lead time, diskon, dan performa.
7. Dibuat 3 draft PO plus perbandingan harga dan draft email negosiasi.
8. Kirim notif WA ke manager via n8n: Pak, butuh approval Rp 12jt untuk 5 item, paling urgent Item X.
9. Manager balas OK di WhatsApp. Sistem kirim PO via Email secara otomatis.
10. Stok otomatis ter-update di Google Sheets dan Supabase. Log eksekusi tersimpan untuk audit.

## 6. Format Wajib - Reasoning Trace dan Execution Trace

### 6.1 Reasoning Trace Panel

Setiap keputusan pembelian harus menampilkan panel ini di Streamlit:

- Kenapa Agent prioritaskan beli Item A dulu? Karena Item A turnover 3x lebih cepat, margin 30%, dan Supplier B lagi diskon 10% sampai hari ini.
- Kenapa Item B ditunda? Karena anggaran terbatas, jadi Item B ditunda ke minggu depan.
- Prediksi Analyst: Item A habis dalam 3 hari karena penjualan naik 30% minggu lalu.

Aturan: Reasoning Trace harus menyebut angka nyata dari data, bukan kalimat generik seperti karena penting atau karena mendesak.

### 6.2 Execution Trace / Audit Log

Simpan untuk setiap eksekusi agent:

- Kapan dijalankan (timestamp)
- Tool apa yang dipakai (Baca Sheets, Cari Supplier, Kirim WA dan Email via n8n, Update Database)
- Apa yang berhasil, apa yang gagal dan di-retry
- Apa output akhirnya

Ini yang membedakan StockQu dari prototype dashboard sederhana di mata juri GTNIC.

## 7. Role, Keamanan, dan Audit

- Super Admin: Mengelola user, produk, supplier, dan kontrak. Akses penuh.
- Warehouse Manager: Melihat stok multi-gudang, menyetujui PO, dan memantau eksekusi agent.
- Purchasing Staff: Mengelola proses pembelian, melihat perbandingan supplier, dan mengekspor laporan.
- Login dan logout aman dengan role di atas.
- Self-correction: Jika baca Sheets gagal, sistem retry otomatis, mencatat error di log, dan memberi notifikasi tanpa menghentikan seluruh alur.
- Audit log: Semua aksi agent dan approval manager tersimpan dengan timestamp.
- Jangan pernah menampilkan data lintas role yang tidak berhak. Jangan hardcode password, API key, atau service role key di kode.

## 8. Data dan CRUD

### 8.1 Sumber Data

- F-01: Import dan baca data inventori real-time dari Google Sheets dan Supabase sebagai sumber stok utama.
- F-02: Manajemen multi-gudang minimal 3 cabang di Jawa Tengah, stok terpisah per lokasi.
- F-03: Info produk lengkap: SKU, nama, kategori, stok saat ini, lead time supplier, margin, dan turnover rate.
- F-04: Admin dapat CRUD data produk, supplier, dan data kontrak supplier historis.
- F-05: Login dan logout aman dengan 3 role di atas.

### 8.2 Skema Minimal di Supabase

Tabel yang harus ada:

- products: id, sku, name, category, margin, turnover_rate, lead_time_days, safety_days
- inventory: product_id, branch_id, stock_qty, updated_at
- branches: id, name, city (Jawa Tengah), address
- suppliers: id, name, contact, lead_time_days, performance_score
- contracts: id, supplier_id, product_id, price, discount, valid_until, file_url
- purchase_orders: id, supplier_id, total_amount, status (draft, waiting_approval, approved, sent), created_at
- po_items: po_id, product_id, qty, unit_price
- approvals: po_id, approver_role, decision (OK / REJECT), via (WhatsApp), timestamp
- agent_logs: agent_name, run_id, tool_name, status, input_summary, output_summary, error_message, created_at
- budgets: month, total_budget (50000000), used_budget, remaining_budget

Gunakan Row Level Security (RLS) di Supabase sesuai role.

## 9. Tool Use Nyata (Bukan Dummy)

Tool Use yang wajib nyata dan bisa diverifikasi juri:

- Baca Sheets
- Cari Supplier (API atau web scraping)
- Kirim WA dan Email via n8n
- Update Database (Supabase + Sheets)

Jangan buat fungsi dummy yang return data hardcode dan mengklaim sebagai Tool Use.

## 10. Dashboard Streamlit

- F-31: Dashboard agent otonom yang menampilkan status agent, reasoning, dan tindakan nyata, bukan hanya grafik stok menipis.
- F-32: Tampilkan panel Reason Trace interaktif untuk setiap keputusan pembelian.
- F-33: Laporan analitik efisiensi enterprise meliputi inventory turnover, pengurangan dead stock, dan estimasi penghematan biaya operasional.
- F-34: Deploy live menggunakan Streamlit (frontend) + Supabase (backend).
- F-35: Dapat mengekspor laporan PO dan perbandingan supplier dalam format PDF dan Excel.

Struktur halaman Streamlit:

1. Overview: KPI stockout risk, sisa budget, status cron jam 7 pagi, 5 item prioritas
2. Agent Run: tombol jalankan Analyst, Purchaser, Negotiator, plus Execution Trace
3. Reason Trace Panel: alasan per item
4. Supplier Comparison: matriks 3 supplier per item
5. Approval: status WA, tombol simulasi balas OK
6. Enterprise Report: turnover, dead stock, hemat waktu, export PDF dan Excel

## 11. Template Pesan dan Dokumen

### 11.1 WhatsApp Approval ke Manager

Template:

Pak, butuh approval Rp [TOTAL] untuk [JUMLAH_ITEM] item, paling urgent Item [NAMA_ITEM].
Rincian: [ITEM_1] - [QTY] unit dari [SUPPLIER], Rp [HARGA].
Alasan: [REASONING_SINGKAT].
Balas OK untuk kirim PO.

Contoh dari dokumen final: Pak, butuh approval Rp 12jt untuk 5 item, paling urgent Item X.

### 11.2 Email PO ke Supplier

Subject: Purchase Order [PO_NUMBER] - StockQu

Body harus berisi: nama supplier, daftar item dengan SKU, qty, harga satuan, total, tanggal kirim yang diminta berdasarkan lead time, dan nomor PO.

### 11.3 Draft Email Negosiasi

Sertakan: referensi kontrak lama dari RAG, harga kompetitor dari matriks 3 supplier, permintaan diskon atau lead time lebih cepat, dan batas waktu penawaran.

## 12. Mapping F-01 sampai F-35 (Cek Cepat)

- F-01 sampai F-05: Manajemen data, multi-gudang, CRUD, login role
- F-06 sampai F-10: Analyst - histori 6 bulan, days-to-stockout, deteksi 4 sampai 7 hari, hitung restock, Reasoning Trace
- F-11 sampai F-15: Purchaser - constraint budget Rp 50.000.000, prioritas, Reasoning Trace, optimasi alokasi, estimasi dampak bisnis
- F-16 sampai F-20: Negotiator - RAG kontrak lama, bandingkan minimal 3 supplier, matriks harga lead time diskon performa, draft email dan PO, kirim ke approval
- F-21 sampai F-25: Orkestrasi otonom end-to-end, cron jam 7 pagi, LangGraph, self-correction dan error handling, execution trace untuk audit
- F-26 sampai F-30: n8n untuk WA approval, balas OK picu update stok, kirim PO via Email setelah approval, update stok di Sheets dan Supabase, Tool Use nyata
- F-31 sampai F-35: Dashboard agent, Reason Trace Panel, laporan enterprise, deploy Streamlit + Supabase, export PDF dan Excel

Jika ada fitur yang tidak ada di daftar ini, tanyakan dulu ke user sebelum menambahkan. Jangan menambah fitur di luar SRS tanpa konfirmasi.

## 13. Larangan

- Jangan mengarang harga supplier, diskon, atau stok jika data tidak ada. Tulis DATA TIDAK DITEMUKAN dan lanjut ke item berikutnya.
- Jangan melewati approval manager untuk PO di atas Rp 0. Semua PO wajib approval.
- Jangan menjumlahkan stok antar cabang.
- Jangan pakai aturan jika stok kurang dari 10 maka beli sebagai pengganti forecasting.
- Jangan buat dashboard yang hanya grafik tanpa Reasoning Trace dan Execution Trace.
- Jangan simpan API key di repo. Pakai environment variable atau secrets di Streamlit dan Supabase.

### 13.1 Aturan Anti-Halusinasi (Patch dari Test 7 Nov 2026)

Aturan ini ditambahkan karena pada test prioritas 5 item, agent mengarang diskon 5% dan nama Gudang Semarang padahal data tidak diberikan.

- Jika user tidak memberikan nama item, stok, harga, atau diskon, jangan buat nama produk FMCG asli seperti Minyak Goreng 2L, Gula Pasir 1kg, atau sejenisnya. Pakai label generik Item A, Item B, Item C, Item D, Item E.
- Jika user tidak memberikan diskon supplier, jangan buat angka diskon seperti diskon 5% untuk pembelian min. 50 karton. Tulis DATA TIDAK DITEMUKAN untuk kolom diskon.
- Jika user tidak memberikan nama cabang, jangan pakai nama kota dari lokasi user seperti Semarang sebagai nama cabang. Cabang StockQu hanya disebut 3 cabang di Jawa Tengah, dengan label Branch 1, Branch 2, Branch 3, kecuali user sudah memberikan nama cabang resmi.
- Setiap tabel simulasi wajib diawali label ini di atas tabel: CONTOH SIMULASI - BUKAN DATA REAL.
- Contoh di skill ini seperti Supplier B diskon 10% sampai hari ini atau penjualan naik 30% minggu lalu adalah contoh format Reasoning Trace saja, bukan fakta yang boleh disalin mentah-mentah ke jawaban.
- Jika user minta keputusan real, bukan simulasi, minta dulu data Sheet atau Supabase-nya. Jangan lanjut dengan angka karangan.
- Setiap angka di Reasoning Trace harus bisa ditelusur sumbernya: dari Sheets, dari Supabase, dari hasil Analyst, atau dari kontrak supplier. Jika tidak ada sumbernya, tulis DATA TIDAK DITEMUKAN.

## 14. Cara Memakai Skill Ini di Antigravity

1. Simpan file ini di `.agents/skills/stockqu-domain/SKILL.md`
2. Saat mulai task StockQu, baca skill ini dulu, lalu baca skill teknis yang relevan: supabase, langgraph-patterns, n8n workflow-architect, streamlit-expert, google-sheets, document-skills.
3. Setiap jawaban yang berisi angka harus menyebut sumber datanya: dari Sheets, dari Supabase, dari hasil Analyst, atau dari kontrak supplier.
4. Setiap keputusan pembelian harus disertai Reasoning Trace dalam format di Bagian 6.
