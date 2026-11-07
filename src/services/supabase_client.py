"""Supabase client + repository StockQu.

- Baca (Streamlit / Analyst): SUPABASE_ANON_KEY cukup (RLS select policy).
- Tulis (Agent Service: agent_logs, analyst_forecasts, sync_logs, inventory):
  pakai SUPABASE_SERVICE_ROLE_KEY yang HANYA disimpan server-side (.env / secrets), jangan di UI.
"""
from __future__ import annotations

from collections.abc import Iterable
from datetime import date, datetime, timedelta
from functools import lru_cache
from typing import Any
from zoneinfo import ZoneInfo

from src.config import get_setting
from src.utils.retry import NonRetryableError

PAGE_SIZE = 1000  # batas default PostgREST max-rows


class SupabaseNotConfigured(NonRetryableError):
    pass


@lru_cache(maxsize=2)
def get_client(write: bool = False):
    """Buat client Supabase. write=True memakai service role key jika tersedia."""
    from supabase import create_client

    url = get_setting("SUPABASE_URL")
    key = None
    if write:
        key = get_setting("SUPABASE_SERVICE_ROLE_KEY")
    key = key or get_setting("SUPABASE_ANON_KEY")
    if not url or not key:
        raise SupabaseNotConfigured("SUPABASE_URL / SUPABASE_ANON_KEY belum diisi di .env atau secrets")
    return create_client(url, key)


def has_write_key() -> bool:
    return bool(get_setting("SUPABASE_SERVICE_ROLE_KEY"))


