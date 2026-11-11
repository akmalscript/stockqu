# Workflow n8n StockQu (P4)

n8n hanya mengurus integrasi eksternal (jadwal, WhatsApp, Gmail). Semua keputusan,
validasi, budget, dan state PO ada di **Python Agent Service** + database
(SRS-NF-23).

| File | Fungsi | Pemicu |
|---|---|---|
| `w1_daily_cron.json` | W1 - Jalankan workflow harian | Cron `0 7 * * *` zona Asia/Jakarta |
| `w2_whatsapp_approval_notify.json` | W2 - Kirim WA approval melalui Fonnte | Dipanggil Agent Service |
| `w3_whatsapp_inbound_reply.json` | W3 - Terima balasan `OK/TOLAK/REVISI APR-...` | Webhook Fonnte |
| `w4_w5_po_email_stock_update.json` | W4 - Gmail kirim PDF PO, W5 - tandai sent + stok in-transit | Dipanggil Agent Service setelah approved |

```text
W1 cron 07:00 -> Agent Service -> Analyst -> Purchaser -> Negotiator -> approval
Manager balas OK -> W3 -> Agent Service -> PO approved
Agent Service -> W4 -> validasi approval + claim outbox -> Gmail -> W5
Tanpa OK WhatsApp: Gmail, status sent, dan stok in-transit diblokir database.
```

## 1. Jalankan n8n lokal

Salin `n8n/.env.example` menjadi `n8n/.env`, isi konfigurasi non-secret yang
sesuai, lalu jalankan:

```bash
docker compose --env-file n8n/.env -f n8n/docker-compose.yml up -d
```

Buka <http://localhost:5678> dan buat akun owner lokal. Port hanya dipublikasikan
ke `127.0.0.1`; volume Docker menyimpan workflow, credential terenkripsi, dan
encryption key lokal.

Compose ini khusus development dengan user tepercaya. Untuk production gunakan
HTTPS, set `N8N_ENCRYPTION_KEY` melalui secret manager, dan pakai external task
runner sidecar.

## 2. Terapkan migration Gmail outbox

Jalankan migration setelah seluruh migration P1-P5:

```text
supabase/migrations/20261110110000_add_po_email_delivery_outbox.sql
```

Migration menambahkan outbox idempoten dan RPC untuk claim, complete, serta
mencatat kegagalan pengiriman. RPC hanya dapat dijalankan `service_role`;
`anon` dan `authenticated` tidak memiliki akses langsung.

## 3. Import workflow

Setelah owner n8n dibuat, import workflow yang diperlukan:

```bash
docker compose --env-file n8n/.env -f n8n/docker-compose.yml exec -T n8n \
  n8n import:workflow --input=/workflows/w4_w5_po_email_stock_update.json
```

Workflow tetap nonaktif sampai seluruh credential dipilih dan test selesai.

## 4. Buat credential n8n

Buat credential berikut melalui **Credentials > Add credential**. Secret hanya
disimpan di credential n8n; jangan taruh token di JSON workflow atau Git.

- **StockQu inbound** (`Header Auth`): `Authorization` =
  `Bearer <SERVICE_TO_N8N_TOKEN>`.
- **StockQu Agent Service** (`Header Auth`): `Authorization` =
  `Bearer <N8N_TO_SERVICE_TOKEN>`.
- **StockQu Fonnte API Token** (`Header Auth`): `Authorization` = token perangkat
  Fonnte tanpa awalan `Bearer`.
- **StockQu Supabase Service Role** (`Supabase API`): project URL dan secret key
  atau legacy `service_role`.
- **StockQu Gmail OAuth2** (`Gmail OAuth2 API`): OAuth akun pengirim PO.

Pilih credential pada semua node yang sesuai. Jangan menaruh ID credential palsu
atau secret key di workflow JSON.

## 5. Buat Gmail OAuth2

Untuk self-hosted n8n, Google OAuth dibuat melalui Google Cloud:

1. Buat atau pilih project Google Cloud dan aktifkan **Gmail API**.
2. Konfigurasikan OAuth consent screen. Jika mode `External` masih testing,
   tambahkan akun Gmail procurement sebagai test user.
3. Buat OAuth Client ID bertipe **Web application**.
4. Tambahkan redirect URI:

   ```text
   http://localhost:5678/rest/oauth2-credential/callback
   ```

