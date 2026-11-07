"""CLI Agent Purchaser.

    python -m scripts.run_purchaser
    python -m scripts.run_purchaser --period 2026-11 --dry-run --json
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime

from src.agents.purchaser import run_purchaser
from src.services.auth import system_actor
from src.services.supabase_client import SupabaseNotConfigured, has_write_key


def main() -> int:
    parser = argparse.ArgumentParser(description="StockQu Agent Purchaser")
    parser.add_argument("--period", help="periode budget YYYY-MM")
    parser.add_argument("--analyst-run-id", help="pakai run Analyst tertentu")
    parser.add_argument("--dry-run", action="store_true", help="jangan simpan ke Supabase")
    parser.add_argument("--json", action="store_true", help="output JSON ringkas")
    args = parser.parse_args()
    if args.period:
        try:
            datetime.strptime(args.period, "%Y-%m")
        except ValueError:
            print("ERROR: --period harus berformat YYYY-MM", file=sys.stderr)
            return 2
    persist = not args.dry_run
    if not has_write_key():
        print(
            "ERROR: SUPABASE_SERVICE_ROLE_KEY wajib untuk read CLI/cron; gunakan UI untuk read via JWT user.",
            file=sys.stderr,
        )
        return 2
    try:
        output = run_purchaser(
            actor=system_actor("purchaser-cli"),
            persist=persist,
            period=args.period,
            analyst_run_id=args.analyst_run_id,
        )
    except SupabaseNotConfigured as exc:
        print(f"ERROR: {exc}. Isi .env lokal atau Streamlit secrets.", file=sys.stderr)
        return 2

    summary = {
        "run_id": output["run_id"],
        "analyst_run_id": output.get("analyst_run_id"),
        "period": output["period"],
        "status": output.get("status"),
        "persisted": output.get("persisted", False),
        "budget": output.get("budget", {}),
        "optimization": output.get("optimization", {}),
        "impact": output.get("impact", {}),
        "validation": output.get("validation", {}),
        "warnings": output.get("warnings", []),
    }
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, default=str))
    else:
        print(
            f"Run {summary['run_id']} | Analyst {summary['analyst_run_id']} | "
            f"status {summary['status']} | persisted={summary['persisted']}"
        )
        print(
            f"Budget tersedia Rp {summary['budget'].get('available_budget', 0):,.0f} | "
            f"usulan Rp {summary['optimization'].get('proposed_total', 0):,.0f}"
        )
        for item in output.get("items", []):
            print(
                f"  #{item.get('rank')} {item.get('sku')} | {item.get('branch')} | "
                f"{item.get('decision')} {item.get('allocated_qty', 0)}/{item.get('requested_qty', 0)} unit"
            )
        for warning in summary["warnings"]:
            print(f"PERINGATAN: {warning}")
    return 1 if summary["status"] == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
