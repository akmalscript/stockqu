"""Python Agent Service (FastAPI) - dipanggil n8n dan Streamlit (SRS-NF-16/18/19/23/28).

Jalankan:  python -m scripts.run_agent_service      (uvicorn, port default 8000)

Endpoint (semua kecuali /health wajib header Authorization: Bearer <N8N_TO_SERVICE_TOKEN>):
    POST /workflows/daily-run                 W1 cron 07:00 WIB / manual. Async + anti duplikat per hari.
    POST /webhooks/whatsapp-reply             W3 balasan WA dari Twilio (diteruskan n8n), signature Twilio dicek.
    POST /approvals/{code}/dashboard-decision Fallback approval dashboard (JWT user Supabase diverifikasi).
    POST /purchase-orders/{po_number}/sent    W4/W5 callback setelah Gmail terkirim -> status sent + stok in-transit.
"""
from __future__ import annotations

import json
import logging
import threading
import urllib.parse
import urllib.request
import uuid
from typing import Any, Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from src.config import get_setting
from src.services.approval import (
    APPROVER_ROLES_DASHBOARD,
    ApprovalRejected,
    parse_reply,
    require_whitelisted,
    token_matches,
    verify_twilio_signature,
)
from src.services.deployment import (
    DeploymentConfigurationError,
    assert_agent_service_production_ready,
)
from src.utils.errors import safe_error

log = logging.getLogger("stockqu.agent_service")
app = FastAPI(title="StockQu Agent Service", version="0.4.0", docs_url=None, redoc_url=None)
_runtime = None
_runtime_lock = threading.Lock()


def runtime():
    global _runtime
    with _runtime_lock:
        if _runtime is None:
            from src.orchestration.checkpoint import CheckpointNotConfigured
            from src.orchestration.runtime import get_runtime
            from src.services.supabase_client import SupabaseNotConfigured

            try:
                assert_agent_service_production_ready()
                _runtime = get_runtime()
            except (CheckpointNotConfigured, DeploymentConfigurationError, SupabaseNotConfigured) as exc:
                raise HTTPException(status_code=503, detail=f"Agent Service belum dikonfigurasi: {exc}") from exc
            except Exception as exc:
                log.error("inisialisasi runtime gagal: %s", safe_error(exc))
                raise HTTPException(status_code=503, detail="Agent Service belum siap; cek log server") from exc
        return _runtime


def set_runtime(rt) -> None:
    """Untuk test: injeksi runtime dengan fake repo/agent."""
    global _runtime
    _runtime = rt


def require_token(authorization: str | None = Header(default=None)) -> None:
    token = (authorization or "").removeprefix("Bearer ").strip()
    if not token_matches(token, "N8N_TO_SERVICE_TOKEN"):
        raise HTTPException(status_code=401, detail="unauthorized")


class DailyRunRequest(BaseModel):
    trigger: Literal["cron", "manual"] = "cron"
    requested_by: str = Field(default="n8n:w1-cron", max_length=120)
    day: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    wait: bool = False  # True hanya untuk test/CLI; n8n memakai async


class WhatsAppReply(BaseModel):
    model_config = ConfigDict(extra="allow")

    webhook_url: str = Field(default="", max_length=500)  # URL publik n8n yang dipanggil Twilio (untuk signature)
    twilio_signature: str = Field(default="", max_length=200)
    params: dict[str, Any] = Field(default_factory=dict)  # seluruh form param Twilio apa adanya (From, Body, MessageSid, ...)


class DashboardDecision(BaseModel):
    decision: Literal["approved", "rejected", "revision_requested"]
    access_token: str = Field(max_length=4000)
    nonce: str = Field(min_length=8, max_length=80)
    note: str | None = Field(default=None, max_length=500)


class SentCallback(BaseModel):
    email_message_id: str = Field(min_length=1, max_length=200)


class InventorySyncRequest(BaseModel):
    requested_by: str = Field(default="streamlit:user", max_length=120)


@app.api_route("/health", methods=["GET", "HEAD"])
def health() -> dict:
    supabase_status = "skipped"
    try:
        from src.services.supabase_client import get_client
        client = get_client(write=True)
        client.table("products").select("id").limit(1).execute()
        supabase_status = "connected"
    except Exception as exc:
        log.warning("Supabase health ping skipped or failed: %s", safe_error(exc))
        supabase_status = "unreachable"
    return {"status": "ok", "supabase": supabase_status}


