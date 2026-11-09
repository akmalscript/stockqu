"""KPI analitik (basis aktual/backtest/target) dan ekspor P5. CONTOH SIMULASI - BUKAN DATA REAL."""
import io

from openpyxl import load_workbook

from src.services.analytics import compute_kpis, risk_by_branch
from src.services.exports import (
    analyst_xlsx,
    comparison_pdf_bytes,
    procurement_report_xlsx,
)
from tests.test_pages import DRAFT, OFFERS, TRACE

FORECASTS = [
    {"product_id": "pA", "branch_id": "b1", "risk_level": "URGENT", "stock_qty": 100, "total_sales_180d": 5400,
     "model_mape": 7.0, "baseline_mape": 15.0},
    {"product_id": "pZ", "branch_id": "b2", "risk_level": "AMAN", "stock_qty": 50, "total_sales_180d": 0,
     "model_mape": None, "baseline_mape": None},
]


def kpis(**over):
    args = {"forecasts": FORECASTS, "products": [{"turnover_rate": 1.0}, {"turnover_rate": 2.0}],
            "contracts": [{"product_id": "pZ", "price": 1000}, {"product_id": "pZ", "price": 900}],
            "budget": {"total_budget": 50_000_000, "used_budget": 38_000_000, "committed_budget": 2_000_000},
            "purchase_orders": [{"status": "sent"}, {"status": "draft"}],
            "jobs": [{"run_id": "WF-1", "started_at": "2026-11-09T00:00:00+00:00"}],
            "approvals": [{"workflow_run_id": "WF-1", "created_at": "2026-11-09T00:12:00+00:00",
                           "decided_at": "2026-11-09T00:42:00+00:00", "status": "approved"}],
            "logs": [{"tool_name": "a", "status": "success"}, {"tool_name": "b", "status": "failed"},
                     {"tool_name": "c", "status": "retried"}, {"tool_name": "selesai", "status": "success"}]}
    args.update(over)
    return {k.key: k for k in compute_kpis(**args)}


def test_kpis_are_computed_from_data_with_basis():
    k = kpis()
    assert k["stockout_risk"].value == 1 and k["stockout_risk"].basis == "aktual"
    assert k["inventory_turnover"].value == 1.5
    assert k["dead_stock_value"].value == 50 * 900  # stok tanpa penjualan x harga kontrak terendah
    assert k["budget_utilization"].value == 80.0
    assert k["procurement_cycle"].value == 12.0 and k["approval_latency"].value == 30.0
    assert k["forecast_mape"].value == 7.0 and k["forecast_mape"].basis == "backtest"
    assert round(k["tool_success"].value, 1) == 66.7 and k["retries"].value == 1
    assert k["target_stockout"].basis == "target" and k["target_hours"].value == 15


def test_kpis_without_data_are_not_invented():
    k = kpis(forecasts=[], products=[], budget=None, jobs=[], approvals=[], logs=[], purchase_orders=[])
    for key in ("stockout_risk", "inventory_turnover", "dead_stock_value", "budget_utilization",
                "procurement_cycle", "approval_latency", "forecast_mape", "tool_success", "po_sent"):
        assert k[key].value is None, key


def test_risk_by_branch_counts_per_branch():
    rows = risk_by_branch(FORECASTS, {"b1": "Branch 1", "b2": "Branch 2"})
    assert rows == [{"Cabang": "Branch 1", "URGENT": 1, "RISIKO": 0, "AMAN": 0},
                    {"Cabang": "Branch 2", "URGENT": 0, "RISIKO": 0, "AMAN": 1}]


def test_exports_produce_valid_files():
    comp = {"sku": "SKU-A", "product_name": "Item A", "branch": "Branch 1", "offers": OFFERS,
            "selected_supplier_id": "sA", "selection_status": "selected", "reason_trace": TRACE}
    assert comparison_pdf_bytes([comp], True, "NEG-1").startswith(b"%PDF")
    xlsx = procurement_report_xlsx(plan={"run_id": "PUR-1", "period": "2026-11"},
                                   plan_items=[{"rank": 1, "sku": "SKU-A", "branch": "Branch 1", "decision": "selected",
                                                "allocated_qty": 80, "line_total": 960000, "reason_trace": TRACE}],
                                   comparisons=[comp], pos=[DRAFT], is_dummy=True)
    wb = load_workbook(io.BytesIO(xlsx))
    assert wb.sheetnames == ["Ringkasan", "Item PO", "Matriks Supplier", "Alasan Prioritas"]
    assert wb["Ringkasan"]["A1"].value == "CONTOH SIMULASI - BUKAN DATA REAL"
    assert load_workbook(io.BytesIO(analyst_xlsx([{"sku": "SKU-A", "risk_level": "URGENT"}], True))).sheetnames == ["Prediksi"]
