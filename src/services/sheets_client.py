"""Google Sheets client - sumber stok real-time (F-01, SRS-F-01).

Mengikuti pola skill `google-sheets` (values.get / values.update, A1 notation, USER_ENTERED vs RAW),
tapi autentikasi memakai **Service Account** agar bisa jalan server-side tanpa login browser
(Streamlit Cloud / n8n / cron jam 07:00). Share spreadsheet ke email service account sebagai Editor.

Layout tab stok (default "Stok"), baris 1 = header:
    sku | product_name | branch | stock_qty | updated_at | source
"""
from __future__ import annotations

import json
from dataclasses import dataclass

from src.config import sheets_settings
from src.utils.retry import NonRetryableError

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
STOCK_HEADER = ["sku", "product_name", "branch", "stock_qty", "updated_at", "source"]


class SheetsNotConfigured(NonRetryableError):
    pass


def extract_spreadsheet_id(value: str) -> str:
    """Terima ID atau URL penuh (seperti skill google-sheets)."""
    if "/spreadsheets/d/" in value:
        return value.split("/spreadsheets/d/")[1].split("/")[0]
    return value


@dataclass
class SheetStockRow:
    row_number: int  # nomor baris di sheet (header = 1)
    sku: str
    product_name: str
    branch: str
    stock_qty: str
    updated_at: str
    source: str


class SheetsClient:
    def __init__(self, spreadsheet_id: str | None = None, stock_tab: str | None = None, service=None):
        cfg = sheets_settings()
        raw_id = spreadsheet_id or cfg["spreadsheet_id"]
        if not raw_id:
            raise SheetsNotConfigured("GOOGLE_SHEETS_SPREADSHEET_ID belum diisi")
        self.spreadsheet_id = extract_spreadsheet_id(raw_id)
        self.stock_tab = stock_tab or cfg["stock_tab"]
        self._service = service

    @property
    def service(self):
        if self._service is None:
            self._service = _build_service()
        return self._service

    # ---------- primitive (pola skill google-sheets) ----------
    def get_range(self, a1_range: str) -> list[list[str]]:
        res = (
            self.service.spreadsheets()
            .values()
            .get(spreadsheetId=self.spreadsheet_id, range=a1_range, valueRenderOption="UNFORMATTED_VALUE")
            .execute()
        )
        return [[("" if c is None else str(c)) for c in row] for row in res.get("values", [])]

    def update_range(self, a1_range: str, values: list[list], raw: bool = True) -> int:
        res = (
            self.service.spreadsheets()
            .values()
            .update(
                spreadsheetId=self.spreadsheet_id,
                range=a1_range,
                valueInputOption="RAW" if raw else "USER_ENTERED",
                body={"values": values},
            )
            .execute()
        )
        return int(res.get("updatedRows", 0))

    def clear_range(self, a1_range: str) -> None:
        self.service.spreadsheets().values().clear(spreadsheetId=self.spreadsheet_id, range=a1_range, body={}).execute()

    def get_metadata(self) -> dict:
        return self.service.spreadsheets().get(spreadsheetId=self.spreadsheet_id).execute()

    def ensure_tab(self, title: str) -> None:
        meta = self.get_metadata()
        if any(s["properties"]["title"] == title for s in meta.get("sheets", [])):
            return
        self.service.spreadsheets().batchUpdate(
            spreadsheetId=self.spreadsheet_id,
            body={"requests": [{"addSheet": {"properties": {"title": title}}}]},
        ).execute()

    # ---------- stok ----------
    def read_stock_rows(self) -> list[SheetStockRow]:
        values = self.get_range(f"{self.stock_tab}!A1:F")
        if not values:
            return []
        header = [h.strip().lower() for h in values[0]]
        idx = {name: header.index(name) if name in header else None for name in STOCK_HEADER}
        missing = [k for k in ("sku", "branch", "stock_qty") if idx[k] is None]
        if missing:
            raise ValueError(f"Header sheet '{self.stock_tab}' tidak lengkap, kolom hilang: {missing}")

        def cell(row: list[str], key: str) -> str:
            i = idx[key]
            return row[i].strip() if i is not None and i < len(row) else ""

        rows = []
        for n, row in enumerate(values[1:], start=2):
            if not any(c.strip() for c in row):
                continue
            rows.append(
                SheetStockRow(
                    row_number=n,
                    sku=cell(row, "sku"),
                    product_name=cell(row, "product_name"),
                    branch=cell(row, "branch"),
                    stock_qty=cell(row, "stock_qty"),
                    updated_at=cell(row, "updated_at"),
                    source=cell(row, "source"),
                )
            )
        return rows

    def write_stock_rows(self, rows: list[list]) -> int:
        """Tulis ulang seluruh tab stok (header + rows). Dipakai sinkron Supabase -> Sheets."""
        self.ensure_tab(self.stock_tab)
        self.clear_range(f"{self.stock_tab}!A:F")
        return self.update_range(f"{self.stock_tab}!A1", [STOCK_HEADER, *rows], raw=True)


def _build_service():
    from google.oauth2 import service_account
    from googleapiclient.discovery import build

    cfg = sheets_settings()
    if cfg["service_account_json"]:
        info = json.loads(cfg["service_account_json"])
        creds = service_account.Credentials.from_service_account_info(info, scopes=SCOPES)
    elif cfg["service_account_file"]:
        creds = service_account.Credentials.from_service_account_file(cfg["service_account_file"], scopes=SCOPES)
    else:
        raise SheetsNotConfigured("GOOGLE_SERVICE_ACCOUNT_FILE / GOOGLE_SERVICE_ACCOUNT_JSON belum diisi")
    return build("sheets", "v4", credentials=creds, cache_discovery=False)


def sheets_configured() -> bool:
    cfg = sheets_settings()
    return bool(cfg["spreadsheet_id"] and (cfg["service_account_file"] or cfg["service_account_json"]))
