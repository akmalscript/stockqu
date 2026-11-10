"""Read model untuk dashboard utama StockQu.

Semua query memakai ``SupabaseRepository`` dengan client JWT user sehingga RLS
tetap membatasi cabang. Modul ini tidak membaca Google Sheets langsung: inventory
di Supabase adalah snapshot hasil sinkron dua arah dan ``sync_logs`` menyimpan
jejak sinkronisasinya.
"""
from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TypeVar

from src.services.budget import calculate_budget_snapshot
from src.utils.errors import safe_error

T = TypeVar("T")


@dataclass
class DashboardSnapshot:
    period: str
    products: list[dict] = field(default_factory=list)
    branches: list[dict] = field(default_factory=list)
    inventory: list[dict] = field(default_factory=list)
    analyst_run_id: str | None = None
    forecasts: list[dict] = field(default_factory=list)
    budget: dict | None = None
    purchase_orders: list[dict] = field(default_factory=list)
    purchaser_run_id: str | None = None
    procurement_plan: dict | None = None
    plan_items: list[dict] = field(default_factory=list)
    agent_logs: list[dict] = field(default_factory=list)
    sync_logs: list[dict] = field(default_factory=list)
    suppliers: list[dict] = field(default_factory=list)
    contracts: list[dict] = field(default_factory=list)
    has_dummy_data: bool = False
    issues: list[str] = field(default_factory=list)


def _attempt(
    issues: list[str],
    label: str,
    operation: Callable[[], T],
    default: T,
) -> T:
    try:
        return operation()
    except Exception as exc:  # noqa: BLE001 - UI hanya menerima tipe error yang aman
        issues.append(f"{label}: {safe_error(exc)}")
        return default


def _sort_forecast(row: dict) -> tuple[float, str, str]:
    value = row.get("days_to_stockout")
    try:
        days = float(value)
    except (TypeError, ValueError):
        days = math.inf
    return days, str(row.get("branch") or ""), str(row.get("sku") or "")


def sync_forecasts_with_inventory(
    forecasts: list[dict],
    inventory: list[dict],
    products: list[dict] | None = None,
    branches: list[dict] | None = None,
) -> list[dict]:
    """Selaraskan stok fisik terkini dari tabel inventory ke data forecast Analyst.

    Menghitung ulang days-to-stockout, kebutuhan restock, dan level risiko
    berdasarkan stok real-time (hasil sinkron Google Sheets) dan laju penjualan harian.
    """
    from src.utils import formulas as F

    prod_map: dict[str, dict] = {}
    if products:
        for p in products:
            if "id" in p:
                prod_map[str(p["id"])] = p
            if p.get("sku"):
                prod_map[str(p["sku"]).strip().upper()] = p

    branch_map: dict[str, str] = {}
    if branches:
        for b in branches:
            if "id" in b:
                branch_map[str(b["id"])] = str(b.get("name") or "")
            if b.get("name"):
                branch_map[str(b["name"]).strip().lower()] = str(b.get("id"))

    stock_map: dict[tuple[str, str], float] = {}
    for r in inventory:
        pid = str(r.get("product_id") or "")
        bid = str(r.get("branch_id") or "")
        sqty = r.get("stock_qty")
        if sqty is not None:
            val = float(sqty)
            if pid and bid:
                stock_map[(pid, bid)] = val
            p_obj = prod_map.get(pid)
            b_name = branch_map.get(bid) or str(r.get("branch") or "")
            if p_obj and p_obj.get("sku") and b_name:
                stock_map[(str(p_obj["sku"]).strip().upper(), b_name.strip().lower())] = val

    synced = []
    for row in forecasts:
        item = dict(row)
        pid = str(item.get("product_id") or "")
        bid = str(item.get("branch_id") or "")
        sku = str(item.get("sku") or "").strip().upper()
        bname = str(item.get("branch") or "").strip().lower()

        current_stock = stock_map.get((pid, bid))
        if current_stock is None and sku and bname:
            current_stock = stock_map.get((sku, bname))

        if current_stock is not None:
            item["stock_qty"] = current_stock
            avg_daily = float(item.get("avg_daily_sales") or 0.0)
            lead_time = int(
                item.get("lead_time_days")
                or (prod_map.get(pid, {}) if pid else {}).get("lead_time_days")
                or 0
            )
            if avg_daily > 0:
                dts = F.days_to_stockout(current_stock, avg_daily)
                item["days_to_stockout"] = dts
                item["restock_qty"] = F.restock_qty(avg_daily, lead_time, current_stock)
                item["risk_level"] = F.classify_risk(dts, lead_time)
            else:
                item["days_to_stockout"] = math.inf
                item["restock_qty"] = 0
                item["risk_level"] = F.RISK_SAFE
        synced.append(item)
    return synced


