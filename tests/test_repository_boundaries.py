import pytest

from src.config import MAX_SALES_HISTORY_ROWS
from src.services.supabase_client import SupabaseNotConfigured, SupabaseRepository


def test_sales_history_limit_covers_documented_dummy_seed():
    assert MAX_SALES_HISTORY_ROWS >= 27_000


def test_repository_does_not_fallback_to_unauthenticated_read_client():
    with pytest.raises(SupabaseNotConfigured):
        _ = SupabaseRepository().read
