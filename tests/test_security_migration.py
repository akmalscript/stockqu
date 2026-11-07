from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_hardening_migration_removes_anon_data_access_and_reserves_budget_atomically():
    sql = (ROOT / "supabase/migrations/20261107190000_p1_p2_security_hardening.sql").read_text()
    lowered = sql.lower()

    assert "grant select" in lowered and "to authenticated" in lowered
    assert "from anon, authenticated" in lowered
    assert " to anon" not in lowered
    assert "app_metadata" in lowered
    assert "auth.jwt() -> 'user_metadata'" not in lowered
    assert "pg_advisory_xact_lock" in lowered
    assert "procurement_budget_reservations" in lowered
    assert "proposed_total does not match item calculation" in lowered
    assert "exceeds authoritative available budget" in lowered
    assert "requester role is not allowed" in lowered
    assert "requester identity does not match" in lowered
    assert "v_requested_by_role is null or v_requested_by_role not in" in lowered
    assert "procurement_plans_requester_identity_check" in lowered
    assert "run_id cannot move between budget periods" in lowered
    assert lowered.index("perform pg_catalog.pg_advisory_xact_lock") < lowered.index(
        "select id, period into v_existing_plan_id"
    )
