"""Konteks sesi per halaman (roadmap P5-22).

``app.py`` memverifikasi login SATU kali per rerun lewat Supabase Auth, lalu menyimpan
actor + client JWT user di session_state. Halaman mengambilnya lewat ``page_context``.
Cek role di sini hanya untuk UX; penegakan akses tetap di RLS (query memakai JWT user).
"""
from __future__ import annotations

from collections.abc import Iterable

import streamlit as st

from src.services import auth as auth_service
from src.services.auth import ActorContext, AuthorizationDenied, authorize
from src.services.supabase_client import SupabaseRepository

ACTOR_KEY = "stockqu_actor"
CLIENT_KEY = "stockqu_user_client"


def remember(actor: ActorContext, client) -> None:
    st.session_state[ACTOR_KEY] = actor
    st.session_state[CLIENT_KEY] = client


def page_context(allowed_roles: Iterable[str], action: str) -> tuple[ActorContext, object, SupabaseRepository]:
    actor = st.session_state.get(ACTOR_KEY)
    client = st.session_state.get(CLIENT_KEY)
    if actor is None or client is None:
        # Halaman dibuka tanpa router (mis. test): pakai gerbang login lengkap.
        actor, client = auth_service.require_streamlit_access(allowed_roles, action)
        remember(actor, client)
    try:
        authorize(actor, allowed_roles, action)
    except AuthorizationDenied as exc:
        st.error(str(exc))
        st.stop()
    return actor, client, SupabaseRepository(read_client=client)
