"""Checkpointer LangGraph persisten (roadmap P4-16, SRS-F-37, NF-09).

PostgresSaver di Supabase Postgres, schema ``langgraph`` (dibuat migration P4, tidak
diekspos ke Data API). Butuh ``SUPABASE_DB_URL`` = connection string Postgres
(Supabase Dashboard -> Connect -> Session pooler). Transaction pooler (port 6543) tidak
mendukung prepared statement; prepare_threshold=None tetap dipasang untuk keamanan.

InMemorySaver hanya untuk test/dev lokal (STOCKQU_ALLOW_MEMORY_CHECKPOINT=1): approval
yang menunggu akan HILANG bila proses restart.
"""
from __future__ import annotations

from functools import lru_cache

from src.config import get_setting


class CheckpointNotConfigured(RuntimeError):
    pass


@lru_cache(maxsize=1)
def get_checkpointer():
    db_url = get_setting("SUPABASE_DB_URL")
    if db_url:
        from langgraph.checkpoint.postgres import PostgresSaver
        from psycopg.rows import dict_row
        from psycopg_pool import ConnectionPool

        pool = ConnectionPool(
            conninfo=db_url,
            min_size=1,
            max_size=4,
            open=True,
            kwargs={
                "autocommit": True,
                "prepare_threshold": None,
                "row_factory": dict_row,
                "options": "-c search_path=langgraph",
            },
        )
        saver = PostgresSaver(pool)
        saver.setup()
        return saver
    if (get_setting("STOCKQU_ALLOW_MEMORY_CHECKPOINT") or "") == "1":
        from langgraph.checkpoint.memory import InMemorySaver

        return InMemorySaver()
    raise CheckpointNotConfigured(
        "SUPABASE_DB_URL belum diisi; workflow approval butuh checkpoint Postgres persisten"
    )
