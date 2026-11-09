# Workflow n8n StockQu (P4)

n8n hanya mengurus integrasi eksternal (jadwal, WhatsApp, Gmail). Semua keputusan, validasi,
budget, dan state PO ada di **Python Agent Service** + database (SRS-NF-23).

| File | Fungsi | Pemicu |
|---|---|---|
| `w1_daily_cron.json` | W1 - Jalankan workflow harian | Cron `0 7 * * *` zona Asia/Jakarta |
| `w2_whatsapp_approval_notify.json` | W2 - Kirim WA approval ke manager | Dipanggil Agent Service |
| `w3_whatsapp_inbound_reply.json` | W3 - Terima balasan `OK/TOLAK/REVISI APR-...` | Webhook Twilio |
| `w4_w5_po_email_stock_update.json` | W4 - Gmail kirim PO, W5 - tandai sent + stok in-transit | Dipanggil Agent Service setelah approved |

```
W1 cron 07:00 -> Agent Service: Analyst -> Purchaser -> Negotiator -> budget_recheck -> approval (PO waiting_approval)
            Agent Service -> W2 -> Twilio WA ke manager: "... Balas OK APR-20261109-01"
Manager balas -> Twilio -> W3 -> Agent Service (cek signature, whitelist, kode) -> PO approved
            Agent Service -> W4 -> Gmail (PDF PO) -> W5 -> Agent Service: PO sent + incoming stock (Supabase + Sheets)
Tanpa OK: tidak ada panggilan W4, dan database menolak update stok untuk PO yang belum approved.
```

## 1. Variabel environment n8n

Set `N8N_BLOCK_ENV_ACCESS_IN_NODE=false` agar node bisa membaca `$env`, lalu isi:

| Variabel | Contoh | Keterangan |
|---|---|---|
| `STOCKQU_AGENT_SERVICE_URL` | `https://agent.stockqu.example` | URL HTTPS Agent Service, tanpa `/` di akhir |
| `TWILIO_WHATSAPP_FROM` | `+14155238886` | Nomor Twilio WhatsApp Sandbox |
| `STOCKQU_WA_INBOUND_URL` | `https://n8n.example/webhook/stockqu-wa-inbound` | URL persis yang diisi di Twilio (dipakai cek signature) |

## 2. Credential n8n (dibuat di UI, tidak ada di JSON)

Setelah import, buka tiap node yang berlabel `REPLACE_ME` dan pilih credential:

- **StockQu Agent Service** (Header Auth): `Authorization` = `Bearer <N8N_TO_SERVICE_TOKEN>`
- **StockQu inbound** (Header Auth): `Authorization` = `Bearer <SERVICE_TO_N8N_TOKEN>`
- **Twilio WhatsApp Sandbox** (Twilio API): Account SID + Auth Token
- **Gmail StockQu** (Gmail OAuth2): scope minimum kirim email

Token dibuat sendiri (misal `python -c "import secrets; print(secrets.token_urlsafe(32))"`) dan
diisi juga di `.env` Agent Service. Jangan commit token.

## 3. Twilio WhatsApp Sandbox

1. Twilio Console -> Messaging -> Try it out -> WhatsApp. Nomor manager harus `join <kata-sandi>` dulu.
2. "When a message comes in": `https://<n8n>/webhook/stockqu-wa-inbound`, method POST.
3. Isi `TWILIO_AUTH_TOKEN` dan `APPROVER_WHATSAPP_NUMBERS` di `.env` Agent Service.
   Balasan dari nomor lain atau tanpa signature Twilio yang valid ditolak tanpa mengubah PO.

## 4. URL webhook untuk Agent Service

Setelah W2 dan W4 aktif, salin Production URL ke `.env` Agent Service:

```
N8N_WA_NOTIFY_WEBHOOK_URL=https://<n8n>/webhook/stockqu-wa-notify
N8N_PO_EMAIL_WEBHOOK_URL=https://<n8n>/webhook/stockqu-po-email
```

W2 dan W4 membalas segera (`onReceived`) supaya Agent Service tidak me-retry dan mengirim email ganda.
Kegagalan Gmail terlihat di Executions n8n; PO tetap `approved` dan bisa diproses ulang.
