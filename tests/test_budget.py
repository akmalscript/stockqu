from src.services.budget import calculate_budget_snapshot


def test_budget_subtracts_pending_po_but_not_draft_or_sent_twice():
    snapshot = calculate_budget_snapshot(
        "2026-11",
        {"total_budget": 50_000_000, "used_budget": 10_000_000, "remaining_budget": 40_000_000},
        [
            {"status": "draft", "total_amount": 3_000_000},
            {"status": "waiting_approval", "total_amount": 2_000_000},
            {"status": "sent", "total_amount": 7_000_000},
        ],
    )
    assert snapshot.total_budget == 50_000_000
    assert snapshot.used_budget == 10_000_000
    assert snapshot.committed_budget == 2_000_000
    assert snapshot.available_budget == 38_000_000
    assert snapshot.committed_po_count == 1
    assert snapshot.warnings


def test_budget_hard_caps_configuration_at_fifty_million():
    snapshot = calculate_budget_snapshot(
        "2026-11",
        {"total_budget": 75_000_000, "used_budget": 0, "remaining_budget": 75_000_000},
        [],
    )
    assert snapshot.total_budget == 50_000_000
    assert snapshot.available_budget == 50_000_000
    assert any("dibatasi" in warning for warning in snapshot.warnings)


def test_budget_policy_fallback_when_period_missing():
    snapshot = calculate_budget_snapshot("2026-11", None, [])
    assert snapshot.total_budget == 50_000_000
    assert snapshot.available_budget == 50_000_000
    assert any("tidak ditemukan" in warning for warning in snapshot.warnings)


def test_budget_subtracts_active_plan_reservations():
    snapshot = calculate_budget_snapshot(
        "2026-11",
        {"total_budget": 50_000_000, "used_budget": 10_000_000},
        [{"status": "waiting_approval", "total_amount": 5_000_000}],
        [
            {"status": "active", "amount": 12_000_000},
            {"status": "released", "amount": 8_000_000},
        ],
    )
    assert snapshot.allocated_budget == 12_000_000
    assert snapshot.active_plan_count == 1
    assert snapshot.available_budget == 23_000_000
