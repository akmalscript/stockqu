"""Seed satu user demo ke Supabase lokal melalui Auth Admin API.

Script ini sengaja menolak URL hosted dan tidak menyimpan password atau service
key ke repository. Jalankan setelah ``supabase start`` dengan kredensial lokal
dari ``supabase status -o env``.
"""
from __future__ import annotations

import argparse
import os
from urllib.parse import urlparse

from supabase import create_client

ALLOWED_LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
ALLOWED_ROLES = frozenset(
    {
        "super_admin",
        "warehouse_manager",
        "purchasing_staff",
        "procurement_manager",
    }
)


def require_local_supabase_url(url: str) -> str:
    """Tolak project hosted supaya akun demo tidak masuk ke production."""
    parsed = urlparse(url)
    if parsed.scheme != "http" or parsed.hostname not in ALLOWED_LOCAL_HOSTS:
        raise ValueError("Seed demo hanya boleh diarahkan ke Supabase lokal via HTTP loopback")
    if not parsed.port:
        raise ValueError("URL Supabase lokal wajib menyertakan port")
    return url.rstrip("/")


def demo_user_attributes(
    email: str,
    password: str,
    role: str,
    branch_ids: list[str] | None = None,
) -> dict:
    """Bangun atribut Auth; otorisasi hanya disimpan di app_metadata."""
    clean_email = email.strip().lower()
    if "@" not in clean_email:
        raise ValueError("Email demo tidak valid")
    if len(password) < 16:
        raise ValueError("Password demo minimal 16 karakter")
    if role not in ALLOWED_ROLES:
        raise ValueError(f"Role demo tidak didukung: {role}")
    return {
        "email": clean_email,
        "password": password,
        "email_confirm": True,
        "app_metadata": {
            "role": role,
            "branch_ids": list(branch_ids or []),
        },
    }


def seed_demo_user(
    url: str,
    service_key: str,
    email: str,
    password: str,
    role: str = "super_admin",
    branch_ids: list[str] | None = None,
) -> tuple[str, str]:
    """Create atau refresh user demo lokal secara idempoten."""
    local_url = require_local_supabase_url(url)
    if not service_key.strip():
        raise ValueError("SUPABASE_SERVICE_ROLE_KEY lokal wajib diisi")
    attributes = demo_user_attributes(email, password, role, branch_ids)
    client = create_client(local_url, service_key)
    existing = next(
        (
            user
            for user in client.auth.admin.list_users(per_page=1000)
            if (user.email or "").lower() == attributes["email"]
        ),
        None,
    )
    if existing:
        response = client.auth.admin.update_user_by_id(existing.id, attributes)
        action = "updated"
    else:
        response = client.auth.admin.create_user(attributes)
        action = "created"
    user = response.user
    if user is None:
        raise RuntimeError("Supabase Auth tidak mengembalikan user")
    return action, str(user.id)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Seed user demo hanya ke Supabase lokal")
    parser.add_argument("--email", default=os.getenv("STOCKQU_DEMO_EMAIL", "demo@stockqu.local"))
    parser.add_argument("--role", default=os.getenv("STOCKQU_DEMO_ROLE", "super_admin"))
    return parser


def main() -> int:
    args = _parser().parse_args()
    url = os.getenv("SUPABASE_URL", "")
    service_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
    password = os.getenv("STOCKQU_DEMO_PASSWORD", "")
    if not password:
        raise SystemExit("STOCKQU_DEMO_PASSWORD wajib diisi; password tidak boleh di-hardcode")
    action, user_id = seed_demo_user(
        url=url,
        service_key=service_key,
        email=args.email,
        password=password,
        role=args.role,
    )
    print(f"Demo user {action}: {args.email} ({args.role}, id={user_id})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
