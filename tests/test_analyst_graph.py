from src.agents.analyst import AnalystAgent
from src.utils import formulas as F
from tests.fakes import AS_OF, FakeRepo, FakeSheets

no_sleep = lambda s: None


def _by_key(result):
    return {(r["sku"], r["branch"]): r for r in result["results"]}


def test_analyst_detects_risk_per_branch_with_trace():
    repo = FakeRepo()
    out = AnalystAgent(repo, sheets_factory=None).run()
    assert out["status"] == "completed"
    assert out["as_of"] == AS_OF.isoformat()

    res = _by_key(out)
    a1 = res[("SKU-A", "Branch 1")]
    assert a1["risk_level"] == F.RISK_URGENT
    assert abs(a1["days_to_stockout"] - 100 / a1["avg_daily_sales"]) < 1e-9
    assert a1["restock_qty"] == F.restock_qty(a1["avg_daily_sales"], 3, 100, 3)
    assert res[("SKU-B", "Branch 1")]["risk_level"] == F.RISK_WARNING
    assert res[("SKU-A", "Branch 2")]["risk_level"] == F.RISK_SAFE  # stok cabang tidak digabung
    assert res[("SKU-Z", "Branch 3")]["risk_level"] == F.RISK_NO_DATA

    skus = [(r["sku"], r["branch"]) for r in out["risk_items"]]
    assert skus == [("SKU-A", "Branch 1"), ("SKU-B", "Branch 1")]  # urut days-to-stockout

    trace = a1["reason_trace"]
    assert "URGENT" in trace["summary"]
    sources = {e["label"]: e["source"] for e in trace["evidence"]}
    assert "sales_history" in sources["Total penjualan 180 hari"]
    assert "products.lead_time_days" in sources["Lead time supplier (hari)"]


def test_persists_forecasts_and_execution_trace():
    repo = FakeRepo()
    out = AnalystAgent(repo, sheets_factory=None).run(run_id="ANL-TEST")
    assert out["persisted"] is True
    assert len(repo.forecasts) == 8  # 9 kombinasi - 1 tanpa data
    assert all(f["run_id"] == "ANL-TEST" for f in repo.forecasts)
    tools = [log["tool_name"] for log in repo.agent_logs]
    assert tools[:2] == ["baca_inventory_supabase", "baca_master_data"]
    assert "python_forecasting_tool" in tools and tools[-1] == "selesai"


def test_sheets_failure_falls_back_to_supabase():
    repo = FakeRepo()
    agent = AnalystAgent(repo, sheets_factory=lambda: FakeSheets(fail_reads=99), sleep=no_sleep)
    out = agent.run()
    assert out["status"] == "completed"
    assert out["stock_source"] == "Supabase inventory"
    steps = {s["tool_name"]: s for s in out["execution_trace"]}
    assert steps["sinkron_sheets_supabase"]["status"] == "fallback"
    assert steps["sinkron_sheets_supabase"]["attempts"] == 3


def test_sheets_value_used_when_newer():
    repo = FakeRepo()
    sheets = FakeSheets([["SKU-B", "Item B", "Branch 2", "15", "2026-11-07T06:59:00+00:00", "sheets"]])
    out = AnalystAgent(repo, sheets_factory=lambda: sheets, sleep=no_sleep).run()
    b2 = _by_key(out)[("SKU-B", "Branch 2")]
    assert b2["stock_qty"] == 15 and b2["risk_level"] == F.RISK_URGENT
    assert "Google Sheets" in b2["reason_trace"]["evidence"][0]["source"]


def test_no_history_routes_to_persist_with_not_found():
    repo = FakeRepo(with_sales=False)
    out = AnalystAgent(repo, sheets_factory=None).run()
    assert out["status"] == "no_history"
    assert out.get("risk_items", []) == []
    failed = next(s for s in out["execution_trace"] if s["tool_name"] == "baca_histori_penjualan")
    assert failed["status"] == "failed" and "DATA TIDAK DITEMUKAN" in failed["output_summary"]


def test_write_failure_does_not_crash():
    repo = FakeRepo(fail_writes=True)
    out = AnalystAgent(repo, sheets_factory=None).run()
    assert out["status"] == "completed" and out["persisted"] is False
    assert any("analyst_forecasts" in w for w in out["warnings"])


def test_dry_run_keeps_all_external_sinks_read_only():
    repo = FakeRepo()
    sheets = FakeSheets([["SKU-A", "Item A", "Branch 1", "75", "2026-11-07T06:59:00+00:00", "sheets"]])
    out = AnalystAgent(repo, sheets_factory=lambda: sheets, persist=False, sleep=no_sleep).run()

    assert out["status"] == "completed"
    assert repo.upserts == []
    assert repo.forecasts == []
    assert repo.agent_logs == []
    assert repo.sync_logs == []
    assert sheets.written is None
