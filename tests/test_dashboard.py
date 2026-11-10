from src.services.dashboard import load_dashboard_snapshot, sync_forecasts_with_inventory


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


def test_sync_forecasts_with_inventory_recalculates_dts_and_risk():
    forecasts = [
        {
            "product_id": "p1",
            "branch_id": "b1",
            "stock_qty": 50.0,
            "avg_daily_sales": 30.0,
            "days_to_stockout": 1.67,
            "risk_level": "URGENT",
            "restock_qty": 130,
        },
        {
            "product_id": "p2",
            "branch_id": "b1",
            "stock_qty": 120.0,
            "avg_daily_sales": 24.0,
            "days_to_stockout": 5.0,
            "risk_level": "RISIKO",
            "restock_qty": 48,
        },
    ]
    # In inventory, SKU-1 stock changed to 150 (restocked), SKU-2 stock dropped to 48 (depleted)
    inventory = [
        {"product_id": "p1", "branch_id": "b1", "stock_qty": 150},
        {"product_id": "p2", "branch_id": "b1", "stock_qty": 48},
    ]
    products = [
        {"id": "p1", "sku": "SKU-1", "lead_time_days": 3},
        {"id": "p2", "sku": "SKU-2", "lead_time_days": 4},
    ]

    synced = sync_forecasts_with_inventory(forecasts, inventory, products)

    # p1: stock=150, avg=30 -> dts=5.0 -> RISIKO (no longer URGENT)
    f1 = next(f for f in synced if f["product_id"] == "p1")
    assert f1["stock_qty"] == 150.0
    assert f1["days_to_stockout"] == 5.0
    assert f1["risk_level"] == "RISIKO"

    # p2: stock=48, avg=24 -> dts=2.0 -> URGENT (now URGENT)
    f2 = next(f for f in synced if f["product_id"] == "p2")
    assert f2["stock_qty"] == 48.0
    assert f2["days_to_stockout"] == 2.0
    assert f2["risk_level"] == "URGENT"

