"""Validasi konfigurasi deployment tanpa pernah mencetak nilai secret."""
from __future__ import annotations

import ipaddress
from urllib.parse import urlparse

from src.config import get_setting

PRODUCTION_NAMES = frozenset({"production", "prod"})
BLOCKED_HOSTS = frozenset({"localhost", "host.docker.internal"})
BLOCKED_SUFFIXES = (".localhost", ".local", ".test", ".invalid", ".example", ".example.com")


class DeploymentConfigurationError(RuntimeError):
    pass


def is_production() -> bool:
    return (get_setting("STOCKQU_ENV", "development") or "development").strip().lower() in PRODUCTION_NAMES


def public_https_url(setting: str, value: str | None = None) -> str:
    """Kembalikan URL HTTPS publik atau raise tanpa membocorkan nilai URL."""
    raw = (value if value is not None else get_setting(setting)) or ""
    raw = raw.strip()
    parsed = urlparse(raw)
    host = (parsed.hostname or "").lower().rstrip(".")
    if parsed.scheme != "https" or not host or parsed.username or parsed.password:
        raise DeploymentConfigurationError(f"{setting} wajib berupa URL HTTPS publik")
    if host in BLOCKED_HOSTS or host.endswith(BLOCKED_SUFFIXES) or "." not in host:
        raise DeploymentConfigurationError(f"{setting} masih memakai host lokal/placeholder")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is not None and not address.is_global:
        raise DeploymentConfigurationError(f"{setting} memakai alamat IP non-publik")
    return raw.rstrip("/")


def agent_service_base_url() -> str:
    raw = (get_setting("STOCKQU_AGENT_SERVICE_URL") or "").strip()
    if not raw:
        raise DeploymentConfigurationError("STOCKQU_AGENT_SERVICE_URL belum diisi")
    return public_https_url("STOCKQU_AGENT_SERVICE_URL", raw) if is_production() else raw.rstrip("/")


def n8n_webhook_url(setting: str) -> str:
    raw = (get_setting(setting) or "").strip()
    if not raw:
        raise DeploymentConfigurationError(f"{setting} belum diisi")
    if is_production():
        return public_https_url(setting, raw)
    if raw.startswith("https://"):
        return raw
    if raw.startswith("http://") and (get_setting("STOCKQU_ALLOW_HTTP_N8N") or "") == "1":
        return raw
    raise DeploymentConfigurationError("Webhook n8n wajib HTTPS")


def _missing(*names: str) -> list[str]:
    return [f"{name} belum diisi" for name in names if not (get_setting(name) or "").strip()]


def _url_error(name: str) -> list[str]:
    try:
        public_https_url(name)
    except DeploymentConfigurationError as exc:
        return [str(exc)]
    return []


def _token_errors() -> list[str]:
    errors: list[str] = []
    first = (get_setting("N8N_TO_SERVICE_TOKEN") or "").strip()
    second = (get_setting("SERVICE_TO_N8N_TOKEN") or "").strip()
    for name, value in (("N8N_TO_SERVICE_TOKEN", first), ("SERVICE_TO_N8N_TOKEN", second)):
        if value and len(value) < 32:
            errors.append(f"{name} minimal 32 karakter pada production")
    if first and second and first == second:
        errors.append("N8N_TO_SERVICE_TOKEN dan SERVICE_TO_N8N_TOKEN wajib berbeda")
    return errors


def streamlit_production_errors() -> list[str]:
    if not is_production():
        return []
    errors = _missing("SUPABASE_URL", "SUPABASE_ANON_KEY", "STOCKQU_AGENT_SERVICE_URL", "N8N_TO_SERVICE_TOKEN")
    errors.extend(_url_error("SUPABASE_URL"))
    errors.extend(_url_error("STOCKQU_AGENT_SERVICE_URL"))
    token = (get_setting("N8N_TO_SERVICE_TOKEN") or "").strip()
    if token and len(token) < 32:
        errors.append("N8N_TO_SERVICE_TOKEN minimal 32 karakter pada production")
    if get_setting("SUPABASE_SERVICE_ROLE_KEY"):
        errors.append("SUPABASE_SERVICE_ROLE_KEY tidak boleh tersedia pada process Streamlit")
    return sorted(set(errors))