class SupabaseRepository:
    """Akses data StockQu. Semua angka yang dipakai agent berasal dari sini (bisa ditelusur)."""

    def __init__(self, read_client=None, write_client=None):
        self._read = read_client
        self._write = write_client

    @property
    def read(self):
        if self._read is None:
            self._read = get_client(write=False)
        return self._read

    @property
    def write(self):
        if self._write is None:
            self._write = get_client(write=True)
        return self._write

    # ---------- helpers ----------
    @staticmethod
    def _fetch_all(query_factory) -> list[dict]:
        rows: list[dict] = []
        start = 0
        while True:
            res = query_factory().range(start, start + PAGE_SIZE - 1).execute()
            batch = res.data or []
            rows.extend(batch)
            if len(batch) < PAGE_SIZE:
                return rows
            start += PAGE_SIZE

    # ---------- master data ----------
    def fetch_branches(self) -> list[dict]:
        return self.read.table("branches").select("id, name, city").order("name").execute().data or []

    def fetch_products(self) -> list[dict]:
        return self._fetch_all(
            lambda: self.read.table("products")
            .select("id, sku, name, category, margin, turnover_rate, lead_time_days, safety_days")
            .order("sku")
        )

    def fetch_inventory(self) -> list[dict]:
        return self._fetch_all(
            lambda: self.read.table("inventory").select("id, product_id, branch_id, stock_qty, updated_at").order("id")
        )

    # ---------- purchaser inputs ----------
    def fetch_budget(self, period: str) -> dict | None:
        res = self.read.table("budgets").select("*").eq("month", period).limit(1).execute()
        return res.data[0] if res.data else None

    def fetch_purchase_orders_for_period(self, period: str) -> list[dict]:
        """PO bulan berjalan dalam batas waktu WIB; dipakai menghitung committed budget."""
        year, month = (int(part) for part in period.split("-", 1))
        start = datetime(year, month, 1, tzinfo=ZoneInfo("Asia/Jakarta"))
        end = datetime(year + (month == 12), 1 if month == 12 else month + 1, 1, tzinfo=start.tzinfo)
        return self._fetch_all(
            lambda: self.read.table("purchase_orders")
            .select("id, total_amount, status, created_at")
            .gte("created_at", start.isoformat())
            .lt("created_at", end.isoformat())
            .order("created_at")
        )

    def fetch_reference_contracts(self) -> list[dict]:
        """Harga referensi P2; pemilihan supplier/kuotasi aktual baru dilakukan P3."""
        return self._fetch_all(
            lambda: self.read.table("contracts")
            .select("id, supplier_id, product_id, price, discount, valid_until, moq, pack_size, created_at")
            .order("created_at", desc=True)
        )

    # ---------- sales history ----------
    def latest_sales_date(self) -> date | None:
        res = self.read.table("sales_history").select("sale_date").order("sale_date", desc=True).limit(1).execute()
        if not res.data:
            return None
        return date.fromisoformat(res.data[0]["sale_date"])

    def fetch_sales_history(self, as_of: date, days: int) -> list[dict]:
        """Histori penjualan harian [as_of - days + 1, as_of] untuk semua SKU dan cabang."""
        start = as_of - timedelta(days=days - 1)
        return self._fetch_all(
            lambda: self.read.table("sales_history")
            .select("product_id, branch_id, sale_date, qty")
            .gte("sale_date", start.isoformat())
            .lte("sale_date", as_of.isoformat())
            .order("id")
        )

    # ---------- writes ----------
    def upsert_inventory(self, rows: Iterable[dict]) -> int:
        payload = [
            {
                "product_id": r["product_id"],
                "branch_id": r["branch_id"],
                "stock_qty": r["stock_qty"],
                "updated_at": r["updated_at"],
            }
            for r in rows
        ]
        if not payload:
            return 0
        self.write.table("inventory").upsert(payload, on_conflict="product_id,branch_id").execute()
        return len(payload)

    def insert_agent_logs(self, rows: list[dict]) -> int:
        if not rows:
            return 0
        self.write.table("agent_logs").insert(rows).execute()
        return len(rows)

    def insert_forecasts(self, rows: list[dict]) -> int:
        if not rows:
            return 0
        for i in range(0, len(rows), 500):
            self.write.table("analyst_forecasts").upsert(
                rows[i : i + 500], on_conflict="run_id,product_id,branch_id"
            ).execute()
        return len(rows)

    def insert_sync_log(self, row: dict) -> None:
        self.write.table("sync_logs").insert(row).execute()

    def persist_procurement_plan(self, plan: dict, items: list[dict]) -> str:
        """Simpan header + detail dalam satu transaksi RPC (P2)."""
        res = self.write.rpc(
            "persist_procurement_plan",
            {"p_plan": to_jsonable(plan), "p_items": to_jsonable(items)},
        ).execute()
        if not res.data:
            raise RuntimeError("RPC persist_procurement_plan tidak mengembalikan plan_id")
        return str(res.data)

    # ---------- reads untuk UI ----------
    def latest_analyst_run_id(self) -> str | None:
        res = (
            self.read.table("analyst_forecasts")
            .select("run_id, created_at")
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
        return res.data[0]["run_id"] if res.data else None

    def fetch_forecasts(self, run_id: str) -> list[dict]:
        return self._fetch_all(
            lambda: self.read.table("analyst_forecasts").select("*").eq("run_id", run_id).order("id")
        )

    def fetch_agent_logs(self, run_id: str) -> list[dict]:
        return (
            self.read.table("agent_logs").select("*").eq("run_id", run_id).order("created_at").execute().data or []
        )

    def fetch_sync_logs(self, limit: int = 10) -> list[dict]:
        return (
            self.read.table("sync_logs").select("*").order("started_at", desc=True).limit(limit).execute().data or []
        )

    def latest_purchaser_run_id(self) -> str | None:
        res = (
            self.read.table("procurement_plans")
            .select("run_id, created_at")
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
        return res.data[0]["run_id"] if res.data else None

    def fetch_procurement_plan(self, run_id: str) -> dict | None:
        res = self.read.table("procurement_plans").select("*").eq("run_id", run_id).limit(1).execute()
        return res.data[0] if res.data else None

    def fetch_procurement_plan_items(self, plan_id: str) -> list[dict]:
        return self._fetch_all(
            lambda: self.read.table("procurement_plan_items")
            .select("*")
            .eq("plan_id", plan_id)
            .order("rank")
        )

    def has_dummy_sales(self) -> bool:
        res = self.read.table("sales_history").select("id").eq("source", "DUMMY-SEED").limit(1).execute()
        return bool(res.data)

    def fetch_sales_series(self, product_id: str, branch_id: str, start: date, end: date) -> list[dict]:
        return (
            self.read.table("sales_history")
            .select("sale_date, qty")
            .eq("product_id", product_id)
            .eq("branch_id", branch_id)
            .gte("sale_date", start.isoformat())
            .lte("sale_date", end.isoformat())
            .order("sale_date")
            .execute()
            .data
            or []
        )


def to_jsonable(value: Any) -> Any:
    """Ubah inf/NaN jadi None supaya aman disimpan ke JSON/Postgres."""
    import math

    if isinstance(value, float) and (math.isinf(value) or math.isnan(value)):
        return None
    if isinstance(value, dict):
        return {k: to_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_jsonable(v) for v in value]
    return value
