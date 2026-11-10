import pytest
from fastapi.testclient import TestClient

from src.service.api import app
from src.services import agent_service_client
from src.services.deployment import (
    DeploymentConfigurationError,
    agent_service_production_errors,
    public_https_url,
    streamlit_production_errors,
)
from src.services.n8n_client import N8nClient


def test_public_url_rejects_local_private_and_placeholder_hosts():
    for value in (
        "http://agent.stockqu.co",
        "https://localhost:8000",
        "https://host.docker.internal:8000",
        "https://127.0.0.1:8000",
        "https://10.0.0.5",
        "https://agent.stockqu.invalid",
    ):
        with pytest.raises(DeploymentConfigurationError):
            public_https_url("SERVICE_URL", value)

    assert public_https_url("SERVICE_URL", "https://agent.stockqu.co/") == "https://agent.stockqu.co"


def test_streamlit_production_rejects_service_role_and_accepts_public_agent(monkeypatch):
    values = {
        "STOCKQU_ENV": "production",
        "SUPABASE_URL": "https://project.supabase.co",
        "SUPABASE_ANON_KEY": "publishable-value",
        "SUPABASE_SERVICE_ROLE_KEY": "",
        "STOCKQU_AGENT_SERVICE_URL": "https://agent.stockqu.co",
        "N8N_TO_SERVICE_TOKEN": "a" * 40,
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    assert streamlit_production_errors() == []

    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "must-stay-server-side")
    assert any("tidak boleh tersedia" in error for error in streamlit_production_errors())


def _set_valid_agent_production(monkeypatch):
    values = {
        "STOCKQU_ENV": "production",
        "STOCKQU_ALLOW_HTTP_N8N": "0",
        "SUPABASE_URL": "https://project.supabase.co",
        "SUPABASE_ANON_KEY": "publishable-value",
        "SUPABASE_SERVICE_ROLE_KEY": "server-secret-value",
        "SUPABASE_DB_URL": "postgresql://postgres:password@db.stockqu.co:5432/postgres",
        "STOCKQU_AGENT_SERVICE_URL": "https://agent.stockqu.co",
        "AGENT_SERVICE_PUBLIC_URL": "https://agent.stockqu.co",
        "N8N_TO_SERVICE_TOKEN": "a" * 40,
        "SERVICE_TO_N8N_TOKEN": "b" * 40,
        "N8N_WA_NOTIFY_WEBHOOK_URL": "https://n8n.stockqu.co/webhook/stockqu-wa-notify",
        "N8N_PO_EMAIL_WEBHOOK_URL": "https://n8n.stockqu.co/webhook/stockqu-po-email",
        "APPROVER_WHATSAPP_NUMBERS": "+6281200000001",
        "TWILIO_AUTH_TOKEN": "twilio-secret-value",
        "GOOGLE_SHEETS_SPREADSHEET_ID": "sheet-id",
        "GOOGLE_SERVICE_ACCOUNT_FILE": "",
        "GOOGLE_SERVICE_ACCOUNT_JSON": '{"type":"service_account"}',
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)


def test_agent_service_production_requires_consistent_public_urls(monkeypatch):
    _set_valid_agent_production(monkeypatch)
    assert agent_service_production_errors() == []

    monkeypatch.setenv("AGENT_SERVICE_PUBLIC_URL", "https://callback.stockqu.co")
    errors = agent_service_production_errors()
    assert any("wajib sama" in error for error in errors)


def test_production_never_allows_http_n8n_bypass(monkeypatch):
    class NoNetwork:
        def post(self, *_args, **_kwargs):
            raise AssertionError("network must not be called for an invalid production URL")

    monkeypatch.setenv("STOCKQU_ENV", "production")
    monkeypatch.setenv("STOCKQU_ALLOW_HTTP_N8N", "1")
    monkeypatch.setenv("SERVICE_TO_N8N_TOKEN", "b" * 40)
    monkeypatch.setenv("N8N_WA_NOTIFY_WEBHOOK_URL", "http://localhost:5678/webhook/stockqu-wa-notify")
    result = N8nClient(session=NoNetwork()).post("N8N_WA_NOTIFY_WEBHOOK_URL", {"idempotency_key": "test"})
    assert result.status == "failed"
    assert "HTTPS publik" in result.detail


def test_streamlit_client_is_not_configured_with_local_production_url(monkeypatch):
    monkeypatch.setenv("STOCKQU_ENV", "production")
    monkeypatch.setenv("STOCKQU_AGENT_SERVICE_URL", "https://localhost:8000")
    monkeypatch.setenv("N8N_TO_SERVICE_TOKEN", "a" * 40)
    assert agent_service_client.configured() is False


def test_agent_service_health_is_live_but_readiness_rejects_bad_production(monkeypatch):
    monkeypatch.setenv("STOCKQU_ENV", "production")
    monkeypatch.setenv("STOCKQU_AGENT_SERVICE_URL", "https://localhost:8000")
    client = TestClient(app)

    assert client.get("/health").status_code == 200
    response = client.get("/ready")
    assert response.status_code == 503
    assert "Konfigurasi production belum siap" in response.json()["detail"]
