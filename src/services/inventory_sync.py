"""Sinkron dua arah Google Sheets <-> Supabase untuk stok per cabang (F-01, SRS-F-01, F-46, NF-05, NF-11).

Aturan:
1. Validasi tiap baris sheet: SKU dikenal, cabang dikenal, stok angka >= 0, tidak duplikat (sku, branch).
   Baris invalid DITOLAK dan dicatat - tidak pernah ditebak.
2. Konflik (nilai stok beda): yang `updated_at`-nya lebih baru menang.
   Baris sheet tanpa `updated_at` dianggap edit manual terbaru (Sheets = sumber stok real-time).
3. Hasil gabungan ditulis balik ke Supabase (yang menang dari Sheets) dan ke Sheets (tulis ulang penuh),
   sehingga keduanya identik. Stok TIDAK pernah dijumlahkan antar cabang.
4. Setiap sinkron dicatat di `sync_logs`. Gagal tulis tidak mematikan alur (status partial/failed).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from src.utils.retry import RetryResult, call_with_retry


@dataclass
class SyncReport:
    run_id: str
    status: str = "success"  # success | partial | failed | skipped
    rows_read: int = 0
    rows_written_supabase: int = 0
    rows_written_sheets: int = 0
    rows_rejected: int = 0
    conflicts: int = 0
    rejected: list[dict] = field(default_factory=list)
    conflict_details: list[dict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    attempts_read: int = 0
    started_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    finished_at: str | None = None
    # stok final hasil gabungan: {(product_id, branch_id): {"stock_qty", "updated_at", "source"}}
    merged: dict = field(default_factory=dict)

    def to_log_row(self) -> dict:
        return {
            "run_id": self.run_id,
            "direction": "two_way",
            "status": self.status,
            "rows_read": self.rows_read,
            "rows_written": self.rows_written_supabase + self.rows_written_sheets,
            "rows_rejected": self.rows_rejected,
            "conflicts": self.conflicts,
            "detail": {
                "rows_written_supabase": self.rows_written_supabase,
                "rows_written_sheets": self.rows_written_sheets,
                "rejected": self.rejected[:50],
                "conflicts": self.conflict_details[:50],
                "attempts_read": self.attempts_read,
            },
            "error_message": "; ".join(self.errors)[:1000] or None,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }


def parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def parse_stock(value: str) -> float | None:
    try:
        qty = float(str(value).replace(",", "").strip())
    except ValueError:
        return None
    if qty < 0 or qty != qty:  # negatif atau NaN
        return None
    return qty


def sync_inventory_two_way(repo, sheets, run_id: str, now: datetime | None = None, sleep=None) -> SyncReport:
    now = now or datetime.now(timezone.utc)
    report = SyncReport(run_id=run_id)

    products = repo.fetch_products()
    branches = repo.fetch_branches()
    inventory = repo.fetch_inventory()
    sku_to_pid = {p["sku"]: p["id"] for p in products}
    pid_to_product = {p["id"]: p for p in products}
    branch_to_bid = {b["name"]: b["id"] for b in branches}
    bid_to_branch = {b["id"]: b["name"] for b in branches}

    db_state: dict[tuple[str, str], dict] = {
        (r["product_id"], r["branch_id"]): {
            "stock_qty": float(r["stock_qty"]),
            "updated_at": r.get("updated_at"),
            "source": "supabase",
        }
        for r in inventory
    }
    merged = {k: dict(v) for k, v in db_state.items()}

    # 1. Baca Sheets dengan retry terbatas (self-correction F-24)
    retry = RetryResult()
    try:
        kwargs = {"result": retry}
        if sleep is not None:
            kwargs["sleep"] = sleep
        sheet_rows = call_with_retry(sheets.read_stock_rows, **kwargs)
    except Exception as exc:  # noqa: BLE001
        report.attempts_read = retry.attempts
        report.status = "failed"
        report.errors.append(f"Baca Sheets gagal setelah {retry.attempts} percobaan: {exc}")
        report.merged = merged
        report.finished_at = datetime.now(timezone.utc).isoformat()
        _safe_log(repo, report)
        return report
    report.attempts_read = retry.attempts
    report.rows_read = len(sheet_rows)

    # 2. Validasi + resolusi konflik
    to_supabase: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for row in sheet_rows:
        reason = None
        pid = sku_to_pid.get(row.sku)
        bid = branch_to_bid.get(row.branch)
        qty = parse_stock(row.stock_qty)
        if pid is None:
            reason = f"SKU tidak dikenal: '{row.sku}'"
        elif bid is None:
            reason = f"Cabang tidak dikenal: '{row.branch}'"
        elif qty is None:
            reason = f"stock_qty tidak valid: '{row.stock_qty}'"
        elif (pid, bid) in seen:
            reason = f"Duplikat {row.sku} / {row.branch}"
        if reason:
            report.rejected.append({"row": row.row_number, "reason": reason})
            continue
        key = (pid, bid)
        seen.add(key)

        db = db_state.get(key)
        sheet_ts = parse_ts(row.updated_at)
        if db is None:
            # Kombinasi belum ada di Supabase -> ambil dari Sheets
            ts = sheet_ts or now
            merged[key] = {"stock_qty": qty, "updated_at": ts.isoformat(), "source": "sheets"}
            to_supabase.append({"product_id": pid, "branch_id": bid, "stock_qty": qty, "updated_at": ts.isoformat()})
            continue
        if abs(db["stock_qty"] - qty) < 1e-9:
            continue
        report.conflicts += 1
        db_ts = parse_ts(db["updated_at"])
        sheet_wins = sheet_ts is None or db_ts is None or sheet_ts > db_ts
        winner_ts = (sheet_ts or now) if sheet_wins else db_ts
        report.conflict_details.append(
            {
                "sku": row.sku,
                "branch": row.branch,
                "sheets": qty,
                "supabase": db["stock_qty"],
                "winner": "sheets" if sheet_wins else "supabase",
            }
        )
        if sheet_wins:
            merged[key] = {"stock_qty": qty, "updated_at": winner_ts.isoformat(), "source": "sheets"}
            to_supabase.append(
                {"product_id": pid, "branch_id": bid, "stock_qty": qty, "updated_at": winner_ts.isoformat()}
            )
    report.rows_rejected = len(report.rejected)

    # 3. Tulis ke Supabase (yang menang dari Sheets)
    if to_supabase:
        try:
            report.rows_written_supabase = call_with_retry(
                lambda: repo.upsert_inventory(to_supabase), **({"sleep": sleep} if sleep else {})
            )
        except Exception as exc:  # noqa: BLE001
            report.errors.append(f"Tulis Supabase gagal: {exc}")

    # 4. Tulis balik ke Sheets (state gabungan, urut cabang lalu SKU)
    out_rows = []
    for (pid, bid), st in sorted(
        merged.items(), key=lambda kv: (bid_to_branch.get(kv[0][1], ""), pid_to_product.get(kv[0][0], {}).get("sku", ""))
    ):
        product = pid_to_product.get(pid)
        if product is None or bid not in bid_to_branch:
            continue
        qty = st["stock_qty"]
        out_rows.append(
            [
                product["sku"],
                product["name"],
                bid_to_branch[bid],
                int(qty) if float(qty).is_integer() else qty,
                st["updated_at"] or "",
                st["source"],
            ]
        )
    try:
        written = call_with_retry(lambda: sheets.write_stock_rows(out_rows), **({"sleep": sleep} if sleep else {}))
        report.rows_written_sheets = max(0, written - 1)  # tanpa header
    except Exception as exc:  # noqa: BLE001
        report.errors.append(f"Tulis Sheets gagal: {exc}")

    if report.errors:
        report.status = "partial"
    elif report.rows_rejected:
        report.status = "partial"
    report.merged = merged
    report.finished_at = datetime.now(timezone.utc).isoformat()
    _safe_log(repo, report)
    return report


def _safe_log(repo, report: SyncReport) -> None:
    try:
        repo.insert_sync_log(report.to_log_row())
    except Exception as exc:  # noqa: BLE001 - log gagal tidak boleh mematikan alur
        report.errors.append(f"Tulis sync_logs gagal: {exc}")