def agent_service_production_errors() -> list[str]:
    if not is_production():
        return []
    errors = _missing(
        "SUPABASE_URL",
        "SUPABASE_ANON_KEY",
        "SUPABASE_SERVICE_ROLE_KEY",
        "SUPABASE_DB_URL",
        "STOCKQU_AGENT_SERVICE_URL",
        "AGENT_SERVICE_PUBLIC_URL",
        "N8N_TO_SERVICE_TOKEN",
        "SERVICE_TO_N8N_TOKEN",
        "N8N_WA_NOTIFY_WEBHOOK_URL",
        "N8N_PO_EMAIL_WEBHOOK_URL",
        "APPROVER_WHATSAPP_NUMBERS",
        "TWILIO_AUTH_TOKEN",
        "GOOGLE_SHEETS_SPREADSHEET_ID",
    )
    if not ((get_setting("GOOGLE_SERVICE_ACCOUNT_FILE") or "").strip() or
            (get_setting("GOOGLE_SERVICE_ACCOUNT_JSON") or "").strip()):
        errors.append("GOOGLE_SERVICE_ACCOUNT_FILE atau GOOGLE_SERVICE_ACCOUNT_JSON wajib diisi")
    for name in (
        "SUPABASE_URL",
        "STOCKQU_AGENT_SERVICE_URL",
        "AGENT_SERVICE_PUBLIC_URL",
        "N8N_WA_NOTIFY_WEBHOOK_URL",
        "N8N_PO_EMAIL_WEBHOOK_URL",
    ):
        errors.extend(_url_error(name))
    db_url = (get_setting("SUPABASE_DB_URL") or "").strip()
    parsed_db = urlparse(db_url)
    if db_url and (parsed_db.scheme not in {"postgres", "postgresql"} or not parsed_db.hostname):
        errors.append("SUPABASE_DB_URL wajib berupa connection string Postgres")
    if db_url and parsed_db.hostname:
        try:
            address = ipaddress.ip_address(parsed_db.hostname)
        except ValueError:
            address = None
        if parsed_db.hostname in BLOCKED_HOSTS or (address is not None and not address.is_global):
            errors.append("SUPABASE_DB_URL production tidak boleh menunjuk database lokal")
    errors.extend(_token_errors())
    public_agent = (get_setting("AGENT_SERVICE_PUBLIC_URL") or "").rstrip("/")
    client_agent = (get_setting("STOCKQU_AGENT_SERVICE_URL") or "").rstrip("/")
    if public_agent and client_agent and public_agent != client_agent:
        errors.append("STOCKQU_AGENT_SERVICE_URL dan AGENT_SERVICE_PUBLIC_URL wajib sama")
    if (get_setting("STOCKQU_ALLOW_HTTP_N8N") or "") == "1":
        errors.append("STOCKQU_ALLOW_HTTP_N8N wajib 0 pada production")
    expected_paths = {
        "N8N_WA_NOTIFY_WEBHOOK_URL": "/webhook/stockqu-wa-notify",
        "N8N_PO_EMAIL_WEBHOOK_URL": "/webhook/stockqu-po-email",
    }
    for name, expected in expected_paths.items():
        value = (get_setting(name) or "").strip()
        if value and urlparse(value).path.rstrip("/") != expected:
            errors.append(f"{name} wajib berakhir dengan {expected}")
    return sorted(set(errors))


def assert_streamlit_production_ready() -> None:
    errors = streamlit_production_errors()
    if errors:
        raise DeploymentConfigurationError("; ".join(errors))


def assert_agent_service_production_ready() -> None:
    errors = agent_service_production_errors()
    if errors:
        raise DeploymentConfigurationError("; ".join(errors))
