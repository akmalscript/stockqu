"""Panggilan keluar Agent Service -> n8n (SRS-NF-19/21/23/25).

n8n memegang integrasi eksternal (Twilio WhatsApp, Gmail). Agent Service hanya mengirim
event JSON ke webhook n8n dengan bearer token. Ganti Twilio Sandbox ke sender produksi
cukup di workflow n8n, tanpa mengubah logika procurement (abstraction layer).

Env:
    N8N_WA_NOTIFY_WEBHOOK_URL   -> W2: kirim notifikasi approval WhatsApp
    N8N_PO_EMAIL_WEBHOOK_URL    -> W4: kirim PO via Gmail lalu callback /purchase-orders/{po}/sent
    SERVICE_TO_N8N_TOKEN        -> header Authorization: Bearer ...
"""
from __future__ import annotations

from dataclasses import dataclass

from src.config import PROVIDER_REQUEST_TIMEOUT_SECONDS, get_setting
from src.services.deployment import DeploymentConfigurationError, n8n_webhook_url
from src.utils.errors import safe_error
from src.utils.retry import call_with_retry

WA_NOTIFY = "N8N_WA_NOTIFY_WEBHOOK_URL"
PO_EMAIL = "N8N_PO_EMAIL_WEBHOOK_URL"


@dataclass
class DispatchResult:
    status: str  # sent | failed | skipped
    detail: str
    attempts: int = 0


class N8nClient:
    def __init__(self, session=None, sleep=None):
        self._session = session
        self._sleep = sleep

    @property
    def session(self):
        if self._session is None:
            import requests

            self._session = requests.Session()
        return self._session

    def post(self, url_setting: str, payload: dict) -> DispatchResult:
        token = get_setting("SERVICE_TO_N8N_TOKEN")
        if not token:
            return DispatchResult("skipped", f"{url_setting} / SERVICE_TO_N8N_TOKEN belum diisi")
        try:
            url = n8n_webhook_url(url_setting)
        except DeploymentConfigurationError as exc:
            return DispatchResult("failed", str(exc))

        attempts = {"n": 0}

        def send():
            attempts["n"] += 1
            res = self.session.post(
                url, json=payload, timeout=PROVIDER_REQUEST_TIMEOUT_SECONDS,
                headers={"Authorization": f"Bearer {token}", "Idempotency-Key": str(payload.get("idempotency_key", ""))},
            )
            if res.status_code >= 500:
                raise RuntimeError(f"n8n HTTP {res.status_code}")
            return res

        try:
            kwargs = {"sleep": self._sleep} if self._sleep else {}
            res = call_with_retry(send, **kwargs)
        except Exception as exc:  # noqa: BLE001
            return DispatchResult("failed", f"n8n tidak bisa dihubungi ({safe_error(exc)})", attempts["n"])
        if res.status_code >= 400:
            return DispatchResult("failed", f"n8n menolak (HTTP {res.status_code})", attempts["n"])
        return DispatchResult("sent", f"HTTP {res.status_code}", attempts["n"])
