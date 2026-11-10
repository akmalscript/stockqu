"""Client HTTP Streamlit -> Python Agent Service (SRS-NF-16: UI tidak menjalankan workflow sendiri)."""
from __future__ import annotations

import uuid

from src.config import PROVIDER_REQUEST_TIMEOUT_SECONDS, get_setting
from src.services.deployment import DeploymentConfigurationError, agent_service_base_url


class AgentServiceUnavailable(RuntimeError):
    pass


def configured() -> bool:
    try:
        agent_service_base_url()
    except DeploymentConfigurationError:
        return False
    return bool(get_setting("N8N_TO_SERVICE_TOKEN"))


def _post(path: str, payload: dict) -> dict:
    import requests

    token = get_setting("N8N_TO_SERVICE_TOKEN")
    if not token:
        raise AgentServiceUnavailable("STOCKQU_AGENT_SERVICE_URL / N8N_TO_SERVICE_TOKEN belum diisi")
    try:
        base = agent_service_base_url()
    except DeploymentConfigurationError as exc:
        raise AgentServiceUnavailable(str(exc)) from exc
    try:
        res = requests.post(f"{base}{path}", json=payload, timeout=PROVIDER_REQUEST_TIMEOUT_SECONDS,
                            headers={"Authorization": f"Bearer {token}"})
    except requests.RequestException as exc:
        raise AgentServiceUnavailable("Agent Service tidak bisa dihubungi") from exc
    if res.status_code >= 400:
        try:
            detail = res.json().get("detail", "")
        except ValueError:
            detail = ""
        raise AgentServiceUnavailable(f"Agent Service menolak (HTTP {res.status_code}) {detail}".strip())
    return res.json()


def trigger_manual_run(requested_by: str) -> dict:
    return _post("/workflows/daily-run", {"trigger": "manual", "requested_by": requested_by[:120]})


def trigger_inventory_sync(requested_by: str) -> dict:
    return _post("/inventory/sync", {"requested_by": requested_by[:120]})


def submit_dashboard_decision(approval_code: str, decision: str, access_token: str, note: str | None) -> dict:
    return _post(f"/approvals/{approval_code}/dashboard-decision", {
        "decision": decision, "access_token": access_token, "nonce": uuid.uuid4().hex, "note": note or None,
    })
