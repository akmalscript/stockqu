from src.services.dashboard import load_dashboard_snapshot


class DashboardRepository:
    def fetch_products(self):
        return [
            {"id": "p1", "sku": "SKU-1", "name": "Produk 1"},
            {"id": "p2", "sku": "SKU-2", "name": "Produk 2"},
        ]

    def fetch_branches(self):
        return [
            {"id": "b1", "name": "Branch 1", "city": ""},
            {"id": "b2", "name": "Branch 2", "city": ""},
        ]

    def fetch_inventory(self):
        return [
            {"id": "i1", "product_id": "p1", "branch_id": "b1", "stock_qty": 10},
            {"id": "i2", "product_id": "p1", "branch_id": "b2", "stock_qty": 90},
        ]

    def fetch_suppliers(self):
        return [
            {
                "id": "s1",
                "name": "Supplier 1",
                "lead_time_days": 2,
                "performance_score": 4.5,
            }
        ]

    def fetch_reference_contracts(self):
        return [
            {
                "id": "c1",
                "product_id": "p1",
                "supplier_id": "s1",
                "price": 1000,
            }
        ]

    def fetch_sync_logs(self, limit):
        return [{"status": "success", "rows_read": 2, "started_at": "2026-11-08T00:00:00Z"}]

    def latest_analyst_run_id(self):
        return "ANL-1"

    def fetch_forecasts(self, run_id):
        assert run_id == "ANL-1"
        return [
            {
                "product_id": "p1",
                "branch_id": "b2",
                "days_to_stockout": 6,
                "risk_level": "RISIKO",
            },
            {
                "product_id": "p1",
                "branch_id": "b1",
                "days_to_stockout": 2,
                "risk_level": "URGENT",
            },
        ]

    def fetch_budget(self, period):
        assert period == "2026-11"
        return {
            "total_budget": 50_000_000,
            "used_budget": 10_000_000,
            "remaining_budget": 35_000_000,
        }

    def fetch_purchase_orders_for_period(self, period):
        return [{"status": "waiting_approval", "total_amount": 5_000_000}]

    def fetch_active_budget_reservations(self, period):
        return []

    def latest_purchaser_run_id(self):
        return None

    def has_dummy_sales(self):
        return False

    def fetch_agent_logs(self, run_id):
        return [{"run_id": run_id, "status": "success"}]


def test_dashboard_uses_real_rows_without_merging_branch_stock():
    snapshot = load_dashboard_snapshot(DashboardRepository(), "2026-11")

    assert [(row["branch"], row["stock_qty"]) for row in snapshot.inventory] == [
        ("Branch 1", 10),
        ("Branch 2", 90),
    ]
    assert [row["branch"] for row in snapshot.forecasts] == ["Branch 1", "Branch 2"]
    assert snapshot.forecasts[0]["sku"] == "SKU-1"
    assert snapshot.budget["available_budget"] == 35_000_000
    assert snapshot.contracts[0]["supplier_name"] == "Supplier 1"
    assert snapshot.agent_logs[0]["run_id"] == "ANL-1"
    assert not snapshot.has_dummy_data


def test_dashboard_does_not_invent_budget_when_row_is_missing():
    repository = DashboardRepository()
    repository.fetch_budget = lambda period: None

    snapshot = load_dashboard_snapshot(repository, "2026-11")

    assert snapshot.budget is None
