"""CLI Agent Negotiator (P3).

    python -m scripts.run_negotiator                      # plan Purchaser terbaru, simpan draft PO
    python -m scripts.run_negotiator --purchaser-run-id PUR-...
    python -m scripts.run_negotiator --dry-run --json      # tidak menulis apa pun

Draft PO dan email TIDAK dikirim. Pengiriman hanya setelah approval manager (P4).
"""
from __future__ import annotations

import argparse
import json
import sys

from src.agents.negotiator import run_negotiator
from src.services.auth import system_actor
from src.services.supabase_client import SupabaseNotConfigured, has_write_key


def main() -> int:
    ap = argparse.ArgumentParser(description="StockQu Agent Negotiator")
    ap.add_argument("--purchaser-run-id", help="pakai plan Purchaser tertentu")
    ap.add_argument("--dry-run", action="store_true", help="jangan simpan ke Supabase")
    ap.add_argument("--json", action="store_true", help="output JSON ringkas")
    args = ap.parse_args()
    if not has_write_key():
        print("ERROR: SUPABASE_SERVICE_ROLE_KEY wajib untuk CLI/cron.", file=sys.stderr)
        return 2
    try:
        out = run_negotiator(actor=system_actor("negotiator-cli"), persist=not args.dry_run,
                             purchaser_run_id=args.purchaser_run_id)
    except SupabaseNotConfigured as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    pos = out.get("pos", [])
    if args.json:
        print(json.dumps({
            "run_id": out["run_id"], "status": out["status"], "persisted": out.get("persisted", False),
            "negotiation_id": out.get("negotiation_id"), "replanned": out.get("replanned"),
            "po_total": out.get("po_total"), "budget_limit": out.get("budget_limit"),
            "purchase_orders": [{k: p[k] for k in ("po_number", "supplier_name", "branch", "total_amount",
                                                   "expected_arrival")} for p in pos],
            "validation": out.get("validation", {}), "warnings": out.get("warnings", []),
        }, default=str))
        return 0 if out["status"] in ("completed", "needs_review", "no_action") else 1

    print(f"Run {out['run_id']} | status {out['status']} | replanned={out.get('replanned')} | persisted={out.get('persisted')}")
    for s in out["execution_trace"]:
        err = f" | {s['error_message']}" if s.get("error_message") else ""
        print(f"  [{s['status']:8}] {s['tool_name']}: {s['output_summary']}{err}")
    for c in out.get("comparisons", []):
        print(f"\n{c['item']['sku']} {c['item']['branch']} ({c['selection_status']}, {c['valid_supplier_count']} supplier valid)")
        for r in c["rows"]:
            mark = "*" if c["selected"] and r["supplier_id"] == c["selected"]["supplier_id"] else " "
            print(f"  {mark} {r['supplier_name']:11} Rp {r['effective_price'] or 0:>9,.0f} x {r['order_qty']:>4} = "
                  f"Rp {r['total_cost'] or 0:>11,.0f} | lead {r['lead_time_days']} | perf {r['performance_score']} "
                  f"| dok {r['verification']['status']}")
        print(f"    {c['reason']}")
    for po in pos:
        print(f"\nDRAFT {po['po_number']} -> {po['supplier_name']} / {po['branch']}: Rp {po['total_amount']:,.0f}, "
              f"tiba {po['expected_arrival']}")
    for w in out.get("warnings", []):
        print(f"PERINGATAN: {w}")
    return 0 if out["status"] in ("completed", "needs_review", "no_action") else 1


if __name__ == "__main__":
    raise SystemExit(main())