@app.get("/ready")
def ready() -> dict:
    try:
        assert_agent_service_production_ready()
        runtime()
    except HTTPException:
        raise
    except DeploymentConfigurationError as exc:
        raise HTTPException(status_code=503, detail=f"Konfigurasi production belum siap: {exc}") from exc
    return {"status": "ready"}


@app.post("/inventory/sync", dependencies=[Depends(require_token)])
def inventory_sync(req: InventorySyncRequest | None = None) -> dict:
    """Sinkronisasi dua arah stok Google Sheets <-> Supabase tanpa memicu procurement workflow."""
    from datetime import datetime, timezone
    from src.services.sheets_client import SheetsClient, sheets_configured
    from src.services.supabase_client import SupabaseRepository, get_client
    from src.services.inventory_sync import sync_inventory_two_way

    if not sheets_configured():
        raise HTTPException(
            status_code=400,
            detail="Google Sheets belum dikonfigurasi di Agent Service (GOOGLE_SHEETS_SPREADSHEET_ID / GOOGLE_SERVICE_ACCOUNT_JSON belum diisi)",
        )
    try:
        client = get_client(write=True)
        repo = SupabaseRepository(read_client=client)
        sheets = SheetsClient()
        actor_id = req.requested_by if req else "agent-service:inventory-sync"
        run_id = f"agent-sync-{int(datetime.now(timezone.utc).timestamp())}"
        report = sync_inventory_two_way(
            repo,
            sheets,
            run_id=run_id,
            write_enabled=True,
            actor_id=actor_id,
            actor_role="service_role",
        )
        return {
            "status": report.status,
            "run_id": report.run_id,
            "rows_read": report.rows_read,
            "rows_written_supabase": report.rows_written_supabase,
            "rows_written_sheets": report.rows_written_sheets,
            "rows_rejected": report.rows_rejected,
            "conflicts": report.conflicts,
            "errors": report.errors,
        }
    except Exception as exc:  # noqa: BLE001
        log.error("sync inventory endpoint gagal: %s", safe_error(exc))
        raise HTTPException(status_code=500, detail=f"Gagal sinkronisasi inventori: {safe_error(exc)}") from exc


@app.post("/workflows/daily-run", dependencies=[Depends(require_token)], status_code=202)
def daily_run(req: DailyRunRequest) -> dict:
    rt = runtime()
    job, created = rt.create_job(req.trigger, req.requested_by, req.day)
    if not created:
        return {"duplicate": True, "run_id": job["run_id"], "status": job["status"]}
    if req.wait:
        out = rt.run_job(job)
        return {"duplicate": False, "run_id": job["run_id"], "status": out.get("status"),
                "approval_code": out.get("approval", {}).get("approval_code")}

    def work():
        try:
            rt.run_job(job)
        except Exception as exc:  # noqa: BLE001
            log.error("workflow %s gagal: %s", job["run_id"], safe_error(exc))

    threading.Thread(target=work, name=f"wf-{job['run_id']}", daemon=True).start()
    return {"duplicate": False, "run_id": job["run_id"], "status": "running"}


@app.post("/webhooks/whatsapp-reply", dependencies=[Depends(require_token)])
def whatsapp_reply(req: WhatsAppReply) -> dict:
    """Selalu 200 dengan reply_text supaya n8n bisa membalas WA; state hanya berubah bila semua cek lolos."""
    try:
        raw_params = dict(req.params)
        if not raw_params and req.model_extra:
            raw_params = {str(k): str(v) for k, v in req.model_extra.items() if k not in ("webhook_url", "twilio_signature")}
        verify_twilio_signature(req.webhook_url, raw_params, req.twilio_signature)
        phone = require_whitelisted(raw_params.get("From", ""))
        parsed = parse_reply(raw_params.get("Body", ""))
        sid = raw_params.get("MessageSid", "")
        if not sid:
            raise ApprovalRejected("MessageSid kosong")
    except ApprovalRejected as exc:
        return {"accepted": False, "reply_text": f"Tidak diproses: {exc}"}
    try:
        res = runtime().submit_decision(parsed.approval_code, {
            "decision": parsed.decision, "via": "whatsapp", "decided_by": phone,
            "decision_ref": f"twilio:{sid}", "approver_role": "manager_whatsapp",
        })
    except Exception as exc:  # noqa: BLE001 - mis. 'approval already decided'
        msg = "sudah diputuskan sebelumnya" if "already decided" in str(exc) else f"gagal ({safe_error(exc)})"
        return {"accepted": False, "reply_text": f"{parsed.approval_code} {msg}."}
    return {"accepted": res["status"] not in ("not_found",), "reply_text": res["reply"], "status": res["status"]}


