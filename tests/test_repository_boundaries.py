from pathlib import Path

import pytest

from src.config import MAX_SALES_HISTORY_ROWS
from src.services.supabase_client import SupabaseNotConfigured, SupabaseRepository

ROOT = Path(__file__).resolve().parents[1]


def test_sales_history_limit_covers_documented_dummy_seed():
    assert MAX_SALES_HISTORY_ROWS >= 27_000


def test_repository_does_not_fallback_to_unauthenticated_read_client():
    with pytest.raises(SupabaseNotConfigured):
        _ = SupabaseRepository().read


def test_production_templates_do_not_contain_secret_values():
    paths = [
        ROOT / ".env.production.example",
        ROOT / ".streamlit/secrets.toml.example",
        ROOT / "n8n/.env.production.example",
    ]
    for path in paths:
        raw = path.read_text().lower()
        assert "sb_secret_" not in raw
        assert "begin private key" not in raw
        assert "localhost" not in raw
