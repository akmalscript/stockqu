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

## Git Commit Message Conventions

Semua anggota tim (Akmal, Bram, Zoe) dan AI agent WAJIB pakai format ini, tanpa scope. Tujuannya biar history rapi dan gampang dibaca.

### Format

```text
<type>: <short imperative description>

[optional body menjelaskan kenapa dan apa yang berubah]
```

### Strict Rules

1. **NEVER pakai scope di dalam kurung `(scope)`:**
   - ✅ CORRECT: `feat: add analyst forecast for days-to-stockout`
   - ❌ INCORRECT: `feat(analyst): add analyst forecast...`
   - ❌ INCORRECT: `feat(ui): add analyst forecast...`
2. **Pakai imperative mood, lowercase** untuk subject:
   - Pakai `"add"`, `"fix"`, `"update"`, `"remove"`, `"refactor"`.
   - Jangan pakai past tense (`"added"`, `"fixed"`) atau third-person (`"adds"`).
3. **No trailing punctuation:** Jangan akhiri subject dengan titik (`.`).
4. **Subject di bawah 72 karakter** kalau bisa.
5. **Body (opsional):** Kalau perlu konteks tambahan, pisahkan subject dan body dengan 1 baris kosong.

### Allowed Types

| Type       | Usage                                  | Example StockQu                                   |
| :--------- | :------------------------------------- | :------------------------------------------------ |
| `feat`     | Fitur baru                             | `feat: add analyst forecast for days-to-stockout` |
| `fix`      | Bug fix                                | `fix: correct budget remaining calculation`       |
| `docs`     | Dokumentasi saja                       | `docs: update roadmap in docs`                    |
| `style`    | Formatting / styling tanpa ubah logika | `style: reformat kpi cards in overview page`      |
| `refactor` | Restrukturisasi tanpa tambah fitur     | `refactor: simplify days-to-stockout formula`     |
| `perf`     | Performance                            | `perf: optimize supabase query for inventory`     |
| `test`     | Tambah / ubah test                     | `test: add unit test for budget calculation`      |
| `chore`    | Maintenance, config, dependencies      | `chore: update python dependencies`               |

### Larangan Commit

Jangan pernah commit: `.env`, `.venv/`, `.streamlit/secrets.toml`, file `*.pem` / `*.key`. File-file itu sudah ada di `.gitignore`.

---

## Git Branching Workflow

Semua WAJIB bikin branch baru sebelum ubah apa-apa. **Jangan commit langsung ke `main`, kecuali 1 pengecualian di bawah.**

### Pengecualian

Commit pertama (initial commit) boleh langsung ke `main` karena `main` masih kosong. Setelah itu, tidak boleh lagi.

### Branch Naming Convention

```text
<type>/<short-kebab-description>
```

**Contoh StockQu:**

- `feat/analyst-forecast`
- `feat/supabase-schema`
- `fix/budget-calculation`
- `docs/update-roadmap`
- `refactor/simplify-purchaser-logic`
- `chore/update-python-dependencies`

`<type>` harus sama dengan Allowed Types di atas.

### Branching Rules

1. **Selalu branch dari `main`:**
   ```bash
   git checkout main
   git pull origin main
   git checkout -b <type>/<short-description>
   ```
2. **Satu branch untuk satu fitur / fix / tugas.** Jangan campur perubahan yang tidak nyambung.
3. **Push branch** dan buka Pull Request ke `main` kalau sudah selesai:
   ```bash
   git push origin <type>/<short-description>
   ```
4. **Jangan pernah force-push ke `main`.** Hanya boleh force-push di branch sendiri kalau perlu.
5. **Hapus branch** setelah di-merge.
6. **Review silang:** PR Zoe direview Akmal, PR Akmal direview Bram, PR Bram direview Zoe. Minimal 1 approve sebelum merge.