def load_dashboard_snapshot(repository, period: str) -> DashboardSnapshot:
    """Muat sumber dashboard secara bounded melalui repository dan pertahankan RLS."""
    snapshot = DashboardSnapshot(period=period)
    issues = snapshot.issues

    snapshot.products = _attempt(issues, "products", repository.fetch_products, [])
    snapshot.branches = _attempt(issues, "branches", repository.fetch_branches, [])
    snapshot.inventory = _attempt(issues, "inventory", repository.fetch_inventory, [])
    snapshot.suppliers = _attempt(issues, "suppliers", repository.fetch_suppliers, [])
    snapshot.contracts = _attempt(
        issues,
        "contracts",
        repository.fetch_reference_contracts,
        [],
    )
    snapshot.sync_logs = _attempt(
        issues,
        "sync_logs",
        lambda: repository.fetch_sync_logs(limit=10),
        [],
    )

    product_map = {str(row["id"]): row for row in snapshot.products}
    branch_map = {str(row["id"]): row for row in snapshot.branches}
    supplier_map = {str(row["id"]): row for row in snapshot.suppliers}

    snapshot.analyst_run_id = _attempt(
        issues,
        "analyst_forecasts",
        repository.latest_analyst_run_id,
        None,
    )
    raw_forecasts = (
        _attempt(
            issues,
            "analyst_forecasts",
            lambda: repository.fetch_forecasts(snapshot.analyst_run_id),
            [],
        )
        if snapshot.analyst_run_id
        else []
    )
    synced_forecasts = sync_forecasts_with_inventory(
        raw_forecasts,
        snapshot.inventory,
        snapshot.products,
        snapshot.branches,
    )
    snapshot.forecasts = sorted(
        [
            {
                **row,
                "sku": product_map.get(str(row.get("product_id")), {}).get("sku"),
                "product_name": product_map.get(str(row.get("product_id")), {}).get("name"),
                "branch": branch_map.get(str(row.get("branch_id")), {}).get("name"),
            }
            for row in synced_forecasts
        ],
        key=_sort_forecast,
    )
    snapshot.inventory = [
        {
            **row,
            "sku": product_map.get(str(row.get("product_id")), {}).get("sku"),
            "product_name": product_map.get(str(row.get("product_id")), {}).get("name"),
            "branch": branch_map.get(str(row.get("branch_id")), {}).get("name"),
        }
        for row in snapshot.inventory
    ]
    snapshot.contracts = [
        {
            **row,
            "sku": product_map.get(str(row.get("product_id")), {}).get("sku"),
            "product_name": product_map.get(str(row.get("product_id")), {}).get("name"),
            "supplier_name": supplier_map.get(str(row.get("supplier_id")), {}).get("name"),
            "supplier_lead_time_days": supplier_map.get(
                str(row.get("supplier_id")), {}
            ).get("lead_time_days"),
            "performance_score": supplier_map.get(str(row.get("supplier_id")), {}).get(
                "performance_score"
            ),
        }
        for row in snapshot.contracts
    ]

    budget_row = _attempt(
        issues,
        "budgets",
        lambda: repository.fetch_budget(period),
        None,
    )
    snapshot.purchase_orders = _attempt(
        issues,
        "purchase_orders",
        lambda: repository.fetch_purchase_orders_for_period(period),
        [],
    )
    reservations = _attempt(
        issues,
        "procurement_budget_reservations",
        lambda: repository.fetch_active_budget_reservations(period),
        [],
    )
    if budget_row is not None:
        snapshot.budget = calculate_budget_snapshot(
            period,
            budget_row,
            snapshot.purchase_orders,
            reservations,
        ).to_dict()

    snapshot.purchaser_run_id = _attempt(
        issues,
        "procurement_plans",
        repository.latest_purchaser_run_id,
        None,
    )
    if snapshot.purchaser_run_id:
        snapshot.procurement_plan = _attempt(
            issues,
            "procurement_plans",
            lambda: repository.fetch_procurement_plan(snapshot.purchaser_run_id),
            None,
        )
    if snapshot.procurement_plan:
        raw_items = _attempt(
            issues,
            "procurement_plan_items",
            lambda: repository.fetch_procurement_plan_items(
                str(snapshot.procurement_plan["id"])
            ),
            [],
        )
        snapshot.plan_items = [
            {
                **row,
                "sku": product_map.get(str(row.get("product_id")), {}).get("sku"),
                "product_name": product_map.get(str(row.get("product_id")), {}).get(
                    "name"
                ),
                "branch": branch_map.get(str(row.get("branch_id")), {}).get("name"),
            }
            for row in raw_items
        ]

    trace_run_id = snapshot.purchaser_run_id or snapshot.analyst_run_id
    if trace_run_id:
        snapshot.agent_logs = _attempt(
            issues,
            "agent_logs",
            lambda: repository.fetch_agent_logs(trace_run_id),
            [],
        )
    snapshot.has_dummy_data = _attempt(
        issues,
        "dummy_marker",
        repository.has_dummy_sales,
        False,
    )
    return snapshot
