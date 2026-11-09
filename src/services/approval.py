"""Approval human-in-the-loop (roadmap P4-18, SRS-F-40/42/63/64, NF-19/24).

Fungsi murni (mudah dites):
- ``build_approval_message``  : template 11.1 stockqu-domain, kode APR diisi DB lewat {{APPROVAL_CODE}}.
- ``parse_reply``             : "OK APR-20261109-01" / "TOLAK APR-..." / "REVISI APR-...". Kode WAJIB,
                                supaya balasan "OK" tanpa konteks tidak menyetujui PO yang salah.
- ``normalize_phone`` + ``approver_numbers``: whitelist nomor manager (E.164).
- ``verify_twilio_signature`` : X-Twilio-Signature (HMAC-SHA1, base64) -> asal pesan terverifikasi.
- ``token_matches``           : bearer token n8n <-> Agent Service (constant-time).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import re
from dataclasses import dataclass

from src.config import LABEL_NOT_FOUND, get_setting

CODE_PLACEHOLDER = "{{APPROVAL_CODE}}"
APPROVAL_TTL_HOURS = 24
DECISION_WORDS = {
    "ok": "approved", "ya": "approved", "setuju": "approved", "approve": "approved",
    "tolak": "rejected", "reject": "rejected",
    "revisi": "revision_requested", "revise": "revision_requested",
}
REPLY_RE = re.compile(r"^\s*([a-z]+)\s+(APR-\d{8}-\d{2,})\s*[.!]?\s*$", re.IGNORECASE)
APPROVER_ROLES_DASHBOARD = frozenset({"super_admin", "warehouse_manager", "procurement_manager"})


class ApprovalRejected(ValueError):
    """Permintaan approval tidak sah (nomor tidak terdaftar, format salah, signature salah)."""


def _rp(value: float) -> str:
    return f"Rp {float(value):,.0f}".replace(",", ".")


def build_approval_message(pos: list[dict], urgent: dict | None, notes: list[str] | None = None) -> dict:
    """Template WA (stockqu-domain 11.1). Semua angka dari draft PO Negotiator + forecast Analyst."""
    total = round(sum(float(p["total_amount"]) for p in pos), 2)
    items = [(p, i) for p in pos for i in p["items"]]
    if urgent:
        urgent_txt = f"{urgent['sku']} ({urgent['branch']}, habis {float(urgent['days_to_stockout']):.1f} hari)"
    else:
        urgent_txt = LABEL_NOT_FOUND
    lines = [f"Pak/Bu, butuh approval {_rp(total)} untuk {len(items)} item, paling urgent {urgent_txt}.", "Rincian:"]
    for p, i in items:
        lines.append(f"- {i['sku']} {i['qty']} unit dari {p['supplier_name']} ke {p['branch']}, {_rp(i['line_total'])}")
    lines.append(f"PO: {', '.join(p['po_number'] for p in pos)}.")
    for note in notes or []:
        lines.append(f"Catatan: {note}")
    lines.append(
        f"Balas OK {CODE_PLACEHOLDER} untuk kirim PO, TOLAK {CODE_PLACEHOLDER} untuk menolak, "
        f"atau REVISI {CODE_PLACEHOLDER}. Berlaku {APPROVAL_TTL_HOURS} jam."
    )
    return {"template": "\n".join(lines), "total": total, "item_count": len(items),
            "most_urgent": urgent_txt}


@dataclass(frozen=True)
class ParsedReply:
    decision: str
    approval_code: str


def parse_reply(text: str) -> ParsedReply:
    m = REPLY_RE.match(text or "")
    if not m:
        raise ApprovalRejected("Format balasan: OK APR-YYYYMMDD-NN, TOLAK APR-YYYYMMDD-NN, atau REVISI APR-YYYYMMDD-NN")
    decision = DECISION_WORDS.get(m.group(1).lower())
    if decision is None:
        raise ApprovalRejected(f"Kata keputusan '{m.group(1)}' tidak dikenal")
    return ParsedReply(decision=decision, approval_code=m.group(2).upper())


def normalize_phone(raw: str) -> str:
    """'whatsapp:+62 812-3456' / '0812...' -> '+62812...'."""
    value = (raw or "").strip().lower().removeprefix("whatsapp:")
    digits = re.sub(r"[^\d+]", "", value)
    if digits.startswith("00"):
        digits = "+" + digits[2:]
    elif digits.startswith("0"):
        digits = "+62" + digits[1:]
    elif digits and not digits.startswith("+"):
        digits = "+" + digits
    return digits


def approver_numbers() -> list[str]:
    raw = get_setting("APPROVER_WHATSAPP_NUMBERS") or ""
    return [n for n in (normalize_phone(x) for x in raw.split(",")) if len(n) > 5]


def require_whitelisted(sender: str) -> str:
    phone = normalize_phone(sender)
    if phone not in approver_numbers():
        raise ApprovalRejected("Nomor pengirim tidak terdaftar sebagai approver")
    return phone


def twilio_signature(auth_token: str, url: str, params: dict[str, str]) -> str:
    payload = url + "".join(f"{k}{params[k]}" for k in sorted(params))
    digest = hmac.new(auth_token.encode(), payload.encode(), hashlib.sha1).digest()
    return base64.b64encode(digest).decode()


def verify_twilio_signature(url: str, params: dict[str, str], signature: str, auth_token: str | None = None) -> None:
    token = auth_token or get_setting("TWILIO_AUTH_TOKEN")
    if not token:
        if (get_setting("STOCKQU_ALLOW_UNSIGNED_TWILIO") or "") == "1":
            return  # khusus pengembangan lokal; JANGAN aktif di deploy
        raise ApprovalRejected("TWILIO_AUTH_TOKEN belum dikonfigurasi; webhook WhatsApp ditolak")
    expected = twilio_signature(token, url, {k: str(v) for k, v in params.items()})
    if not signature or not hmac.compare_digest(expected, signature):
        raise ApprovalRejected("Signature Twilio tidak valid")


def token_matches(provided: str | None, setting_name: str) -> bool:
    expected = get_setting(setting_name)
    if not expected or not provided:
        return False  # fail closed
    return hmac.compare_digest(expected.encode(), provided.encode())
