from src.tools import optimizer
from src.tools.optimizer import optimize_budget_allocation, validate_procurement_plan


def items():
    return [
        {
            "product_id": "pA",
            "branch_id": "b1",
            "risk_level": "URGENT",
            "requested_qty": 10,
            "unit_price": 100_000,
            "moq": 2,
            "pack_size": 2,
            "margin_fraction": 0.30,
            "priority_score": 0.95,
            "rank": 1,
        },
        {
            "product_id": "pB",
            "branch_id": "b1",
            "risk_level": "RISIKO",
            "requested_qty": 10,
            "unit_price": 100_000,
            "moq": 5,
            "pack_size": 5,
            "margin_fraction": 0.10,
            "priority_score": 0.60,
            "rank": 2,
        },
    ]


def test_optimizer_never_exceeds_budget_and_respects_moq_pack():
    result = optimize_budget_allocation(items(), 1_000_000)
    assert result.proposed_total <= 1_000_000
    assert result.allocations["pA|b1"] >= 2
    assert result.allocations["pA|b1"] % 2 == 0
    assert result.allocations["pB|b1"] == 0 or result.allocations["pB|b1"] >= 5
    assert result.allocations["pB|b1"] % 5 == 0


def test_optimizer_marks_infeasible_urgent_minimum():
    result = optimize_budget_allocation(items(), 100_000)
    assert result.status == "budget_insufficient"
    assert result.minimum_urgent_cost == 200_000
    assert result.budget_shortfall == 100_000
    assert result.proposed_total <= 100_000


def test_optimizer_greedy_fallback_is_auditable(monkeypatch):
    def fail_solver(*_args, **_kwargs):
        raise RuntimeError("solver unavailable")

    monkeypatch.setattr(optimizer, "_solve_milp", fail_solver)
    result = optimize_budget_allocation(items(), 1_000_000)
    assert result.method == "greedy_fallback"
    assert result.solver_status == "fallback: RuntimeError"
    assert result.proposed_total <= 1_000_000


def test_plan_validation_rejects_duplicate_and_budget_overrun():
    plan_items = [
        {
            "product_id": "pA", "branch_id": "b1", "allocated_qty": 10,
            "unit_price": 100_000, "moq": 2, "pack_size": 2,
        },
        {
            "product_id": "pA", "branch_id": "b1", "allocated_qty": 5,
            "unit_price": 100_000, "moq": 1, "pack_size": 1,
        },
    ]
    validation = validate_procurement_plan(plan_items, 1_000_000)
    assert not validation["valid"]
    assert any("Duplikat" in error for error in validation["errors"])
    assert any("melebihi" in error for error in validation["errors"])
