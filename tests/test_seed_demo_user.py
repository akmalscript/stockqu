import secrets

import pytest

from scripts.seed_demo_user import demo_user_attributes, require_local_supabase_url


def test_seed_demo_user_rejects_hosted_project():
    with pytest.raises(ValueError, match="Supabase lokal"):
        require_local_supabase_url("https://example.supabase.co")


def test_seed_demo_user_accepts_loopback_with_port():
    assert require_local_supabase_url("http://127.0.0.1:54321/") == "http://127.0.0.1:54321"


def test_demo_user_authorization_uses_app_metadata():
    password = f"LocalDemo-{secrets.token_urlsafe(18)}!"
    attributes = demo_user_attributes(
        "Demo@StockQu.Local",
        password,
        "super_admin",
    )

    assert attributes["email"] == "demo@stockqu.local"
    assert attributes["app_metadata"] == {"role": "super_admin", "branch_ids": []}
    assert "user_metadata" not in attributes


def test_demo_user_requires_strong_password():
    short_password = secrets.token_urlsafe(4)
    with pytest.raises(ValueError, match="minimal 16"):
        demo_user_attributes("demo@stockqu.local", short_password, "super_admin")
