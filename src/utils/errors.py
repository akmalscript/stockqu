"""Error text yang aman untuk UI dan audit trace."""
from __future__ import annotations


def safe_error(exc: BaseException) -> str:
    """Jangan meneruskan response provider, URL, token, atau payload ke UI/log."""
    return type(exc).__name__
