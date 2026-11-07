"""Tool optimasi budget Purchaser.

Model MILP memilih kuantitas per SKU-cabang dengan constraint budget, MOQ, dan
pack size. Jika solver tidak tersedia/gagal, fallback greedy tetap memenuhi
constraint dan method-nya dicatat di Execution Trace.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class OptimizationResult:
    allocations: dict[str, int]
    method: str
    status: str
    proposed_total: float
    minimum_urgent_cost: float
    budget_shortfall: float
    solver_status: str


def _key(item: dict) -> str:
    return f"{item['product_id']}|{item['branch_id']}"


def _rounded_full_qty(item: dict) -> int:
    pack = max(int(item.get("pack_size") or 1), 1)
    requested = max(math.ceil(float(item.get("requested_qty") or 0)), 0)
    return int(math.ceil(requested / pack) * pack) if requested else 0


def _minimum_qty(item: dict) -> int:
    pack = max(int(item.get("pack_size") or 1), 1)
    moq = max(int(item.get("moq") or 1), 1)
    return int(math.ceil(moq / pack) * pack)


def _item_value_per_unit(item: dict) -> float:
    margin_fraction = max(float(item.get("margin_fraction") or 0), 0.0)
    unit_price = max(float(item.get("unit_price") or 0), 0.0)
    priority = max(float(item.get("priority_score") or 0), 0.0)
    return priority * margin_fraction * unit_price


def _minimum_urgent_cost(items: list[dict]) -> float:
    return sum(
        _minimum_qty(item) * float(item["unit_price"])
        for item in items
        if item.get("risk_level") == "URGENT" and _rounded_full_qty(item) >= _minimum_qty(item)
    )


def _solve_milp(items: list[dict], budget: float, enforce_urgent: bool, time_limit: int) -> tuple[dict[str, int], str]:
    import pulp

    problem = pulp.LpProblem("stockqu_purchaser_budget", pulp.LpMaximize)
    pack_vars: dict[str, Any] = {}
    selected_vars: dict[str, Any] = {}

    for index, item in enumerate(items):
        key = _key(item)
        pack = max(int(item.get("pack_size") or 1), 1)
        full_qty = _rounded_full_qty(item)
        max_packs = full_qty // pack
        min_packs = math.ceil(_minimum_qty(item) / pack)
        packs = pulp.LpVariable(f"packs_{index}", lowBound=0, upBound=max_packs, cat=pulp.LpInteger)
        selected = pulp.LpVariable(f"selected_{index}", cat=pulp.LpBinary)
        pack_vars[key] = packs
        selected_vars[key] = selected
        problem += packs <= max_packs * selected
        problem += packs >= min_packs * selected
        if enforce_urgent and item.get("risk_level") == "URGENT" and max_packs >= min_packs:
            problem += selected == 1

    problem += pulp.lpSum(
        pack_vars[_key(item)] * int(item.get("pack_size") or 1) * float(item["unit_price"])
        for item in items
    ) <= budget
    problem += pulp.lpSum(
        pack_vars[_key(item)]
        * int(item.get("pack_size") or 1)
        * (_item_value_per_unit(item) + max(0.0, 1e-6 * (len(items) - int(item.get("rank") or 0))))
        for item in items
    )

    # PuLP 3.x membundel CBC lewat PULP_CBC_CMD. PuLP 4.x menggantinya dengan
    # COIN_CMD + extra cbcbox; fallback menjaga kompatibilitas kedua API.
    solver_class = getattr(pulp, "PULP_CBC_CMD", None) or pulp.COIN_CMD
    solver = solver_class(msg=False, timeLimit=time_limit)
    status_code = problem.solve(solver)
    solver_status = pulp.LpStatus.get(status_code, str(status_code))
    if solver_status not in {"Optimal", "Integer Feasible"}:
        raise RuntimeError(f"solver status {solver_status}")
    allocations = {
        _key(item): round(float(pulp.value(pack_vars[_key(item)]) or 0)) * int(item.get("pack_size") or 1)
        for item in items
    }
    return allocations, solver_status


def _solve_greedy(items: list[dict], budget: float, enforce_urgent: bool) -> dict[str, int]:
    remaining = float(budget)
    allocations = {_key(item): 0 for item in items}
    ordered = sorted(
        items,
        key=lambda item: (
            item.get("risk_level") != "URGENT",
            -(_item_value_per_unit(item) / max(float(item.get("unit_price") or 1), 1.0)),
            int(item.get("rank") or 10**9),
        ),
    )

    if enforce_urgent:
        for item in ordered:
            if item.get("risk_level") != "URGENT":
                continue
            qty = _minimum_qty(item)
            if qty <= _rounded_full_qty(item) and qty * float(item["unit_price"]) <= remaining:
                allocations[_key(item)] = qty
                remaining -= qty * float(item["unit_price"])

    for item in ordered:
        key = _key(item)
        pack = max(int(item.get("pack_size") or 1), 1)
        full = _rounded_full_qty(item)
        current = allocations[key]
        if current == 0:
            minimum = _minimum_qty(item)
            if minimum > full or minimum * float(item["unit_price"]) > remaining:
                continue
            current = minimum
            allocations[key] = current
            remaining -= minimum * float(item["unit_price"])
        extra_packs = min((full - current) // pack, int(remaining // (pack * float(item["unit_price"]))))
        if extra_packs > 0:
            extra = extra_packs * pack
            allocations[key] += extra
            remaining -= extra * float(item["unit_price"])
    return allocations


def optimize_budget_allocation(items: list[dict], available_budget: float, time_limit: int = 5) -> OptimizationResult:
    """Alokasikan budget tanpa pernah melampaui ``available_budget``."""
    budget = max(float(available_budget), 0.0)
    eligible = [
        item for item in items
        if float(item.get("unit_price") or 0) > 0 and _rounded_full_qty(item) >= _minimum_qty(item)
    ]
    min_urgent = _minimum_urgent_cost(eligible)
    enforce_urgent = min_urgent <= budget
    shortfall = max(min_urgent - budget, 0.0)

    if not eligible or budget <= 0:
        status = "budget_insufficient" if min_urgent > budget else "no_action"
        return OptimizationResult(
            allocations={_key(item): 0 for item in items},
            method="no_allocation",
            status=status,
            proposed_total=0.0,
            minimum_urgent_cost=min_urgent,
            budget_shortfall=shortfall,
            solver_status="not_run",
        )

    try:
        allocations, solver_status = _solve_milp(eligible, budget, enforce_urgent, time_limit)
        method = "milp_coin_cbc"
    except Exception as exc:  # noqa: BLE001 - kegagalan solver wajib fallback
        allocations = _solve_greedy(eligible, budget, enforce_urgent)
        solver_status = f"fallback: {type(exc).__name__}: {exc}"
        method = "greedy_fallback"

    for item in items:
        allocations.setdefault(_key(item), 0)
    total = sum(allocations[_key(item)] * float(item.get("unit_price") or 0) for item in items)
    if total > budget + 0.01:
        raise ValueError(f"Optimizer melampaui budget: {total} > {budget}")
    status = "budget_insufficient" if shortfall > 0 else ("completed" if total > 0 else "no_action")
    return OptimizationResult(
        allocations=allocations,
        method=method,
        status=status,
        proposed_total=round(total, 2),
        minimum_urgent_cost=round(min_urgent, 2),
        budget_shortfall=round(shortfall, 2),
        solver_status=solver_status,
    )


def validate_procurement_plan(items: list[dict], available_budget: float) -> dict:
    """Validasi deterministik budget, MOQ, pack, dan duplikasi SKU-cabang."""
    errors: list[str] = []
    seen: set[tuple[str, str]] = set()
    total = 0.0
    for item in items:
        key = (str(item["product_id"]), str(item["branch_id"]))
        if key in seen:
            errors.append(f"Duplikat product_id/branch_id: {key[0]} / {key[1]}")
        seen.add(key)
        qty = int(item.get("allocated_qty") or 0)
        if qty <= 0:
            continue
        price = float(item.get("unit_price") or 0)
        moq = int(item.get("moq") or 0)
        pack = int(item.get("pack_size") or 0)
        if price <= 0:
            errors.append(f"Harga referensi tidak valid: {key[0]} / {key[1]}")
        if moq <= 0 or qty < moq:
            errors.append(f"MOQ dilanggar: {key[0]} / {key[1]}")
        if pack <= 0 or qty % pack != 0:
            errors.append(f"Pack size dilanggar: {key[0]} / {key[1]}")
        total += qty * price
    if total > float(available_budget) + 0.01:
        errors.append(f"Total Rp {total:,.2f} melebihi budget tersedia Rp {float(available_budget):,.2f}")
    return {
        "valid": not errors,
        "errors": errors,
        "checked": ["budget", "moq", "pack_size", "duplicate_product_branch", "reference_price"],
        "calculated_total": round(total, 2),
        "available_budget": round(float(available_budget), 2),
    }
