"""Budget Purchaser StockQu (P2, F-11/F-21).

Anggaran tersedia dihitung ulang dari sumber Supabase, bukan mempercayai kolom
``remaining_budget`` yang bisa stale:

    available = min(total_budget, Rp50jt) - used_budget - PO committed - plan allocated

PO yang belum selesai dihitung committed agar cron berikutnya tidak membeli item
yang sama dengan budget yang sama.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation

from src.config import BUDGET_MONTHLY

COMMITTED_PO_STATUSES = frozenset({"waiting_approval", "pending_approval"})
SPENT_PO_STATUSES = frozenset({"approved", "sent", "received"})


def _money(value, default: int | Decimal = 0) -> Decimal:
    try:
        amount = Decimal(str(value if value is not None else default))
    except (InvalidOperation, ValueError):
        return Decimal(str(default))
    return max(amount, Decimal(0))


@dataclass(frozen=True)
class BudgetSnapshot:
    period: str
    total_budget: Decimal
    used_budget: Decimal
    committed_budget: Decimal
    allocated_budget: Decimal
    available_budget: Decimal
    recorded_remaining: Decimal | None
    committed_po_count: int
    active_plan_count: int
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        data = asdict(self)
        for key in (
            "total_budget",
            "used_budget",
            "committed_budget",
            "allocated_budget",
            "available_budget",
            "recorded_remaining",
        ):
            if data[key] is not None:
                data[key] = float(data[key])
        data["warnings"] = list(self.warnings)
        return data


def calculate_budget_snapshot(
    period: str,
    budget_row: dict | None,
    purchase_orders: list[dict],
    reservations: list[dict] | None = None,
) -> BudgetSnapshot:
    """Hitung budget efektif dengan plafon keras Rp50 juta per bulan."""
    warnings: list[str] = []
    row = budget_row or {}
    configured_total = _money(row.get("total_budget"), BUDGET_MONTHLY)
    hard_cap = Decimal(BUDGET_MONTHLY)
    total = min(configured_total, hard_cap)
    if not budget_row:
        warnings.append("Budget periode tidak ditemukan; memakai plafon kebijakan Rp 50.000.000.")
    elif configured_total > hard_cap:
        warnings.append("Budget Supabase melebihi plafon; nilai efektif dibatasi Rp 50.000.000.")

    recorded_used = _money(row.get("used_budget"))
    spent_rows = [po for po in purchase_orders if str(po.get("status", "")).lower() in SPENT_PO_STATUSES]
    spent_from_po = sum((_money(po.get("total_amount")) for po in spent_rows), Decimal(0))
    # budgets.used_budget dan status PO adalah dua representasi pengeluaran yang
    # bisa sinkron pada waktu berbeda. Ambil nilai terbesar, jangan dijumlahkan,
    # agar PO approved/sent tidak terhitung dua kali.
    used = max(recorded_used, spent_from_po)
    if spent_from_po > recorded_used:
        warnings.append("used_budget tertinggal; Purchaser memakai total PO approved/sent/received bulan berjalan.")
    committed_rows = [po for po in purchase_orders if str(po.get("status", "")).lower() in COMMITTED_PO_STATUSES]
    committed = sum((_money(po.get("total_amount")) for po in committed_rows), Decimal(0))
    active_reservations = [
        row for row in (reservations or []) if str(row.get("status", "active")).lower() == "active"
    ]
    allocated = sum((_money(row.get("amount")) for row in active_reservations), Decimal(0))
    available = max(total - used - committed - allocated, Decimal(0))

    recorded = None if row.get("remaining_budget") is None else _money(row.get("remaining_budget"))
    if recorded is not None and recorded != available:
        warnings.append(
            "remaining_budget Supabase berbeda dari hasil hitung; Purchaser memakai total - used - committed - allocated."
        )

    return BudgetSnapshot(
        period=period,
        total_budget=total,
        used_budget=used,
        committed_budget=committed,
        allocated_budget=allocated,
        available_budget=available,
        recorded_remaining=recorded,
        committed_po_count=len(committed_rows),
        active_plan_count=len(active_reservations),
        warnings=tuple(warnings),
    )


def load_budget_snapshot(repo, period: str) -> BudgetSnapshot:
    return calculate_budget_snapshot(
        period,
        repo.fetch_budget(period),
        repo.fetch_purchase_orders_for_period(period),
        repo.fetch_active_budget_reservations(period),
    )
