"""Retry terbatas dengan backoff (SRS-F-35 / NF-09). Tidak ada retry tak terbatas."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, TypeVar

T = TypeVar("T")


class NonRetryableError(Exception):
    """Error yang tidak perlu di-retry (mis. kredensial belum dikonfigurasi)."""


@dataclass
class RetryResult:
    attempts: int = 0
    errors: list[str] = field(default_factory=list)


def call_with_retry(
    fn: Callable[[], T],
    *,
    max_attempts: int = 3,
    base_delay: float = 0.5,
    sleep: Callable[[float], None] = time.sleep,
    result: RetryResult | None = None,
) -> T:
    """Panggil fn, retry maksimal max_attempts kali dengan exponential backoff.

    Info percobaan dicatat ke `result` supaya bisa masuk Execution Trace.
    """
    tracker = result if result is not None else RetryResult()
    last_exc: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        tracker.attempts = attempt
        try:
            return fn()
        except NonRetryableError as exc:
            tracker.errors.append(f"attempt {attempt}: {exc}")
            raise
        except Exception as exc:  # noqa: BLE001 - tool eksternal bisa gagal dengan apa saja
            last_exc = exc
            tracker.errors.append(f"attempt {attempt}: {type(exc).__name__}: {exc}")
            if attempt < max_attempts:
                sleep(base_delay * (2 ** (attempt - 1)))
    assert last_exc is not None
    raise last_exc
