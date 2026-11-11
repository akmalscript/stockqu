# Audit halaman proposal StockQu — GTNIC 2026

Audit ini membandingkan sumber `Proposal_GTNIC_2026.docx.pdf` dengan versi final
`Proposal_GTNIC_2026_StockQu_Final.pdf`. Bukti implementasi diperiksa terhadap kode,
konfigurasi, screenshot aplikasi, dan snapshot Supabase prototipe pada 10 November 2026.

> Semua angka demo berasal dari dataset seed/simulasi dan tidak diposisikan sebagai data
> operasional perusahaan nyata.

| Halaman | Temuan pada PDF sumber | Perbaikan pada PDF final | Bukti / status |
|---:|---|---|---|
| 1 | Sampul benar tetapi masih menyerupai template dokumen umum. | Dibangun ulang dengan hierarki visual StockQu, logo GTNIC, palet wajib, nama tim, dan anggota. | Identitas asli dipertahankan. |
| 2 | Latar belakang dan rumusan masalah sudah relevan. | Dirapikan menjadi empat problem card agar cepat dipindai tanpa mengubah substansi. | Konsisten dengan alur procurement multi-gudang. |
| 3 | Baseline masih berisi placeholder. | Diisi 3 cabang, 50 SKU, 150 SKU–cabang, histori 180 hari, 5 risiko, dan 2 urgent. | Run `ANL-20261109-062039-405FB2`; label simulasi eksplisit. |
| 4 | Solusi mengklaim pgvector aktif. | Dikoreksi: retrieval aktual adalah lexical/BM25 dengan citation; pgvector menjadi roadmap. | `src/tools/contract_rag.py` dan README. |
| 5 | Mapping fitur mencapai F-64 dan menyebut dynamic tool selection/pgvector sebagai fitur aktif. | Dibatasi ke ruang F-01–F-35; fitur aktual dikelompokkan ke enam kapabilitas dan empat guardrail. | Selaras dengan AGENTS.md dan implementasi repository. |
| 6 | Skenario demo dan stack berisi beberapa placeholder. | Diisi run ID, budget, jumlah supplier, draft PO, FastAPI, LangGraph, PuLP/CBC, retrieval aktual, dan status LLM eksternal. | Plan Rp3.434.000; 2 draft PO Rp3.384.454; email belum dikirim. |
| 7 | Diagram arsitektur belum ada. | Ditambahkan diagram 9-node: Streamlit, FastAPI, LangGraph, 3 agent, Supabase, Sheets, dan n8n. | Sumber diagram mandiri: `docs/proposal-assets/diagrams/architecture.html`. |
| 8 | ERD dan retrieval pipeline masih placeholder serta mengklaim embedding aktif. | Ditambahkan model data konseptual dan pipeline lexical/BM25 yang aktual; roadmap semantic diberi label. | Sumber diagram mandiri: `docs/proposal-assets/diagrams/data-model.html`. |
| 9 | Logika agent bersifat konseptual dan memberi peran besar pada LLM. | Dibuat deterministik dan sesuai kode: formula Analyst, MILP/fallback Purchaser, validator Negotiator, Reasoning/Execution Trace. | `src/agents/`, `src/tools/forecasting.py`, dan `src/services/analytics.py`. |
| 10 | Tabel evaluasi masih seluruhnya placeholder. | Diisi MAPE median 7,9% vs baseline 10,0%, 1/1 plan feasible, 32 tool call tertangani, dan status approval yang belum terukur. | 150 seri backtest; 30 success, 2 fallback; 0 approval request. |
| 11 | Batasan sudah kuat tetapi belum merangkum deployment dan retrieval aktual. | Diperbarui dengan enam batasan dan mitigasi: data sintetis, lexical retrieval, supplier, Sandbox, deployment publik, dan otomasi. | Tidak ada klaim produksi atau dampak bisnis tanpa data. |
| 12 | Dua kotak screenshot kosong. | Diganti screenshot nyata halaman Inventory dan Analyst dari aplikasi lokal yang membaca Supabase prototipe. | Tidak menampilkan credential atau data pribadi. |
| 13 | Dua kotak screenshot kosong. | Diganti screenshot nyata Purchaser dan Negotiator. | Memperlihatkan budget, MILP/CBC, supplier, dan dua draft PO. |
| 14 | Dua kotak screenshot kosong. | Diganti screenshot Execution Log dan Approval/PO Lifecycle. | Status approval kosong dipertahankan sebagai bukti guardrail, bukan disamarkan. |
| 15 | Seluruh link masih placeholder. | Remote GitHub dicantumkan tetapi ditandai akses terbatas karena URL mengembalikan 404 tanpa login; live demo/video ditandai belum publik; surat keaslian ditandai berkas terpisah. | Tidak ada URL publik yang difabrikasi. |

## Ringkasan kontrol kualitas

- Format A4, 15 halaman, dan navigasi bookmark.
- Palet utama: teal `#0E7C61`, navy `#1E2A44`, putih `#FFFFFF`, mint `#EFF4F1`.
- Font Arial tersemat dan teks dapat diekstrak/dicari.
- Struktur PDF bertanda (tagged PDF) untuk aksesibilitas dasar.
- Tidak ditemukan token, password, service-role key, private key, nomor WhatsApp, atau email pribadi.
- Tidak ada placeholder implementasi yang tersisa; status yang belum tersedia ditulis eksplisit.
- File asli tidak diubah atau ditimpa.
