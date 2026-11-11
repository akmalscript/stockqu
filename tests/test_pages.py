"""Smoke test UI P5: router + 11 halaman render tanpa exception untuk tiap role.

Repository palsu berisi data kecil yang meniru hasil Analyst/Purchaser/Negotiator/P4.
CONTOH SIMULASI - BUKAN DATA REAL.
"""
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from src.services import auth, supabase_client
from src.services.auth import ActorContext

ROOT = Path(__file__).resolve().parents[1]
PAGES = ["command_center", "inventori", "prediksi", "rencana", "perbandingan", "po_approval",
         "reason_trace", "execution_log", "analitik", "ekspor", "admin"]
TRACE = {"summary": "SKU-A URGENT, habis 3.3 hari", "evidence": [{"label": "Stok", "value": 100, "source": "Supabase inventory"}],
         "assumptions": ["rata-rata = total/180"]}
OFFERS = [{"supplier_id": s, "supplier_name": n, "status": "valid", "list_price": p, "discount_pct": 0,
           "effective_price": p, "moq": 1, "pack_size": 1, "order_qty": 81, "total_cost": 81 * p, "lead_time_days": lt,
           "arrives_before_stockout": lt <= 3.3, "performance_score": 90, "verification": {"status": "verified"},
           "source": "Supabase contracts"} for s, n, p, lt in (("sA", "Supplier A", 12000, 3), ("sB", "Supplier B", 11270, 4),
                                                                ("sC", "Supplier C", 12500, 5))]
DRAFT = {"po_number": "PO-1", "supplier_id": "sA", "supplier_name": "Supplier A", "branch_id": "b1", "branch": "Branch 1",
         "total_amount": 972000, "lead_time_days": 3, "expected_arrival": "2026-11-12", "approval_status": "-",
         "items": [{"product_id": "pA", "sku": "SKU-A", "product_name": "Item A", "qty": 81, "list_price": 12000,
                    "discount_pct": 0, "unit_price": 12000, "line_total": 972000}],
         "emails": {"purchase_order": {"subject": "PO", "body": "isi", "status": "DRAFT"}}}
LOG_ROWS = [{"created_at": "2026-11-09T00:00:00+00:00", "agent_name": a, "run_id": r, "tool_name": t, "status": s,
             "input_summary": "", "output_summary": f"ok [durasi {d} ms, percobaan 1]", "error_message": None,
             "actor_role": "system_agent"}
            for a, r, t, s, d in (("Analyst", "ANL-1", "python_forecasting_tool", "success", 120),
                                  ("Purchaser", "PUR-1", "optimize_budget_allocation", "retried", 80),
                                  ("Orchestrator", "WF-1", "request_approval", "success", 30))]


