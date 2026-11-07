from src.agents.purchaser import PurchaserAgent


class PurchaserRepo:
    def __init__(self, include_second_contract: bool = True):
        self.persisted = None
        self.logs = []
        self.include_second_contract = include_second_contract

    def fetch_budget(self, period):
        assert period == "2026-11"
        return {
            "month": period,
            "total_budget": 50_000_000,
            "used_budget": 49_000_000,
            "remaining_budget": 1_000_000,
        }

    def fetch_purchase_orders_for_period(self, period):
        return []

    def latest_analyst_run_id(self):
        return "ANL-TEST"

    def fetch_forecasts(self, run_id):
        assert run_id == "ANL-TEST"
        return [
            {
                "product_id": "pA", "branch_id": "b1", "as_of_date": "2026-11-06",
                "restock_qty": 8, "risk_level": "URGENT", "days_to_stockout": 3.0,
                "forecast_daily_demand": 4.0, "lead_time_days": 3,
            },
            {
                "product_id": "pB", "branch_id": "b1", "as_of_date": "2026-11-06",
                "restock_qty": 10, "risk_level": "RISIKO", "days_to_stockout": 6.0,
                "forecast_daily_demand": 2.0, "lead_time_days": 4,
            },
            {
                "product_id": "pA", "branch_id": "b2", "as_of_date": "2026-11-06",
                "restock_qty": 0, "risk_level": "AMAN", "days_to_stockout": 30.0,
                "forecast_daily_demand": 4.0, "lead_time_days": 3,
            },
        ]

    def fetch_products(self):
        return [
            {"id": "pA", "sku": "SKU-A", "name": "Item A", "margin": 30, "turnover_rate": 3.0},
            {"id": "pB", "sku": "SKU-B", "name": "Item B", "margin": 20, "turnover_rate": 2.0},
        ]

    def fetch_branches(self):
        return [
            {"id": "b1", "name": "Branch 1", "city": "Jawa Tengah"},
            {"id": "b2", "name": "Branch 2", "city": "Jawa Tengah"},
            {"id": "b3", "name": "Branch 3", "city": "Jawa Tengah"},
        ]

    def fetch_reference_contracts(self):
        rows = [
            {
                "id": "cA", "supplier_id": "sA", "product_id": "pA", "price": 100_000,
                "discount": 0, "valid_until": "2026-12-31", "moq": 2, "pack_size": 2,
                "created_at": "2026-11-01T00:00:00+00:00",
            }
        ]
        if self.include_second_contract:
            rows.append({
                "id": "cB", "supplier_id": "sB", "product_id": "pB", "price": 100_000,
                "discount": 0, "valid_until": "2026-12-31", "moq": 5, "pack_size": 5,
                "created_at": "2026-11-01T00:00:00+00:00",
            })
        return rows

    def persist_procurement_plan(self, plan, items):
        self.persisted = (plan, items)
        return plan["id"]

    def insert_agent_logs(self, rows):
        self.logs.extend(rows)
        return len(rows)


def test_purchaser_graph_persists_grounded_budget_safe_plan():
    repo = PurchaserRepo()
    output = PurchaserAgent(repo, persist=True).run(period="2026-11", run_id="PUR-TEST")
    assert output["status"] in {"completed", "budget_insufficient"}
    assert output["validation"]["valid"]
    assert output["optimization"]["proposed_total"] <= 1_000_000
    assert output["persisted"]
    assert repo.persisted is not None
    assert {item["branch_id"] for item in output["items"]} == {"b1"}
    assert all(item["reason_trace"]["evidence"] for item in output["items"])
    assert any(log["tool_name"] == "optimize_budget_allocation" for log in repo.logs)


def test_purchaser_defers_item_when_reference_price_missing():
    repo = PurchaserRepo(include_second_contract=False)
    output = PurchaserAgent(repo, persist=False).run(period="2026-11", run_id="PUR-NO-PRICE")
    item_b = next(item for item in output["items"] if item["product_id"] == "pB")
    assert item_b["decision"] == "deferred"
    assert item_b["allocated_qty"] == 0
    assert item_b["unit_price"] is None
    assert "DATA TIDAK DITEMUKAN" in item_b["reason_trace"]["summary"]
