"""Execution Trace - log setiap langkah/tool agent untuk audit (F-25, SRS-F-38, NF-04).

Disimpan ke tabel `agent_logs` (skema P0): agent_name, run_id, tool_name, status,
input_summary, output_summary, error_message, created_at.
"""
from __future__ import annotations

import time
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

from src.utils.errors import safe_error


def new_run_id(prefix: str = "RUN") -> str:
    now = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")  # noqa: UP017 - Python 3.10 compatible
    return f"{prefix}-{now}-{uuid.uuid4().hex[:6].upper()}"


@dataclass
class TraceStep:
    agent_name: str
    run_id: str
    tool_name: str
    status: str  # success | failed | retried | skipped | fallback
    input_summary: str = ""
    output_summary: str = ""
    error_message: str | None = None
    duration_ms: int = 0
    attempts: int = 1
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()  # noqa: UP017 - Python 3.10 compatible
    )

    def to_db_row(self) -> dict:
        """Map ke kolom agent_logs. Durasi & attempts disisipkan di output_summary (kolom P0 belum ada)."""
        meta = f" [durasi {self.duration_ms} ms, percobaan {self.attempts}]"
        return {
            "agent_name": self.agent_name,
            "run_id": self.run_id,
            "tool_name": self.tool_name,
            "status": self.status,
            "input_summary": self.input_summary[:1000],
            "output_summary": (self.output_summary + meta)[:2000],
            "error_message": (self.error_message or None) and self.error_message[:1000],
            "created_at": self.created_at,
        }

    def to_dict(self) -> dict:
        return asdict(self)


class ExecutionTracer:
    def __init__(self, agent_name: str, run_id: str):
        self.agent_name = agent_name
        self.run_id = run_id
        self.steps: list[TraceStep] = []

    def log(self, tool_name: str, status: str, **kwargs) -> TraceStep:
        step = TraceStep(agent_name=self.agent_name, run_id=self.run_id, tool_name=tool_name, status=status, **kwargs)
        self.steps.append(step)
        return step

    @contextmanager
    def step(self, tool_name: str, input_summary: str = ""):
        """Context manager: catat durasi + status. Isi `rec['output']` / `rec['attempts']` di dalam blok."""
        rec: dict = {"output": "", "attempts": 1, "status": None}
        started = time.perf_counter()
        try:
            yield rec
        except Exception as exc:
            self.log(
                tool_name,
                "failed",
                input_summary=input_summary,
                output_summary=rec.get("output", ""),
                error_message=safe_error(exc),
                duration_ms=int((time.perf_counter() - started) * 1000),
                attempts=rec.get("attempts", 1),
            )
            raise
        else:
            status = rec.get("status") or ("retried" if rec.get("attempts", 1) > 1 else "success")
            self.log(
                tool_name,
                status,
                input_summary=input_summary,
                output_summary=rec.get("output", ""),
                error_message=rec.get("error"),
                duration_ms=int((time.perf_counter() - started) * 1000),
                attempts=rec.get("attempts", 1),
            )
