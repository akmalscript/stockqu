"""Jalankan Agent Analyst dari CLI (dipakai juga oleh n8n cron 07:00 di P4).

    python -m scripts.run_analyst            # sinkron Sheets (jika dikonfigurasi) + simpan ke Supabase
    python -m scripts.run_analyst --dry-run  # tidak menulis ke Supabase
    python -m scripts.run_analyst --no-sheets
"""
from __future__ import annotations

import argparse
import json
import math
import sys

from src.agents.analyst import run_analyst
from src.services.auth import system_actor
from src.services.supabase_client import SupabaseNotConfigured, has_write_key


def _d(x) -> str:
    return "-" if x is None or (isinstance(x, float) and math.isinf(x)) else f"{x:.2f}"


def main() -> int:
    ap = argparse.ArgumentParser(description="StockQu Agent Analyst")
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="baca dan hitung saja; jangan menulis Supabase, Sheets, forecast, atau log",
    )
    ap.add_argument("--no-sheets", action="store_true", help="lewati sinkron Google Sheets")
    ap.add_argument("--json", action="store_true", help="output JSON ringkas (untuk n8n)")
    args = ap.parse_args()

    persist = not args.dry_run
    if not has_write_key():
        print(
            "ERROR: SUPABASE_SERVICE_ROLE_KEY wajib untuk read CLI/cron; gunakan UI untuk read via JWT user.",
            file=sys.stderr,
        )
        return 2
    try:
        out = run_analyst(
            actor=system_actor("analyst-cli"),
            persist=persist,
            use_sheets=not args.no_sheets,
        )
    except SupabaseNotConfigured as exc:
        print(f"ERROR: {exc}. Copy .env.example ke .env lalu isi nilainya.", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps({
            "run_id": out["run_id"],
            "status": out["status"],
            "persisted": out.get("persisted", False),
            "risk_items": [
                {k: (None if isinstance(v, float) and math.isinf(v) else v) for k, v in r.items() if k != "reason_trace"}
                | {"summary": r["reason_trace"]["summary"]}
                for r in out.get("risk_items", [])
            ],
            "warnings": out.get("warnings", []),
        }, default=str))
        return 0 if out["status"] == "completed" else 1

    print(f"Run {out['run_id']} | status {out['status']} | sumber stok: {out.get('stock_source')}")
    for s in out["execution_trace"]:
        err = f" | ERROR: {s['error_message']}" if s.get("error_message") else ""
        print(f"  [{s['status']:8}] {s['tool_name']}: {s['output_summary']}{err}")
    print(f"\nItem berisiko: {len(out.get('risk_items', []))}")
    for r in out.get("risk_items", []):
        print(f"  {r['risk_level']:7} {r['sku']:8} {r['branch']:9} stok {r['stock_qty']:.0f} | "
              f"dts {_d(r['days_to_stockout'])} hari (forecast {_d(r['days_to_stockout_forecast'])}) | restock {r['restock_qty']}")
        print(f"          {r['reason_trace']['summary']}")
    for w in out.get("warnings", []):
        print(f"PERINGATAN: {w}")
    return 0 if out["status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
