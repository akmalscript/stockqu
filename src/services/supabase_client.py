"""Supabase client + repository StockQu.

- Baca UI memakai client JWT user agar RLS memfilter role/cabang.
- Baca CLI/cron memakai service client yang diinjeksi explicit oleh runner agent.
- Tulis (Agent Service: agent_logs, analyst_forecasts, sync_logs, inventory):
  pakai SUPABASE_SERVICE_ROLE_KEY yang HANYA disimpan server-side (.env / secrets), jangan di UI.
"""
from __future__ import annotations

from collections.abc import Iterable
from datetime import date, datetime, timedelta
from functools import lru_cache
from typing import Any
from zoneinfo import ZoneInfo

from src.config import (
    MAX_DATA_ROWS,
    MAX_SALES_HISTORY_ROWS,
    PROVIDER_REQUEST_TIMEOUT_SECONDS,
    get_setting,
)
from src.utils.retry import NonRetryableError

PAGE_SIZE = 1000  # batas default PostgREST max-rows


class SupabaseNotConfigured(NonRetryableError):
    pass


class DataLimitExceeded(NonRetryableError):
    pass


@lru_cache(maxsize=2)
def get_client(write: bool = False):
    """Buat client Supabase; write tidak pernah fallback ke anon/publishable key."""
    from supabase import ClientOptions, create_client

    url = get_setting("SUPABASE_URL")
    key = get_setting("SUPABASE_SERVICE_ROLE_KEY") if write else get_setting("SUPABASE_ANON_KEY")
    if not url or not key:
        required = "SUPABASE_SERVICE_ROLE_KEY" if write else "SUPABASE_ANON_KEY"
        raise SupabaseNotConfigured(f"SUPABASE_URL / {required} belum diisi di .env atau secrets")
    return create_client(
        url,
        key,
        options=ClientOptions(
            postgrest_client_timeout=PROVIDER_REQUEST_TIMEOUT_SECONDS,
            storage_client_timeout=PROVIDER_REQUEST_TIMEOUT_SECONDS,
            function_client_timeout=PROVIDER_REQUEST_TIMEOUT_SECONDS,
        ),
    )


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
            raise SupabaseNotConfigured(
                "Read client wajib berupa JWT user terverifikasi atau service client CLI/cron"
            )
        return self._read

    @property
    def write(self):
        if self._write is None:
            self._write = get_client(write=True)
        return self._write

    # ---------- helpers ----------
    @staticmethod
    def _fetch_all(query_factory, max_rows: int = MAX_DATA_ROWS) -> list[dict]:
        rows: list[dict] = []
        start = 0
        while True:
            res = query_factory().range(start, start + PAGE_SIZE - 1).execute()
            batch = res.data or []
            rows.extend(batch)
            if len(rows) > max_rows:
                raise DataLimitExceeded(f"Query melewati batas aman {max_rows} baris")
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
            .select("id, supplier_id, total_amount, status, created_at")
            .gte("created_at", start.isoformat())
            .lt("created_at", end.isoformat())
            .order("created_at")
        )

    def fetch_active_budget_reservations(self, period: str) -> list[dict]:
        return self._fetch_all(
            lambda: self.read.table("procurement_budget_reservations")
            .select("plan_id, period, amount, status, created_at")
            .eq("period", period)
            .eq("status", "active")
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
            .order("id"),
            max_rows=MAX_SALES_HISTORY_ROWS,
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

    # ---------- P3 negotiator: input ----------
    def fetch_suppliers(self) -> list[dict]:
        return (
            self.read.table("suppliers")
            .select("id, name, contact, lead_time_days, performance_score")
            .order("name")
            .execute()
            .data
            or []
        )

    def fetch_supplier_documents(self) -> list[dict]:
        return self._fetch_all(
            lambda: self.read.table("supplier_documents")
            .select("id, supplier_id, doc_type, title, version, valid_from, valid_until, page_count, source, created_at")
            .order("created_at", desc=True)
        )

    def fetch_document_chunks(self, document_ids: list[str]) -> list[dict]:
        if not document_ids:
            return []
        return self._fetch_all(
            lambda: self.read.table("supplier_document_chunks")
            .select("document_id, chunk_index, page, clause_ref, content")
            .in_("document_id", document_ids)
            .order("id")
        )

    def fetch_plan_reservation(self, plan_id: str) -> dict | None:
        res = (
            self.read.table("procurement_budget_reservations")
            .select("plan_id, period, amount, status")
            .eq("plan_id", plan_id)
            .limit(1)
            .execute()
        )
        return res.data[0] if res.data else None

    # ---------- P3 negotiator: dokumen (service role) ----------
    def find_supplier_document_by_sha(self, sha256: str) -> dict | None:
        res = self.write.table("supplier_documents").select("*").eq("sha256", sha256).limit(1).execute()
        return res.data[0] if res.data else None

    def next_document_version(self, supplier_id: str, doc_type: str) -> int:
        res = (
            self.write.table("supplier_documents")
            .select("version")
            .eq("supplier_id", supplier_id)
            .eq("doc_type", doc_type)
            .order("version", desc=True)
            .limit(1)
            .execute()
        )
        return int(res.data[0]["version"]) + 1 if res.data else 1

    def upload_document(self, bucket: str, path: str, data: bytes) -> None:
        self.write.storage.from_(bucket).upload(
            path, data, {"content-type": "application/pdf", "upsert": "true"}
        )

    def download_document(self, bucket: str, path: str) -> bytes:
        return self.write.storage.from_(bucket).download(path)

    def insert_supplier_document(self, row: dict) -> dict:
        res = self.write.table("supplier_documents").insert(row).execute()
        return res.data[0]

    def insert_document_chunks(self, rows: list[dict]) -> int:
        if rows:
            self.write.table("supplier_document_chunks").insert(rows).execute()
        return len(rows)

    def delete_supplier_document(self, document_id: str) -> None:
        self.write.table("supplier_documents").delete().eq("id", document_id).execute()

    # ---------- P3 negotiator: persist + UI ----------
    def persist_negotiation(self, run: dict, comparisons: list[dict], pos: list[dict]) -> str:
        res = self.write.rpc(
            "persist_negotiation",
            {"p_run": to_jsonable(run), "p_comparisons": to_jsonable(comparisons), "p_pos": to_jsonable(pos)},
        ).execute()
        if not res.data:
            raise RuntimeError("RPC persist_negotiation tidak mengembalikan id")
        return str(res.data)

    def latest_negotiation_run(self) -> dict | None:
        res = self.read.table("negotiation_runs").select("*").order("created_at", desc=True).limit(1).execute()
        return res.data[0] if res.data else None

    def fetch_supplier_comparisons(self, negotiation_run_uuid: str) -> list[dict]:
        return self._fetch_all(
            lambda: self.read.table("supplier_comparisons")
            .select("*")
            .eq("negotiation_run_id", negotiation_run_uuid)
            .order("id")
        )

    def fetch_draft_purchase_orders(self, negotiation_run_uuid: str) -> list[dict]:
        return self._fetch_all(
            lambda: self.read.table("purchase_orders")
            .select("id, po_number, supplier_id, branch_id, total_amount, status, expected_arrival, draft, created_at")
            .eq("negotiation_run_id", negotiation_run_uuid)
            .order("po_number")
        )

    # ---------- P4 orkestrasi & approval (service role) ----------
    def create_workflow_job(self, row: dict) -> tuple[dict, bool]:
        """Insert job; jika job_key sudah ada / ada job running, kembalikan job lama (created=False)."""
        try:
            res = self.write.table("workflow_jobs").insert(row).execute()
            return res.data[0], True
        except Exception as exc:
            if "23505" not in str(exc) and "duplicate" not in str(exc).lower():
                raise
            existing = (
                self.write.table("workflow_jobs").select("*").eq("job_key", row["job_key"]).limit(1).execute().data
                or self.write.table("workflow_jobs").select("*").eq("status", "running").limit(1).execute().data
            )
            if not existing:
                raise
            return existing[0], False

    def update_workflow_job(self, run_id: str, fields: dict) -> None:
        self.write.table("workflow_jobs").update(
            {**to_jsonable(fields), "updated_at": datetime.now(ZoneInfo("UTC")).isoformat()}
        ).eq("run_id", run_id).execute()

    def fetch_workflow_jobs(self, limit: int = 10) -> list[dict]:
        return self.read.table("workflow_jobs").select("*").order("started_at", desc=True).limit(limit).execute().data or []

    def request_po_approval(self, payload: dict) -> dict:
        return self.write.rpc("request_po_approval", {"p_request": to_jsonable(payload)}).execute().data

    def decide_approval(self, payload: dict) -> dict:
        return self.write.rpc("decide_approval", {"p_decision": to_jsonable(payload)}).execute().data

    def mark_po_sent(self, po_id: str, email_ref: str, actor: str) -> dict:
        return self.write.rpc(
            "mark_po_sent", {"p_po_id": po_id, "p_email_ref": email_ref, "p_actor": actor}
        ).execute().data

    def set_approval_notification(self, approval_code: str, status: str) -> None:
        self.write.table("approval_requests").update({"notification_status": status}).eq(
            "approval_code", approval_code
        ).execute()

    def fetch_approval_request(self, approval_code: str) -> dict | None:
        res = self.read.table("approval_requests").select("*").eq("approval_code", approval_code).limit(1).execute()
        return res.data[0] if res.data else None

    def fetch_approval_requests(self, limit: int = 20) -> list[dict]:
        return (
            self.read.table("approval_requests").select("*").order("created_at", desc=True).limit(limit).execute().data
            or []
        )

    def fetch_purchase_orders_by_ids(self, po_ids: list[str]) -> list[dict]:
        if not po_ids:
            return []
        return (
            self.read.table("purchase_orders")
            .select("id, po_number, supplier_id, branch_id, total_amount, status, expected_arrival, draft, "
                    "approval_code, approved_at, sent_at")
            .in_("id", po_ids)
            .order("po_number")
            .execute()
            .data
            or []
        )

    def fetch_purchase_order_by_number(self, po_number: str) -> dict | None:
        res = (
            self.read.table("purchase_orders")
            .select("id, po_number, supplier_id, branch_id, total_amount, status, expected_arrival, draft")
            .eq("po_number", po_number)
            .limit(1)
            .execute()
        )
        return res.data[0] if res.data else None

    def fetch_recent_purchase_orders(self, limit: int = 30) -> list[dict]:
        return (
            self.read.table("purchase_orders")
            .select("id, po_number, supplier_id, branch_id, total_amount, status, expected_arrival, approval_code, "
                    "approved_at, sent_at, created_at")
            .not_.is_("po_number", "null")
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
            .data
            or []
        )

    def fetch_supplier_emails(self) -> dict[str, str | None]:
        rows = self.read.table("suppliers").select("id, email").execute().data or []
        return {str(r["id"]): r.get("email") for r in rows}

    # ---------- P5 dashboard (JWT user, RLS) ----------
    ADMIN_TABLES = frozenset({"products", "suppliers", "contracts", "budgets"})

    def fetch_inventory_detailed(self) -> list[dict]:
        return self._fetch_all(
            lambda: self.read.table("inventory")
            .select("id, product_id, branch_id, stock_qty, incoming_qty, updated_at")
            .order("id")
        )

    def fetch_recent_agent_logs(self, limit: int = 500, agent: str | None = None, run_id: str | None = None) -> list[dict]:
        query = self.read.table("agent_logs").select("*").order("created_at", desc=True).limit(min(limit, 2000))
        if agent:
            query = query.eq("agent_name", agent)
        if run_id:
            query = query.eq("run_id", run_id)
        return query.execute().data or []

    def fetch_procurement_plans(self, limit: int = 20) -> list[dict]:
        return (
            self.read.table("procurement_plans")
            .select("id, run_id, analyst_run_id, period, status, proposed_total, created_at")
            .order("created_at", desc=True).limit(limit).execute().data or []
        )

    def fetch_negotiation_runs(self, limit: int = 20) -> list[dict]:
        return (
            self.read.table("negotiation_runs")
            .select("id, run_id, plan_id, status, po_total, created_at")
            .order("created_at", desc=True).limit(limit).execute().data or []
        )

    def fetch_admin_rows(self, table: str) -> list[dict]:
        if table not in self.ADMIN_TABLES:
            raise ValueError(f"tabel {table} tidak boleh dikelola dari Admin")
        return self._fetch_all(lambda: self.read.table(table).select("*").order("id"))

    def admin_insert(self, table: str, row: dict) -> dict:
        if table not in self.ADMIN_TABLES:
            raise ValueError(f"tabel {table} tidak boleh dikelola dari Admin")
        return (self.read.table(table).insert(to_jsonable(row)).execute().data or [{}])[0]

    def admin_update(self, table: str, row_id: str, row: dict) -> dict:
        if table not in self.ADMIN_TABLES:
            raise ValueError(f"tabel {table} tidak boleh dikelola dari Admin")
        data = self.read.table(table).update(to_jsonable(row)).eq("id", row_id).execute().data
        if not data:
            raise PermissionError("Tidak ada baris yang berubah (RLS menolak atau id tidak ada)")
        return data[0]

    def admin_delete(self, table: str, row_id: str) -> None:
        if table not in self.ADMIN_TABLES:
            raise ValueError(f"tabel {table} tidak boleh dikelola dari Admin")
        data = self.read.table(table).delete().eq("id", row_id).execute().data
        if not data:
            raise PermissionError("Tidak ada baris yang terhapus (RLS menolak atau id tidak ada)")

    def fetch_audit_log(self, limit: int = 100) -> list[dict]:
        return self.read.table("audit_log").select("*").order("created_at", desc=True).limit(limit).execute().data or []


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
