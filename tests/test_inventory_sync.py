from datetime import datetime, timezone

from src.services.inventory_sync import sync_inventory_two_way
from tests.fakes import FakeRepo, FakeSheets

NOW = datetime(2026, 11, 7, 7, 0, tzinfo=timezone.utc)
no_sleep = lambda s: None  # noqa: E731


def test_empty_sheet_gets_initialized_from_supabase():
    repo, sheets = FakeRepo(), FakeSheets()
    rep = sync_inventory_two_way(repo, sheets, "RUN-1", now=NOW, sleep=no_sleep)
    assert rep.status == "success"
    assert rep.rows_written_sheets == len(repo.inventory)
    assert repo.upserts == []
    assert repo.sync_logs and repo.sync_logs[0]["direction"] == "two_way"


def test_newer_sheet_value_wins_and_goes_to_supabase():
    repo = FakeRepo()
    sheets = FakeSheets([["SKU-A", "Item A", "Branch 1", "75", "2026-11-07T06:30:00+00:00", "sheets"]])
    rep = sync_inventory_two_way(repo, sheets, "RUN-2", now=NOW, sleep=no_sleep)
    assert rep.conflicts == 1
    assert repo.upserts == [
        {"product_id": "pA", "branch_id": "b1", "stock_qty": 75.0, "updated_at": "2026-11-07T06:30:00+00:00"}
    ]
    assert rep.merged[("pA", "b1")]["stock_qty"] == 75.0
    # Branch 2 tidak berubah / tidak dijumlahkan
    assert rep.merged[("pA", "b2")]["stock_qty"] == 800


def test_older_sheet_value_loses():
    repo = FakeRepo()
    sheets = FakeSheets([["SKU-A", "Item A", "Branch 1", "75", "2026-11-01T00:00:00+00:00", "sheets"]])
    rep = sync_inventory_two_way(repo, sheets, "RUN-3", now=NOW, sleep=no_sleep)
    assert rep.conflicts == 1 and repo.upserts == []
    assert rep.merged[("pA", "b1")]["stock_qty"] == 100
    # sheet ditulis ulang dengan nilai Supabase
    row = next(r for r in sheets.written if r[0] == "SKU-A" and r[2] == "Branch 1")
    assert row[3] == 100


def test_invalid_rows_rejected_not_guessed():
    repo = FakeRepo()
    sheets = FakeSheets([
        ["SKU-XXX", "?", "Branch 1", "10", "", ""],
        ["SKU-A", "Item A", "Gudang Lain", "10", "", ""],
        ["SKU-B", "Item B", "Branch 1", "-5", "", ""],
        ["SKU-B", "Item B", "Branch 2", "abc", "", ""],
    ])
    rep = sync_inventory_two_way(repo, sheets, "RUN-4", now=NOW, sleep=no_sleep)
    assert rep.rows_rejected == 4
    assert rep.status == "partial"
    assert repo.upserts == []


def test_read_retry_then_success():
    repo, sheets = FakeRepo(), FakeSheets(fail_reads=2)
    rep = sync_inventory_two_way(repo, sheets, "RUN-5", now=NOW, sleep=no_sleep)
    assert rep.status == "success" and rep.attempts_read == 3


def test_read_fails_after_retries_returns_failed_without_crash():
    repo, sheets = FakeRepo(), FakeSheets(fail_reads=99)
    rep = sync_inventory_two_way(repo, sheets, "RUN-6", now=NOW, sleep=no_sleep)
    assert rep.status == "failed" and rep.attempts_read == 3
    assert rep.merged[("pA", "b1")]["stock_qty"] == 100
    assert repo.sync_logs[0]["status"] == "failed"
