"""Fake repository & Sheets untuk test (tanpa jaringan). CONTOH SIMULASI - BUKAN DATA REAL."""
from __future__ import annotations

from datetime import date, timedelta

from src.services.sheets_client import STOCK_HEADER, SheetStockRow

AS_OF = date(2026, 11, 6)
BRANCHES = [{"id": f"b{i}", "name": f"Branch {i}", "city": "Jawa Tengah"} for i in (1, 2, 3)]
PRODUCTS = [
    {"id": "pA", "sku": "SKU-A", "name": "Item A", "category": "Kategori A", "margin": 18, "turnover_rate": 1.2, "lead_time_days": 3, "safety_days": 3},
    {"id": "pB", "sku": "SKU-B", "name": "Item B", "category": "Kategori B", "margin": 22, "turnover_rate": 1.5, "lead_time_days": 4, "safety_days": 3},
    {"id": "pZ", "sku": "SKU-Z", "name": "Item Z", "category": "Kategori C", "margin": 10, "turnover_rate": 0.8, "lead_time_days": 3, "safety_days": 3},
]
# (pid, bid) -> (stok, rata-rata harian target)
SETUP = {
    ("pA", "b1"): (100, 30.0),  # 3.3 hari -> URGENT
    ("pB", "b1"): (120, 20.0),  # 6.0 hari -> RISIKO
    ("pZ", "b1"): (900, 10.0),  # 90 hari -> AMAN
    ("pA", "b2"): (800, 30.0),  # 26.7 hari -> AMAN (tidak dijumlah dengan Branch 1)
    ("pB", "b2"): (500, 5.0),
    ("pZ", "b2"): (500, 5.0),
    ("pA", "b3"): (600, 10.0),
    ("pB", "b3"): (600, 10.0),
    # ("pZ", "b3") sengaja tanpa stok -> DATA TIDAK DITEMUKAN
}


def make_sales(as_of: date = AS_OF, days: int = 180) -> list[dict]:
    rows = []
    for (pid, bid), (_, avg) in SETUP.items():
        for i in range(days):
            d = as_of - timedelta(days=days - 1 - i)
            weekly = 1.15 if d.weekday() >= 5 else 0.94
            rows.append({"product_id": pid, "branch_id": bid, "sale_date": d.isoformat(), "qty": round(avg * weekly, 2)})
    return rows


class FakeRepo:
    def __init__(self, with_sales: bool = True, fail_writes: bool = False):
        self.inventory = [
            {"id": f"i{n}", "product_id": p, "branch_id": b, "stock_qty": s, "updated_at": "2026-11-07T06:00:00+00:00"}
            for n, ((p, b), (s, _)) in enumerate(SETUP.items())
        ]
        self.sales = make_sales() if with_sales else []
        self.fail_writes = fail_writes
        self.forecasts: list[dict] = []
        self.agent_logs: list[dict] = []
        self.sync_logs: list[dict] = []
        self.upserts: list[dict] = []

    def fetch_products(self):
        return PRODUCTS

    def fetch_branches(self):
        return BRANCHES

    def fetch_inventory(self):
        return [dict(r) for r in self.inventory]

    def latest_sales_date(self):
        return max((date.fromisoformat(r["sale_date"]) for r in self.sales), default=None)

    def fetch_sales_history(self, as_of, days):
        start = as_of - timedelta(days=days - 1)
        return [r for r in self.sales if start.isoformat() <= r["sale_date"] <= as_of.isoformat()]

    def _check(self):
        if self.fail_writes:
            raise PermissionError("RLS: anon key tidak boleh menulis")

    def upsert_inventory(self, rows):
        self._check()
        rows = list(rows)
        self.upserts.extend(rows)
        return len(rows)

    def insert_forecasts(self, rows):
        self._check()
        self.forecasts.extend(rows)
        return len(rows)

    def insert_agent_logs(self, rows):
        self._check()
        self.agent_logs.extend(rows)
        return len(rows)

    def insert_sync_log(self, row):
        self._check()
        self.sync_logs.append(row)


class FakeSheets:
    def __init__(self, rows: list[list] | None = None, fail_reads: int = 0):
        self.values = [STOCK_HEADER, *(rows or [])]
        self.fail_reads = fail_reads
        self.read_calls = 0
        self.written: list[list] | None = None

    def read_stock_rows(self):
        self.read_calls += 1
        if self.read_calls <= self.fail_reads:
            raise ConnectionError("Sheets API timeout")
        out = []
        for n, row in enumerate(self.values[1:], start=2):
            row = [str(c) for c in row] + [""] * (6 - len(row))
            out.append(SheetStockRow(n, row[0], row[1], row[2], row[3], row[4], row[5]))
        return out

    def write_stock_rows(self, rows):
        self.written = rows
        self.values = [STOCK_HEADER, *rows]
        return len(rows) + 1