5. Di n8n buat credential **Gmail OAuth2 API**, isi Client ID dan Client Secret,
   gunakan dua scope minimum berikut, lalu klik **Sign in with Google**:

   ```text
   https://www.googleapis.com/auth/gmail.send
   https://www.googleapis.com/auth/gmail.metadata
   ```

   Scope `gmail.send` dipakai untuk mengirim PO. Scope `gmail.metadata` diperlukan
   karena node Gmail n8n memanggil profil akun saat menguji credential; scope ini
   hanya memberi akses metadata/header dan tidak memberi akses isi email. Jangan
   memakai scope penuh `https://mail.google.com/`.
6. Pilih credential itu pada node **W4 - Gmail kirim PO**.

Jangan kirim Client Secret atau token OAuth melalui chat dan jangan commit ke
repository.

## 6. Hubungkan Agent Service

Untuk deployment, isi URL HTTPS Agent Service di `STOCKQU_AGENT_SERVICE_URL`.
Untuk test lokal Docker Desktop, biasanya gunakan
`http://host.docker.internal:8000` dan set `STOCKQU_ALLOW_HTTP_N8N=1` hanya pada
Agent Service lokal.

Untuk production self-hosted, salin `n8n/.env.production.example`, ganti semua
domain `*.invalid`, isi `N8N_ENCRYPTION_KEY` acak, lalu jalankan base compose
bersama `docker-compose.prod.yml`. Override production memaksa HTTPS, secure
cookie, encryption key, `N8N_EDITOR_BASE_URL`, dan `WEBHOOK_URL`. Di belakang
reverse proxy set `N8N_PROXY_HOPS=1` (atau sesuai jumlah proxy tepercaya).
`WEBHOOK_URL`, bukan `N8N_WEBHOOK_URL`, adalah origin publik yang dipakai n8n
untuk membentuk URL webhook production.

Setelah workflow W2 dan W4 aktif, isi `.env` Agent Service:

```text
N8N_WA_NOTIFY_WEBHOOK_URL=https://<n8n>/webhook/stockqu-wa-notify
N8N_PO_EMAIL_WEBHOOK_URL=https://<n8n>/webhook/stockqu-po-email
```

## 7. Fonnte WhatsApp

1. Hubungkan perangkat WhatsApp di dashboard Fonnte dan aktifkan **Auto Read**.
2. Buat credential Header Auth **StockQu Fonnte API Token** di n8n. Nama header
   `Authorization`; value adalah token perangkat Fonnte tanpa awalan `Bearer`.
3. Buat `FONNTE_WEBHOOK_SECRET` acak minimal 32 karakter di environment n8n.
4. Pada Device -> Edit -> Webhook URL, isi:
   `https://<n8n>/webhook/stockqu-wa-inbound?secret=<FONNTE_WEBHOOK_SECRET>`.
5. Isi `APPROVER_WHATSAPP_NUMBERS` di Agent Service. Nomor lain, payload tanpa
   `inboxid`, format tanpa kode APR, dan webhook dengan secret salah ditolak.

Token Fonnte tidak disimpan di workflow JSON atau Agent Service. W3 meneruskan
payload yang sudah lolos secret ke Agent Service menggunakan bearer token n8n,
kemudian n8n mengirim balasan melalui API Fonnte dengan `inboxid` yang sama.

## 8. Test aman sebelum aktivasi

Lakukan validasi negatif lebih dulu dengan PO yang belum disetujui. Hasil yang benar:
claim ditolak dan Gmail tidak terpanggil. Setelah itu validasi satu PO approved
dengan alamat resmi pada `suppliers.email`.

Verifikasi setelah test positif:

- email dan PDF PO masuk ke alamat supplier dari Supabase;
- `po_email_deliveries.status = 'sent'` dan `provider_message_id` terisi;
- `purchase_orders.status = 'sent'`;
- stok hanya bertambah pada `incoming_qty` cabang PO, bukan stok fisik;
- `agent_logs` mencatat `started` dan `success`;
- pengiriman ulang PO yang sama tidak menghasilkan email kedua.

Node Gmail sengaja tidak memakai retry otomatis: timeout ambigu setelah Gmail
menerima email dapat menimbulkan duplikat. RPC Supabase idempoten dan di-retry
maksimal tiga kali. Jika Gmail gagal, periksa folder Sent dan execution log
sebelum menjalankan ulang; maksimal tiga claim per PO.
