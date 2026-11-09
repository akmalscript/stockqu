"""KPI enterprise (F-33, SRS-F-50, NF-12, DESIGN 16.1).

Setiap KPI diberi ``basis``: ``aktual`` (dihitung dari tabel), ``backtest`` (evaluasi model),
atau ``target`` (klaim SRS yang belum terukur). Tidak ada angka karangan: data kosong ->
nilai None -> UI menulis DATA TIDAK DITEMUKAN.
"""
from __future__ import annotations

import math
import statistics
from dataclasses import asdict, dataclass
from datetime import datetime


@dataclass
class Kpi:
    key: str
    label: str
    value: float | None
    unit: str
    basis: str  # aktual | backtest | target
    method: str
    sample: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


def _ts(value) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))  # noqa: FURB162 - Python 3.10 compatible
    except ValueError:
        return None


def _num(value) -> float | None:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(v) or math.isinf(v) else v


def compute_kpis(*, forecasts: list[dict], products: list[dict], contracts: list[dict], budget: dict | None,
                 purchase_orders: list[dict], jobs: list[dict], approvals: list[dict], logs: list[dict]) -> list[Kpi]:
    kpis: list[Kpi] = []

    # Risiko stockout (aktual, dari Analyst terakhir) - per SKU-cabang, tidak dijumlah antar cabang
    risky = [f for f in forecasts if f.get("risk_level") in ("URGENT", "RISIKO")]
    kpis.append(Kpi("stockout_risk", "SKU-cabang berisiko habis <= 7 hari",
                    len(risky) if forecasts else None, "SKU-cabang", "aktual",
                    "analyst_forecasts run terakhir, risk_level URGENT/RISIKO", len(forecasts)))

    # Inventory turnover rata-rata (data master)
    turnovers = [v for v in (_num(p.get("turnover_rate")) for p in products) if v is not None]
    kpis.append(Kpi("inventory_turnover", "Inventory turnover rata-rata",
                    round(statistics.mean(turnovers), 2) if turnovers else None, "x", "aktual",
                    "rata-rata products.turnover_rate", len(turnovers)))

    # Dead stock: stok tanpa penjualan 180 hari (dari Analyst) x harga referensi terendah
    prices: dict[str, float] = {}
    for c in contracts:
        p = _num(c.get("price"))
        if p and p > 0:
            pid = str(c["product_id"])
            prices[pid] = min(prices.get(pid, p), p)
    dead = [f for f in forecasts if (_num(f.get("total_sales_180d")) or 0) == 0 and (_num(f.get("stock_qty")) or 0) > 0]
    dead_value = sum((_num(f["stock_qty"]) or 0) * prices.get(str(f["product_id"]), 0) for f in dead)
    kpis.append(Kpi("dead_stock_value", "Nilai dead stock (tanpa penjualan 180 hari)",
                    round(dead_value, 2) if forecasts else None, "Rp", "aktual",
                    "stok x harga kontrak terendah untuk SKU-cabang dengan total_sales_180d = 0", len(dead)))

    # Utilisasi budget
    if budget and _num(budget.get("total_budget")):
        total = _num(budget["total_budget"])
        used = (_num(budget.get("used_budget")) or 0) + (_num(budget.get("committed_budget")) or 0)
        kpis.append(Kpi("budget_utilization", "Utilisasi budget bulan berjalan", round(100 * used / total, 1),
                        "%", "aktual", "(used + committed PO) / total budget", 1))
    else:
        kpis.append(Kpi("budget_utilization", "Utilisasi budget bulan berjalan", None, "%", "aktual",
                        "budgets + purchase_orders", 0))

    # Siklus pengadaan: start job -> approval request dibuat (draft PO siap)
    by_run = {j["run_id"]: j for j in jobs}
    cycles = []
    latencies = []
    for a in approvals:
        job = by_run.get(a.get("workflow_run_id"))
        start, created = _ts(job.get("started_at")) if job else None, _ts(a.get("created_at"))
        if start and created and created >= start:
            cycles.append((created - start).total_seconds() / 60)
        decided = _ts(a.get("decided_at"))
        if created and decided and a.get("status") in ("approved", "rejected", "revision_requested"):
            latencies.append((decided - created).total_seconds() / 60)
    kpis.append(Kpi("procurement_cycle", "Siklus deteksi -> draft PO (median)",
                    round(statistics.median(cycles), 1) if cycles else None, "menit", "aktual",
                    "workflow_jobs.started_at -> approval_requests.created_at", len(cycles)))
    kpis.append(Kpi("approval_latency", "Latensi approval manager (median)",
                    round(statistics.median(latencies), 1) if latencies else None, "menit", "aktual",
                    "approval_requests.created_at -> decided_at", len(latencies)))

    # Akurasi forecast: backtest holdout 14 hari (Analyst)
    mapes = [v for v in (_num(f.get("model_mape")) for f in forecasts) if v is not None]
    base = [v for v in (_num(f.get("baseline_mape")) for f in forecasts) if v is not None]
    kpis.append(Kpi("forecast_mape", "Error forecast (MAPE median)", round(statistics.median(mapes), 1) if mapes else None,
                    "%", "backtest", "analyst_forecasts.model_mape, holdout 14 hari", len(mapes)))
    kpis.append(Kpi("baseline_mape", "Error baseline rata-rata 180 hari (MAPE median)",
                    round(statistics.median(base), 1) if base else None, "%", "backtest",
                    "analyst_forecasts.baseline_mape", len(base)))

    # Keandalan agent
    tool_logs = [g for g in logs if g.get("tool_name") != "selesai"]
    ok = sum(g.get("status") in ("success", "retried", "fallback", "skipped") for g in tool_logs)
    kpis.append(Kpi("tool_success", "Tool call berhasil / tertangani", round(100 * ok / len(tool_logs), 1) if tool_logs else None,
                    "%", "aktual", "agent_logs status success/retried/fallback/skipped", len(tool_logs)))
    retries = sum(g.get("status") == "retried" for g in tool_logs)
    kpis.append(Kpi("retries", "Tool call yang di-retry", retries if tool_logs else None, "kali", "aktual",
                    "agent_logs status retried", len(tool_logs)))

    # PO terkirim setelah approval (human-in-the-loop terpenuhi)
    sent = [p for p in purchase_orders if p.get("status") == "sent"]
    kpis.append(Kpi("po_sent", "PO terkirim setelah approval", len(sent) if purchase_orders else None, "PO",
                    "aktual", "purchase_orders.status = sent", len(purchase_orders)))

    # Target SRS - ditampilkan sebagai target, bukan hasil ukur
    kpis.append(Kpi("target_stockout", "Target pengurangan stockout", 80, "%", "target",
                    "Target SRS; dibuktikan lewat simulasi replay di P6"))
    kpis.append(Kpi("target_hours", "Target hemat cek stok manual", 15, "jam/minggu", "target",
                    "Target SRS; asumsi dibuktikan di P6"))
    kpis.append(Kpi("target_dead_stock", "Target pengurangan dead stock", 30, "%", "target", "Target SRS"))
    return kpis


def risk_by_branch(forecasts: list[dict], branches: dict[str, str]) -> list[dict]:
    """Jumlah SKU berisiko per cabang (dihitung per cabang, bukan menjumlah stok)."""
    out: dict[str, dict] = {}
    for f in forecasts:
        name = branches.get(str(f.get("branch_id")), str(f.get("branch_id")))
        row = out.setdefault(name, {"Cabang": name, "URGENT": 0, "RISIKO": 0, "AMAN": 0})
        level = f.get("risk_level")
        if level in row:
            row[level] += 1
    return sorted(out.values(), key=lambda r: r["Cabang"])
