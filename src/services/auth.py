"""Supabase Auth + RBAC untuk halaman dan trigger StockQu.

Role dan scope cabang diambil hanya dari ``app_metadata`` Supabase Auth. Nilai
``user_metadata`` sengaja tidak pernah dipakai untuk otorisasi karena dapat
diubah oleh user. Client baca memakai JWT user agar RLS tetap menjadi boundary
terakhir; service-role tetap hanya dipakai server untuk write agent yang sudah
melewati pemeriksaan actor/action.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from src.config import PROVIDER_REQUEST_TIMEOUT_SECONDS, get_setting

ROLE_SUPER_ADMIN = "super_admin"
ROLE_WAREHOUSE_MANAGER = "warehouse_manager"
ROLE_PURCHASING_STAFF = "purchasing_staff"
ROLE_PROCUREMENT_MANAGER = "procurement_manager"
ROLE_SYSTEM_AGENT = "system_agent"

ALL_USER_ROLES = frozenset(
    {
        ROLE_SUPER_ADMIN,
        ROLE_WAREHOUSE_MANAGER,
        ROLE_PURCHASING_STAFF,
        ROLE_PROCUREMENT_MANAGER,
    }
)
ANALYST_VIEW_ROLES = ALL_USER_ROLES
# Analyst menulis snapshot Sheets global. Trigger browser dibatasi ke Super Admin
# agar user bercakupan cabang tidak dapat menimpa data cabang lain lewat service
# account; cron/CLI tetap masuk sebagai system_agent tepercaya.
ANALYST_RUN_ROLES = frozenset({ROLE_SUPER_ADMIN})
PURCHASER_VIEW_ROLES = frozenset(
    {ROLE_SUPER_ADMIN, ROLE_PURCHASING_STAFF, ROLE_PROCUREMENT_MANAGER}
)
PURCHASER_RUN_ROLES = PURCHASER_VIEW_ROLES
# P3: Negotiator membaca plan + kontrak dan membuat draft PO (belum dikirim).
NEGOTIATOR_VIEW_ROLES = PURCHASER_VIEW_ROLES
NEGOTIATOR_RUN_ROLES = PURCHASER_VIEW_ROLES
# F-04: hanya admin yang mengelola dokumen kontrak supplier.
SUPPLIER_DOCUMENT_ADMIN_ROLES = frozenset({ROLE_SUPER_ADMIN})


class AuthenticationRequired(PermissionError):
    """Tidak ada session user terverifikasi."""


class AuthorizationDenied(PermissionError):
    """Actor terverifikasi tetapi role-nya tidak diizinkan."""


@dataclass(frozen=True)
class ActorContext:
    user_id: str
    email: str
    role: str
    branch_ids: tuple[str, ...] = ()
    source: str = "supabase_auth"

    @property
    def is_system(self) -> bool:
        return self.role == ROLE_SYSTEM_AGENT

    def audit_fields(self) -> dict[str, str]:
        return {"actor_id": self.user_id, "actor_role": self.role}


def system_actor(name: str) -> ActorContext:
    """Actor internal untuk CLI/cron tepercaya, bukan browser visitor."""
    safe_name = "".join(ch for ch in name.lower() if ch.isalnum() or ch in "-_:")[:80]
    if not safe_name:
        raise ValueError("Nama system actor wajib diisi")
    return ActorContext(
        user_id=f"system:{safe_name}",
        email="",
        role=ROLE_SYSTEM_AGENT,
        source="trusted_service",
    )


def authorize(actor: ActorContext | None, allowed_roles: Iterable[str], action: str) -> ActorContext:
    if actor is None:
        raise AuthenticationRequired(f"Login diperlukan untuk {action}")
    allowed = frozenset(allowed_roles)
    if actor.is_system:
        if actor.source != "trusted_service" or not actor.user_id.startswith("system:"):
            raise AuthorizationDenied("System actor tidak berasal dari trusted service")
        return actor
    if actor.role not in allowed:
        raise AuthorizationDenied(f"Role '{actor.role}' tidak boleh {action}")
    return actor


def _auth_client():
    from supabase import ClientOptions, create_client

    url = get_setting("SUPABASE_URL")
    key = get_setting("SUPABASE_ANON_KEY")
    if not url or not key:
        raise AuthenticationRequired("SUPABASE_URL / SUPABASE_ANON_KEY belum dikonfigurasi")
    return create_client(
        url,
        key,
        options=ClientOptions(
            postgrest_client_timeout=PROVIDER_REQUEST_TIMEOUT_SECONDS,
            storage_client_timeout=PROVIDER_REQUEST_TIMEOUT_SECONDS,
            function_client_timeout=PROVIDER_REQUEST_TIMEOUT_SECONDS,
        ),
    )


def _value(obj: Any, name: str, default=None):
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _actor_from_verified_user(user: Any) -> ActorContext:
    metadata = _value(user, "app_metadata", {}) or {}
    role = str(metadata.get("role") or "").strip().lower()
    if role not in ALL_USER_ROLES:
        raise AuthorizationDenied("Role StockQu belum ditetapkan di Supabase app_metadata")
    raw_branches = metadata.get("branch_ids") or []
    if not isinstance(raw_branches, list):
        raise AuthorizationDenied("app_metadata.branch_ids harus berupa list")
    return ActorContext(
        user_id=str(_value(user, "id", "")),
        email=str(_value(user, "email", "") or ""),
        role=role,
        branch_ids=tuple(str(branch_id) for branch_id in raw_branches),
    )


def sign_in(email: str, password: str) -> tuple[ActorContext, dict[str, str], Any]:
    """Login Supabase dan verifikasi user melalui Auth server."""
    client = _auth_client()
    response = client.auth.sign_in_with_password({"email": email, "password": password})
    session = _value(response, "session")
    if session is None:
        raise AuthenticationRequired("Login gagal")
    access_token = str(_value(session, "access_token", ""))
    refresh_token = str(_value(session, "refresh_token", ""))
    if not access_token or not refresh_token:
        raise AuthenticationRequired("Supabase tidak mengembalikan session lengkap")
    verified = client.auth.get_user(access_token)
    actor = _actor_from_verified_user(_value(verified, "user"))
    return actor, {"access_token": access_token, "refresh_token": refresh_token}, client


def restore_session(tokens: dict[str, str]) -> tuple[ActorContext, dict[str, str], Any]:
    """Pulihkan session dan selalu verifikasi user; jangan percaya payload session lokal."""
    access_token = str(tokens.get("access_token") or "")
    refresh_token = str(tokens.get("refresh_token") or "")
    if not access_token and not refresh_token:
        raise AuthenticationRequired("Session tidak lengkap")
    client = _auth_client()
    session = None
    if access_token and refresh_token:
        try:
            refreshed = client.auth.set_session(access_token, refresh_token)
            session = _value(refreshed, "session")
        except Exception:
            session = None
    if session is None and refresh_token:
        refreshed = client.auth.refresh_session(refresh_token)
        session = _value(refreshed, "session")
    if session is None:
        raise AuthenticationRequired("Sesi Supabase tidak valid atau kedaluwarsa")
    current_access = str(_value(session, "access_token", access_token))
    current_refresh = str(_value(session, "refresh_token", refresh_token))
    if not current_access:
        raise AuthenticationRequired("Token akses Supabase kosong")
    verified = client.auth.get_user(current_access)
    actor = _actor_from_verified_user(_value(verified, "user"))
    return actor, {"access_token": current_access, "refresh_token": current_refresh}, client


def _session_cipher():
    import base64
    import hashlib
    from cryptography.fernet import Fernet

    secret = (
        get_setting("STOCKQU_SECRET_KEY")
        or get_setting("SUPABASE_SERVICE_ROLE_KEY")
        or get_setting("SUPABASE_ANON_KEY")
        or "stockqu-auth-session-key"
    )
    key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode("utf-8")).digest())
    return Fernet(key)


def serialize_session(tokens: dict[str, str]) -> str:
    import json

    cipher = _session_cipher()
    payload = json.dumps(tokens).encode("utf-8")
    return cipher.encrypt(payload).decode("utf-8")


def deserialize_session(token_str: str, max_age_seconds: int = 86400 * 7) -> dict[str, str] | None:
    import json
    from cryptography.fernet import InvalidToken

    if not token_str or not isinstance(token_str, str):
        return None
    cipher = _session_cipher()
    try:
        raw = cipher.decrypt(token_str.encode("utf-8"), ttl=max_age_seconds)
        data = json.loads(raw.decode("utf-8"))
        if isinstance(data, dict) and (data.get("access_token") or data.get("refresh_token")):
            return data
    except (InvalidToken, Exception):
        return None
    return None


def require_streamlit_access(allowed_roles: Iterable[str], action: str):
    """Render login/logout dan hentikan halaman sebelum data sensitif dimuat."""
    import streamlit as st

    state_key = "stockqu_auth_tokens"
    param_key = "_auth"
    tokens = st.session_state.get(state_key)
    if not tokens:
        query_token = st.query_params.get(param_key)
        if query_token:
            tokens = deserialize_session(query_token)

    actor = None
    client = None
    if tokens:
        try:
            actor, refreshed, client = restore_session(tokens)
            st.session_state[state_key] = refreshed
            token_str = serialize_session(refreshed)
            st.query_params[param_key] = token_str
            st.html(f"<script>try {{ localStorage.setItem('stockqu_auth', '{token_str}'); }} catch(e) {{}}</script>")
        except Exception:  # noqa: BLE001 - session invalid harus kembali ke login
            st.session_state.pop(state_key, None)
            st.query_params.pop(param_key, None)
            st.html("<script>try { localStorage.removeItem('stockqu_auth'); } catch(e) {}</script>")

    if actor is None:
        st.html(
            """
            <style>
            /* Sembunyikan panel sidebar di halaman login, tapi pertahankan header & logo */
            [data-testid="stSidebar"] {
                display: none !important;
                width: 0 !important;
                min-width: 0 !important;
            }
            /* Sembunyikan hanya tombol panah expand/collapse, bukan logo */
            [data-testid="collapsedControl"] button,
            [data-testid="stExpandSidebarButton"],
            [data-testid="stSidebarCollapseButton"] {
                display: none !important;
            }
            /* Pastikan header dan logo di header tetap terlihat */
            [data-testid="stHeader"],
            .stAppHeader,
            [data-testid="collapsedControl"],
            [data-testid="stLogo"],
            [data-testid="stHeaderLogo"],
            [data-testid="stLogoLink"],
            img.stLogo {
                display: inline-flex !important;
                visibility: visible !important;
                opacity: 1 !important;
            }
            /* Pastikan konten utama full-width tanpa margin sidebar */
            [data-testid="stAppViewContainer"],
            .main,
            [data-testid="stMain"] {
                margin-left: 0 !important;
            }
            </style>
            <script>
            (function() {
                try {
                    const saved = localStorage.getItem('stockqu_auth');
                    if (saved && !window.location.search.includes('_auth=')) {
                        const url = new URL(window.location.href);
                        url.searchParams.set('_auth', saved);
                        window.location.replace(url.toString());
                    }
                } catch (e) {}

                // Jika sidebar masih berstatus terbuka di state Streamlit, collapse agar logo berpindah ke header
                try {
                    const doc = window.parent.document;
                    const collapseBtn = doc.querySelector('[data-testid="stSidebarCollapseButton"] button')
                        || doc.querySelector('button[data-testid="stSidebarCollapseButton"]');
                    if (collapseBtn) {
                        collapseBtn.click();
                    }
                } catch (e) {}
            })();
            </script>
            """
        )
        st.markdown("<div style='height: 3.2rem;'></div>", unsafe_allow_html=True)
        _, col_login, _ = st.columns([1, 1.2, 1])
        with col_login:
            with st.container(border=True):
                st.markdown(
                    """
                    <style>
                    .login-heading {
                        font-size: 2.5rem !important;
                        font-weight: 700 !important;
                        line-height: 1.25 !important;
                        color: #000000 !important;
                        margin-top: 0 !important;
                        margin-bottom: 0.25rem !important;
                        text-align: center !important;
                    }
                    .login-heading-stock {
                        color: #000000 !important;
                    }
                    .login-heading-qu {
                        color: #1e7e5c !important;
                    }
                    </style>
                    <div class="login-heading" style="color: #000000 !important; font-size: 1.75rem; font-weight: 700; margin-top: 0; margin-bottom: 0.25rem; text-align: center;">
                        <span class="login-heading-stock" style="color: #000000 !important;">Stock</span><span class="login-heading-qu" style="color: #1e7e5c !important;">Qu</span>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )
                st.markdown(
                    "<p style='text-align: center; font-size: 0.85rem; color: rgba(49,51,63,0.6); "
                    "margin-bottom: 0;'>"
                    "Silahkan Masuk</p>",
                    unsafe_allow_html=True,
                )
                st.markdown("<div style='height: 0.5rem;'></div>", unsafe_allow_html=True)
                with st.form("stockqu_login"):
                    email = st.text_input("Email", placeholder="admin@stockqu.com")
                    password = st.text_input("Password", type="password", placeholder="••••••••")
                    submitted = st.form_submit_button("Login", type="primary", use_container_width=True)
                if submitted:
                    try:
                        actor, new_tokens, client = sign_in(email.strip(), password)
                        token_str = serialize_session(new_tokens)
                        st.session_state[state_key] = new_tokens
                        st.query_params[param_key] = token_str
                        st.html(f"<script>try {{ localStorage.setItem('stockqu_auth', '{token_str}'); }} catch(e) {{}}</script>")
                        st.rerun()
                    except (AuthenticationRequired, AuthorizationDenied) as exc:
                        st.error(str(exc))
                    except Exception:  # noqa: BLE001 - jangan tampilkan exception provider mentah
                        st.error("Login gagal. Periksa kredensial atau konfigurasi Supabase.")
        st.stop()

    try:
        authorize(actor, allowed_roles, action)
    except AuthorizationDenied as exc:
        st.error(str(exc))
        if st.button("Logout", icon=":material/logout:"):
            st.session_state.pop(state_key, None)
            st.query_params.pop(param_key, None)
            st.html("<script>try { localStorage.removeItem('stockqu_auth'); } catch(e) {}</script>")
            st.rerun()
        st.stop()

    with st.sidebar:
        st.divider()
        col_user, col_logout = st.columns([2.2, 1.3], vertical_alignment="center")
        with col_user:
            st.markdown(
                f"<div style='font-size: 0.78rem; font-weight: 600; color: #1E2A44; "
                f"overflow: hidden; text-overflow: ellipsis; white-space: nowrap;' "
                f"title='{actor.email or actor.user_id}'>{actor.email or actor.user_id}</div>"
                f"<div style='font-size: 0.7rem; color: #64748b;'>{actor.role}</div>",
                unsafe_allow_html=True,
            )
        with col_logout:
            if st.button("Logout", key=f"logout-{action}", icon=":material/logout:", use_container_width=True):
                st.session_state.pop(state_key, None)
                st.session_state.pop("stockqu_actor", None)
                st.session_state.pop("stockqu_user_client", None)
                st.query_params.pop(param_key, None)
                st.html("<script>try { localStorage.removeItem('stockqu_auth'); } catch(e) {}</script>")
                try:
                    if client:
                        client.auth.sign_out()
                except Exception:  # noqa: BLE001 - session lokal tetap dihapus
                    client = None
                st.rerun()
    return actor, client
