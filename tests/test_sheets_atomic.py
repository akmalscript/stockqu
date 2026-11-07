from src.services.sheets_client import SheetsClient


class _Call:
    def __init__(self, result):
        self.result = result

    def execute(self):
        return self.result


class _Spreadsheets:
    def __init__(self):
        self.batch_bodies = []

    def get(self, **_kwargs):
        return _Call(
            {
                "sheets": [
                    {
                        "properties": {
                            "title": "Stok",
                            "sheetId": 7,
                            "gridProperties": {"rowCount": 100},
                        }
                    }
                ]
            }
        )

    def batchUpdate(self, *, spreadsheetId, body):
        assert spreadsheetId == "sheet-id"
        self.batch_bodies.append(body)
        return _Call({"replies": [{}]})


class _Service:
    def __init__(self):
        self.api = _Spreadsheets()

    def spreadsheets(self):
        return self.api


def test_stock_snapshot_uses_one_atomic_batch_without_clear_then_update():
    service = _Service()
    client = SheetsClient(spreadsheet_id="sheet-id", stock_tab="Stok", service=service)

    written = client.write_stock_rows(
        [["SKU-A", "Item A", "Branch 1", 10, "2026-11-07T00:00:00+00:00", "supabase"]]
    )

    assert written == 2
    assert len(service.api.batch_bodies) == 1
    requests = service.api.batch_bodies[0]["requests"]
    assert [list(request) for request in requests] == [["updateCells"]]
    update = requests[0]["updateCells"]
    assert update["range"]["endRowIndex"] == 100
    assert update["fields"] == "userEnteredValue"
    assert len(update["rows"]) == 2
