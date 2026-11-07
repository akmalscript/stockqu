"""Batas in-process untuk run agent dari Streamlit.

Database/provider tetap memerlukan quota/deadline deployment, tetapi boundary
aplikasi tidak lagi menerima run paralel atau rapid repeat tanpa batas.
"""
from __future__ import annotations

import threading
import time
from contextlib import contextmanager

from src.config import (
    AGENT_RATE_LIMIT_SECONDS,
    AGENT_RUN_TIMEOUT_SECONDS,
    MAX_CONCURRENT_AGENT_RUNS,
)
from src.services.auth import ActorContext


class WorkRejected(RuntimeError):
    pass


class WorkflowDeadlineExceeded(TimeoutError):
    pass


class WorkflowDeadline:
    def __init__(self, seconds: int = AGENT_RUN_TIMEOUT_SECONDS, clock=time.monotonic):
        if seconds <= 0:
            raise ValueError("Workflow deadline harus positif")
        self._clock = clock
        self._expires_at = clock() + seconds

    def check(self, stage: str) -> None:
        if self._clock() >= self._expires_at:
            raise WorkflowDeadlineExceeded(f"Batas waktu workflow tercapai pada tahap {stage}")


class WorkAdmissionController:
    def __init__(
        self,
        max_concurrent: int = MAX_CONCURRENT_AGENT_RUNS,
        cooldown_seconds: int = AGENT_RATE_LIMIT_SECONDS,
        clock=time.monotonic,
    ):
        self.max_concurrent = max_concurrent
        self.cooldown_seconds = cooldown_seconds
        self._clock = clock
        self._lock = threading.Lock()
        self._active: set[tuple[str, str]] = set()
        self._last_started: dict[tuple[str, str], float] = {}

    @contextmanager
    def admit(self, actor: ActorContext, workflow: str):
        key = (actor.user_id, workflow)
        now = self._clock()
        with self._lock:
            if key in self._active:
                raise WorkRejected("Workflow yang sama masih berjalan untuk user ini")
            if len(self._active) >= self.max_concurrent:
                raise WorkRejected("Kapasitas agent sedang penuh; coba lagi setelah run aktif selesai")
            last = self._last_started.get(key)
            if last is not None and now - last < self.cooldown_seconds:
                remaining = int(self.cooldown_seconds - (now - last)) + 1
                raise WorkRejected(f"Tunggu {remaining} detik sebelum menjalankan workflow yang sama")
            self._active.add(key)
            self._last_started[key] = now
        try:
            yield
        finally:
            with self._lock:
                self._active.discard(key)


work_admission = WorkAdmissionController()