class UIRepo:
    def __init__(self, read_client=None, write_client=None):
        pass

    def fetch_products(self):
        return [{"id": "pA", "sku": "SKU-A", "name": "Item A", "category": "Kategori A", "margin": 18,
                 "turnover_rate": 1.2, "lead_time_days": 3, "safety_days": 3}]

    def fetch_branches(self):
        return [{"id": "b1", "name": "Branch 1", "city": "Jawa Tengah"}, {"id": "b2", "name": "Branch 2", "city": "Jawa Tengah"}]

    def fetch_inventory(self):
        return [{"id": "i1", "product_id": "pA", "branch_id": "b1", "stock_qty": 100, "updated_at": "2026-11-09T00:00:00+00:00"},
                {"id": "i2", "product_id": "pA", "branch_id": "b2", "stock_qty": 800, "updated_at": "2026-11-09T00:00:00+00:00"}]

    def fetch_inventory_detailed(self):
        return [{**r, "incoming_qty": 81 if r["branch_id"] == "b1" else 0} for r in self.fetch_inventory()]

    def fetch_suppliers(self):
        return [{"id": s, "name": n, "contact": "-", "email": None, "lead_time_days": 3, "performance_score": 90}
                for s, n in (("sA", "Supplier A"), ("sB", "Supplier B"), ("sC", "Supplier C"))]

    def fetch_reference_contracts(self):
        return [{"id": f"k{s}", "supplier_id": s, "product_id": "pA", "price": 12000, "discount": 0,
                 "valid_until": "2026-12-31", "moq": 1, "pack_size": 1, "created_at": "2026-11-07"} for s in ("sA", "sB", "sC")]

    def latest_analyst_run_id(self):
        return "ANL-1"

    def fetch_forecasts(self, run_id):
        base = {"run_id": "ANL-1", "product_id": "pA", "as_of_date": "2026-11-08", "total_sales_180d": 5400,
                "avg_daily_sales": 30.0, "forecast_daily_demand": 32.0, "safety_stock": 90, "lead_time_days": 3,
                "model_mape": 7.3, "baseline_mape": 14.9, "model_name": "linear", "trend_pct": 2.1,
                "reason_trace": {**TRACE, "projection": [32.0] * 30, "projection_start": "2026-11-09"}}
        return [{**base, "branch_id": "b1", "stock_qty": 100, "days_to_stockout": 3.3, "days_to_stockout_forecast": 3.1,
                 "days_to_stockout_low": 2.9, "days_to_stockout_high": 3.4, "restock_qty": 80, "risk_level": "URGENT"},
                {**base, "branch_id": "b2", "stock_qty": 800, "days_to_stockout": 26.7, "days_to_stockout_forecast": 25.0,
                 "days_to_stockout_low": 24.0, "days_to_stockout_high": 27.0, "restock_qty": 0, "risk_level": "AMAN"}]

    def fetch_sales_series(self, product_id, branch_id, start, end):
        return [{"sale_date": f"2026-11-0{d}", "qty": 30} for d in range(1, 9)]

    def fetch_budget(self, period):
        return {"month": period, "total_budget": 50_000_000, "used_budget": 38_000_000, "remaining_budget": 12_000_000}

    def fetch_purchase_orders_for_period(self, period):
        return []

    def fetch_active_budget_reservations(self, period):
        return []

    def latest_purchaser_run_id(self):
        return "PUR-1"

    def fetch_procurement_plan(self, run_id):
        return {"id": "plan-1", "run_id": "PUR-1", "analyst_run_id": "ANL-1", "period": "2026-11", "status": "completed",
                "budget_total": 50_000_000, "budget_used": 38_000_000, "budget_committed": 0, "budget_allocated": 0,
                "budget_available": 12_000_000, "proposed_total": 960_000, "remaining_after_plan": 11_040_000,
                "optimization_method": "milp", "impact": {}, "validation": {"valid": True, "errors": []},
                "created_at": "2026-11-09T00:00:00+00:00"}

    def fetch_procurement_plans(self, limit=20):
        return [self.fetch_procurement_plan("PUR-1")]

    def fetch_procurement_plan_items(self, plan_id):
        return [{"plan_id": "plan-1", "product_id": "pA", "branch_id": "b1", "rank": 1, "decision": "selected",
                 "requested_qty": 80, "allocated_qty": 80, "unit_price": 12000, "moq": 1, "pack_size": 1,
                 "line_total": 960000, "priority_score": 0.9, "risk_level": "URGENT", "reason_trace": TRACE,
                 "evidence": TRACE["evidence"]}]

    def latest_negotiation_run(self):
        return {"id": "neg-1", "run_id": "NEG-1", "plan_id": "plan-1", "status": "completed", "po_total": 972000}

    def fetch_negotiation_runs(self, limit=20):
        return [self.latest_negotiation_run()]

    def fetch_supplier_comparisons(self, run_uuid):
        return [{"product_id": "pA", "branch_id": "b1", "order_qty": 81, "offers": OFFERS, "valid_supplier_count": 3,
                 "selected_supplier_id": "sA", "selected_contract_id": "ksA", "selection_status": "selected",
                 "citations": [{"quote": "Pasal 2 ... SKU-A", "document_title": "Kontrak A", "version": 1,
                                "clause_ref": "Pasal 2", "page": 1, "valid_until": "2026-12-31"}],
                 "reason_trace": TRACE}]

    def fetch_draft_purchase_orders(self, run_uuid):
        return [{"id": "po1", "po_number": "PO-1", "status": "waiting_approval", "draft": DRAFT, "total_amount": 972000}]

    def fetch_supplier_documents(self):
        return [{"id": "d1", "supplier_id": "sA", "doc_type": "contract", "title": "Kontrak A", "version": 1,
                 "page_count": 1, "valid_until": "2026-12-31", "source": "DUMMY-SEED"}]

    def fetch_workflow_jobs(self, limit=10):
        return [{"run_id": "WF-1", "trigger": "cron", "status": "waiting_approval", "started_at": "2026-11-09T00:00:00+00:00",
                 "analyst_run_id": "ANL-1", "purchaser_run_id": "PUR-1", "negotiator_run_id": "NEG-1",
                 "approval_code": "APR-20261109-01", "finished_at": None, "error_message": None,
                 "summary": {"risk_count": 1, "po_count": 1, "dispatch": []}}]

    def fetch_approval_requests(self, limit=20):
        return [{"approval_code": "APR-20261109-01", "workflow_run_id": "WF-1", "total_amount": 972000, "item_count": 1,
                 "status": "pending", "message": "Balas OK APR-20261109-01", "notification_status": "sent",
                 "expires_at": "2026-11-10T00:00:00+00:00", "created_at": "2026-11-09T00:05:00+00:00"}]

    def fetch_recent_purchase_orders(self, limit=30):
        return [{"po_number": "PO-1", "supplier_id": "sA", "branch_id": "b1", "total_amount": 972000,
                 "status": "waiting_approval", "approval_code": "APR-20261109-01", "expected_arrival": "2026-11-12"}]

    def fetch_agent_logs(self, run_id):
        return [r for r in LOG_ROWS if r["run_id"] == run_id] or LOG_ROWS[:1]

    def fetch_recent_agent_logs(self, limit=500, agent=None, run_id=None):
        return [r for r in LOG_ROWS if (not agent or r["agent_name"] == agent) and (not run_id or r["run_id"] == run_id)]

    def fetch_sync_logs(self, limit=10):
        return []

    def has_dummy_sales(self):
        return True

    def fetch_admin_rows(self, table):
        return {"products": self.fetch_products(), "suppliers": self.fetch_suppliers(),
                "contracts": self.fetch_reference_contracts(),
                "budgets": [{"id": "bg1", **self.fetch_budget("2026-11")}]}[table]

    def fetch_audit_log(self, limit=100):
        return [{"created_at": "2026-11-09T00:00:00+00:00", "table_name": "products", "action": "UPDATE",
                 "row_id": "pA", "actor_id": "u1", "actor_role": "super_admin"}]


