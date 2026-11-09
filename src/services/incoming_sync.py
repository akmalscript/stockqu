"""Catat barang PO terkirim sebagai in-transit di Google Sheets (roadmap P4-19, SRS-F-29/44/46).

Tab terpisah (default "Incoming") supaya tidak mengganggu sinkron dua arah tab "Stok":
stok fisik (on-hand) TIDAK berubah sampai barang diterima (goods receipt, fase berikutnya).

Kolom: po_number | sku | product_name | branch | qty_incoming | supplier | expected_arrival | status | updated_at
Idempotent: kunci (po_number, sku) -> update baris yang ada, bukan menambah baris ganda.
"""
from __future__ import annotations

from datetime import datetime, timezone

from src.config import get_setting
from src.utils.retry import call_with_retry

INCOMING_HEADER = ["po_number", "sku", "product_name", "branch", "qty_incoming", "supplier",
                   "expected_arrival", "status", "updated_at"]


def incoming_tab() -> str:
    return get_setting("GOOGLE_SHEETS_INCOMING_TAB", "Incoming") or "Incoming"


def upsert_incoming_rows(sheets, rows: list[dict], sleep=None) -> dict:
    """rows: dict dengan kunci INCOMING_HEADER (tanpa updated_at). Kembalikan ringkasan."""
    tab = incoming_tab()
    kw = {"sleep": sleep} if sleep else {}
    now = datetime.now(timezone.utc).isoformat()  # noqa: UP017 - Python 3.10 compatible

    call_with_retry(lambda: sheets.ensure_tab(tab), **kw)
    values = call_with_retry(lambda: sheets.get_range(f"{tab}!A1:I"), **kw)
    if not values or [h.strip().lower() for h in values[0]] != INCOMING_HEADER:
        call_with_retry(lambda: sheets.update_range(f"{tab}!A1:I1", [INCOMING_HEADER]), **kw)
        values = [INCOMING_HEADER, *values[1:]] if values else [INCOMING_HEADER]

    index = {(r[0], r[1]): n for n, r in enumerate(values[1:], start=2) if len(r) >= 2}
    next_row = len(values) + 1
    updated = appended = 0
    for row in rows:
        line = [str(row.get(k, "")) if k != "qty_incoming" else row.get(k, 0) for k in INCOMING_HEADER[:-1]] + [now]
        key = (str(row["po_number"]), str(row["sku"]))
        target = index.get(key)
        if target is None:
            target, next_row = next_row, next_row + 1
            index[key] = target
            appended += 1
        else:
            updated += 1
        call_with_retry(lambda t=target, v=line: sheets.update_range(f"{tab}!A{t}:I{t}", [v]), **kw)
    return {"tab": tab, "appended": appended, "updated": updated}