def _send_fonnte_reply(target: str, message: str) -> bool:
    token = get_setting("FONNTE_TOKEN") or "mMae5qcWpTCYkh5HPMrV"
    if not token:
        log.warning("FONNTE_TOKEN belum dikonfigurasi, balasan WA tidak dikirim ke %s", target)
        return False
    url = "https://api.fonnte.com/send"
    clean_target = target.removeprefix("+")
    # Fonnte hanya butuh target dan message untuk pesan langsung; jangan kirim inboxid karena menyebabkan reject
    payload = {"target": clean_target, "message": message}
    data = urllib.parse.urlencode(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Authorization": token})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            resp_body = resp.read().decode("utf-8", errors="replace")
            try:
                res_json = json.loads(resp_body)
                if res_json.get("status") is True:
                    log.info("Balasan Fonnte terkirim ke %s: %s", clean_target, resp_body[:200])
                    return True
                log.error("Fonnte menolak pengiriman ke %s: %s", clean_target, resp_body)
                return False
            except Exception:
                log.info("Balasan Fonnte terkirim ke %s (status %s): %s", clean_target, resp.status, resp_body[:200])
                return True
    except Exception as exc:  # noqa: BLE001
        log.error("Gagal mengirim balasan Fonnte ke %s: %s", clean_target, safe_error(exc))
        return False


@app.api_route("/webhooks/fonnte-reply", methods=["GET", "POST"])
@app.api_route("/webhooks/fonn", methods=["GET", "POST"])
@app.api_route("/fonnte", methods=["GET", "POST"])
@app.api_route("/fonn", methods=["GET", "POST"])
async def fonnte_reply(request: Request) -> dict:
    """Webhook balasan langsung dari WhatsApp via Fonnte (GET untuk cek Fonnte, POST untuk pesan masuk)."""
    if request.method == "GET":
        return {"status": "ok", "detail": "StockQu Fonnte Webhook"}

    data: dict[str, Any] = {}
    body_bytes = await request.body()
    if body_bytes:
        try:
            data = json.loads(body_bytes.decode("utf-8", errors="replace"))
        except Exception:  # noqa: BLE001
            pass
        if not data:
            try:
                qs = urllib.parse.parse_qs(body_bytes.decode("utf-8", errors="replace"))
                data = {k: v[0] if isinstance(v, list) and len(v) == 1 else v for k, v in qs.items()}
            except Exception:  # noqa: BLE001
                pass
    if not data:
        try:
            form = await request.form()
            data = dict(form)
        except Exception:  # noqa: BLE001
            pass

    log.info("Fonnte webhook masuk: %s", data)

    # Fonnte mengirim pesan keluar kita sendiri juga sebagai webhook; abaikan jika
    # field 'status' ada (menandakan delivery report, bukan pesan masuk baru)
    if data.get("status") is not None and not data.get("message"):
        log.info("Fonnte delivery report diabaikan: status=%s", data.get("status"))
        return {"status": "ignored", "detail": "delivery report"}

    sender = str(data.get("sender") or data.get("from") or data.get("From") or "").strip()
    body = str(data.get("message") or data.get("body") or data.get("Body") or data.get("text") or "").strip()
    inboxid = str(data.get("inboxid") or data.get("id") or "")
    device = str(data.get("device") or "")

    # Abaikan pesan dari device kita sendiri (echo dari pesan keluar)
    if sender and device and sender == device:
        log.info("Fonnte echo dari device sendiri diabaikan: sender=%s", sender)
        return {"status": "ignored", "detail": "pesan dari device sendiri"}

    if not sender or not body:
        log.warning("Fonnte webhook tanpa sender/body: sender=%r body=%r data=%s", sender, body, data)
        return {"status": "ignored", "detail": "sender atau message kosong"}

    log.info("Fonnte pesan masuk: sender=%s body=%r inboxid=%s", sender, body, inboxid)

    try:
        phone = require_whitelisted(sender)
        parsed = parse_reply(body)
    except ApprovalRejected as exc:
        reply_msg = f"Tidak diproses: {exc}"
        log.warning("Fonnte approval ditolak: sender=%s reason=%s", sender, exc)
        _send_fonnte_reply(sender, reply_msg)
        return {"status": False, "reply": reply_msg}
    except Exception as exc:  # noqa: BLE001
        reply_msg = f"Format balasan tidak dikenal: {safe_error(exc)}"
        log.warning("Fonnte parse gagal: sender=%s error=%s", sender, safe_error(exc))
        _send_fonnte_reply(sender, reply_msg)
        return {"status": False, "reply": reply_msg}

    log.info("Fonnte approval valid: code=%s decision=%s by=%s", parsed.approval_code, parsed.decision, phone)

    # Respon cepat ke WhatsApp saat pesan diterima sebelum proses background/email dimulai
    action_label = {
        "approved": "disetujui, memproses pengiriman email PO...",
        "rejected": "ditolak, membatalkan PO...",
        "revision_requested": "diminta revisi, mengembalikan PO ke draft...",
    }.get(parsed.decision, "sedang diproses...")
    _send_fonnte_reply(sender, f"Pesan balasan {parsed.approval_code} {action_label}")

    # decision_ref unik per kode approval untuk mencegah collision replay dengan approval terdahulu
    ref_suffix = inboxid.strip() if inboxid.strip() else uuid.uuid4().hex[:8]
    decision_ref = f"fonnte:{parsed.approval_code}:{ref_suffix}"

    try:
        res = runtime().submit_decision(parsed.approval_code, {
            "decision": parsed.decision,
            "via": "whatsapp",
            "decided_by": phone,
            "decision_ref": decision_ref,
            "approver_role": "manager_whatsapp",
        })
        reply_msg = res["reply"]
        log.info("Fonnte approval diproses: code=%s status=%s reply=%s", parsed.approval_code, res["status"], reply_msg)
    except Exception as exc:  # noqa: BLE001 - mis. sudah disetujui sebelumnya
        msg = "sudah diputuskan sebelumnya" if "already decided" in str(exc) else f"gagal ({safe_error(exc)})"
        reply_msg = f"{parsed.approval_code} {msg}."
        log.error("Fonnte submit_decision gagal: code=%s error=%s", parsed.approval_code, safe_error(exc))

    _send_fonnte_reply(sender, reply_msg)
    return {"status": True, "reply": reply_msg}


@app.post("/approvals/{approval_code}/dashboard-decision", dependencies=[Depends(require_token)])
def dashboard_decision(approval_code: str, req: DashboardDecision) -> dict:
    from src.services.auth import _actor_from_verified_user, _auth_client, _value

    try:
        user = _value(_auth_client().auth.get_user(req.access_token), "user")
        actor = _actor_from_verified_user(user)
    except Exception as exc:
        raise HTTPException(status_code=401, detail="session user tidak valid") from exc
    if actor.role not in APPROVER_ROLES_DASHBOARD:
        raise HTTPException(status_code=403, detail=f"role {actor.role} tidak boleh approve PO")
    try:
        res = runtime().submit_decision(approval_code.upper(), {
            "decision": req.decision, "via": "dashboard", "decided_by": actor.user_id,
            "decision_ref": f"dashboard:{req.nonce}", "approver_role": actor.role, "note": req.note,
        })
    except Exception as exc:
        raise HTTPException(status_code=409, detail=f"keputusan ditolak ({safe_error(exc)})") from exc
    return res


@app.post("/purchase-orders/{po_number}/sent", dependencies=[Depends(require_token)])
def po_sent(po_number: str, req: SentCallback) -> dict:
    try:
        res = runtime().mark_sent(po_number, req.email_message_id)
    except Exception as exc:
        blocked = "not approved" in str(exc)
        raise HTTPException(status_code=409 if blocked else 500,
                            detail="PO belum approved; email/stok diblokir" if blocked else safe_error(exc)) from exc
    if res.get("status") == "not_found":
        raise HTTPException(status_code=404, detail="PO tidak ditemukan")
    return res