@pytest.fixture
def ui(monkeypatch):
    # UI smoke tests use isolated fakes and must not inherit a developer's
    # production environment, whose readiness gate intentionally stops the app.
    monkeypatch.setenv("STOCKQU_ENV", "test")
    monkeypatch.setenv("STOCKQU_AGENT_SERVICE_URL", "https://agent.test")
    monkeypatch.setenv("N8N_TO_SERVICE_TOKEN", "token")

    def login_as(role: str):
        actor = ActorContext("u1", "user@test", role, branch_ids=("b1",))
        monkeypatch.setattr(auth, "require_streamlit_access", lambda roles, action: (actor, object()))
        monkeypatch.setattr(supabase_client, "SupabaseRepository", UIRepo)
        at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=60)
        at.run()
        assert not at.exception, [e.value for e in at.exception]
        return at
    return login_as


@pytest.mark.parametrize("page", PAGES)
def test_every_page_renders_for_super_admin(ui, page):
    at = ui("super_admin")
    at.switch_page(f"app_pages/{page}.py").run()
    assert not at.exception, [e.value for e in at.exception]
    assert at.title, f"{page} tanpa judul"


def test_router_menu_follows_role(ui):
    assert (ROOT / "app_pages").is_dir() and not (ROOT / "pages").exists()
    staff = ui("purchasing_staff")
    with pytest.raises(ValueError, match="Could not find a navigation page"):
        staff.switch_page("app_pages/admin.py")  # menu Admin tidak terdaftar untuk staff
    with pytest.raises(ValueError):
        ui("warehouse_manager").switch_page("app_pages/rencana.py")  # RLS plan tidak untuk warehouse manager
    manager = ui("warehouse_manager")
    manager.switch_page("app_pages/po_approval.py").run()
    assert not manager.exception
    assert any(b.label == "Setujui (OK)" for b in manager.button)


def test_po_approval_hides_decision_buttons_for_staff(ui):
    at = ui("purchasing_staff")
    at.switch_page("app_pages/po_approval.py").run()
    assert not any(b.label == "Setujui (OK)" for b in at.button)


def test_p5_migration_enforces_admin_writes_in_rls():
    sql = (ROOT / "supabase/migrations/20261110100000_p5_admin_audit.sql").read_text().lower()
    assert "super_admin write" in sql and "with check" in sql
    assert "revoke all on table public.audit_log from anon, authenticated, service_role" in sql
    assert "security definer" in sql and "auth.uid()" in sql
    assert "total_budget <= 50000000" in sql
    assert " to anon" not in sql and "'user_metadata'" not in sql


def test_admin_page_denies_direct_access(monkeypatch):
    actor = ActorContext("u2", "staff@test", "purchasing_staff")
    monkeypatch.setattr(auth, "require_streamlit_access", lambda roles, action: (actor, object()))
    monkeypatch.setattr(supabase_client, "SupabaseRepository", UIRepo)
    at = AppTest.from_file(str(ROOT / "app_pages/admin.py"), default_timeout=60).run()
    assert at.error and "tidak boleh" in at.error[0].value
    assert not at.dataframe  # tidak ada data master yang dirender
