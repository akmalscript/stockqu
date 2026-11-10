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
    assert client.head("/health").status_code == 200
    response = client.get("/ready")
    assert response.status_code == 503
    assert "Konfigurasi production belum siap" in response.json()["detail"]


def test_inventory_sync_endpoint_auth_and_behavior(monkeypatch):
    from unittest.mock import MagicMock, patch
    from src.services.inventory_sync import SyncReport

    token = "test-token-" + "x" * 30
    monkeypatch.setenv("N8N_TO_SERVICE_TOKEN", token)
    client = TestClient(app)

    # 1. Reject without token (401)
    unauthorized = client.post("/inventory/sync", json={"requested_by": "test-user"})
    assert unauthorized.status_code == 401

    # 2. Reject with wrong token (401)
    wrong_token = client.post("/inventory/sync", headers={"Authorization": "Bearer wrong-token"})
    assert wrong_token.status_code == 401

    # 3. Reject when sheets not configured (400)
    with patch("src.services.sheets_client.sheets_configured", return_value=False):
        res = client.post(
            "/inventory/sync",
            headers={"Authorization": f"Bearer {token}"},
            json={"requested_by": "test-user"},
        )
        assert res.status_code == 400
        assert "Google Sheets belum dikonfigurasi" in res.json()["detail"]

    # 4. Success when sheets configured (200 with report)
    mock_report = SyncReport(
        run_id="test-sync-1",
        status="success",
        rows_read=10,
        rows_written_supabase=2,
        rows_written_sheets=2,
        conflicts=1,
    )
    with (
        patch("src.services.sheets_client.sheets_configured", return_value=True),
        patch("src.services.supabase_client.get_client", return_value=MagicMock()),
        patch("src.services.supabase_client.SupabaseRepository", return_value=MagicMock()),
        patch("src.services.sheets_client.SheetsClient", return_value=MagicMock()),
        patch("src.services.inventory_sync.sync_inventory_two_way", return_value=mock_report),
    ):
        res = client.post(
            "/inventory/sync",
            headers={"Authorization": f"Bearer {token}"},
            json={"requested_by": "test-user"},
        )
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "success"
        assert data["rows_read"] == 10
        assert data["rows_written_supabase"] == 2
        assert data["rows_written_sheets"] == 2
        assert data["conflicts"] == 1


def test_sheets_settings_url_extraction(monkeypatch):
    from src.config import extract_spreadsheet_id, sheets_settings

    full_url = "https://docs.google.com/spreadsheets/d/1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgvE2upms/edit#gid=0"
    assert extract_spreadsheet_id(full_url) == "1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgvE2upms"

    monkeypatch.setenv("SPREADSHEET_URL", full_url)
    monkeypatch.delenv("GOOGLE_SHEETS_SPREADSHEET_ID", raising=False)
    cfg = sheets_settings()
    assert cfg["spreadsheet_id"] == "1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgvE2upms"


def test_sheets_settings_handles_streamlit_attrdict_mapping(monkeypatch):
    import json
    from collections.abc import Mapping
    import streamlit as st
    from src.config import sheets_settings
    from src.services.sheets_client import sheets_configured

    class FakeAttrDict(Mapping):
        def __init__(self, data):
            self._data = data

        def __getitem__(self, key):
            return self._data[key]

        def __iter__(self):
            return iter(self._data)

        def __len__(self):
            return len(self._data)

        def get(self, key, default=None):
            return self._data.get(key, default)

    fake_secrets = {
        "GOOGLE_SHEETS_SPREADSHEET_ID": "https://docs.google.com/spreadsheets/d/abc12345/edit",
        "gcp_service_account": FakeAttrDict({
            "type": "service_account",
            "client_email": "bot@example.iam.gserviceaccount.com",
            "private_key": "-----BEGIN PRIVATE KEY-----\\ntest\\n-----END PRIVATE KEY-----\\n",
        }),
    }
    monkeypatch.delenv("GOOGLE_SHEETS_SPREADSHEET_ID", raising=False)
    monkeypatch.delenv("SPREADSHEET_URL", raising=False)
    monkeypatch.setattr(st, "secrets", fake_secrets)
    cfg = sheets_settings()
    assert cfg["spreadsheet_id"] == "abc12345"
    assert cfg["service_account_json"] is not None
    info = json.loads(cfg["service_account_json"])
    assert info["client_email"] == "bot@example.iam.gserviceaccount.com"
    assert sheets_configured() is True
