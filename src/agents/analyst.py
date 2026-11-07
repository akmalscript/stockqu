r"""Agent 1 - The Analyst (LangGraph). F-06..F-10, SRS-F-06/08/10..15.

Graph:
    sync_stock -> load_history -(ada histori?)-> forecast -> detect_risk -> explain -> persist -> END
                                  \-(tidak ada)-------------------------------------> persist

- sync_stock  : Baca Sheets (retry terbatas) + sinkron dua arah ke Supabase. Gagal -> fallback stok Supabase.
- load_history: Ambil histori penjualan 180 hari per SKU per cabang dari Supabase.
- forecast    : Rumus resmi (formulas.py) + Python forecasting tool (tren x musiman, backtest).
- detect_risk : Klasifikasi URGENT (<=4 hari / sebelum lead time) dan RISIKO (<=7 hari).
- explain     : Reason Trace terstruktur - setiap angka punya sumber. Bukan chain-of-thought.
- persist     : Simpan hasil ke analyst_forecasts + Execution Trace ke agent_logs.

Stok dan penjualan TIDAK PERNAH dijumlahkan antar cabang: unit analisis = (SKU, cabang).
"""
from __future__ import annotations

import math
from collections import defaultdict
from datetime import date, datetime, timezone
from typing import Any, Callable, TypedDict

from langgraph.graph import END, START, StateGraph

from src.config import HISTORY_DAYS, LABEL_NOT_FOUND, SAFETY_DAYS_DEFAULT
from src.services.inventory_sync import sync_inventory_two_way
from src.services.supabase_client import to_jsonable
from src.tools.forecasting import (
    build_daily_series,
    forecast_demand,
    simulate_days_to_stockout,
    stockout_range,
)
from src.utils import formulas as F
from src.utils.retry import RetryResult, call_with_retry
from src.utils.trace import ExecutionTracer, new_run_id

AGENT_NAME = "Analyst"


class AnalystState(TypedDict, total=False):
    run_id: str
    as_of: str
    products: list[dict]
    branches: list[dict]
    stock: dict  # "pid|bid" -> {"stock_qty", "updated_at", "source"}
    stock_source: str
    sync: dict
    sales: dict  # "pid|bid" -> list[[iso_date, qty]]
    history_window: list[str]
    results: list[dict]
    risk_items: list[dict]
    warnings: list[str]
    status: str
    persisted: bool


def _key(pid: str, bid: str) -> str:
    return f"{pid}|{bid}"


def _fmt_days(x: float | None) -> str:
    if x is None or (isinstance(x, float) and math.isinf(x)):
        return "tidak habis dalam horizon (tidak ada penjualan)"
    return f"{x:.1f} hari"


def _fmt_pct(x: float | None) -> str:
    if x is None:
        return LABEL_NOT_FOUND
    return f"{'naik' if x >= 0 else 'turun'} {abs(x):.1f}%"


class AnalystAgent:
    """Bungkus LangGraph Analyst. `repo` = SupabaseRepository, `sheets_factory` -> SheetsClient | None."""

    def __init__(self, repo, sheets_factory: Callable[[], Any] | None = None, persist: bool = True, sleep=None):
        self.repo = repo
        self.sheets_factory = sheets_factory
        self.persist_enabled = persist
        self.sleep = sleep
        self.tracer: ExecutionTracer | None = None

    # ------------------------------------------------------------------ nodes
    def node_sync_stock(self, state: AnalystState) -> AnalystState:
        tr = self.tracer
        warnings = list(state.get("warnings", []))
        sheets = None
        if self.sheets_factory is not None:
            try:
                sheets = self.sheets_factory()
            except Exception as exc:  # noqa: BLE001 - kredensial belum ada -> fallback
                tr.log("baca_sheets", "skipped", input_summary="Google Sheets", error_message=str(exc),
                       output_summary="Sheets belum dikonfigurasi, pakai stok Supabase")
                warnings.append(f"Google Sheets tidak dipakai: {exc}")
        if sheets is not None:
            with tr.step("sinkron_sheets_supabase", "Sheets tab stok <-> Supabase inventory") as rec:
                report = sync_inventory_two_way(self.repo, sheets, state["run_id"], sleep=self.sleep)
                rec["attempts"] = report.attempts_read or 1
                rec["output"] = (
                    f"status {report.status}: dibaca {report.rows_read}, ke Supabase {report.rows_written_supabase}, "
                    f"ke Sheets {report.rows_written_sheets}, ditolak {report.rows_rejected}, konflik {report.conflicts}"
                )
                if report.status == "failed":
                    rec["status"] = "fallback"
                    rec["error"] = "; ".join(report.errors)
                elif report.errors:
                    rec["error"] = "; ".join(report.errors)
            if report.status != "failed":
                stock = {_key(p, b): v for (p, b), v in report.merged.items()}
                return {
                    "stock": stock,
                    "stock_source": f"Google Sheets + Supabase (sinkron {state['run_id']})",
                    "sync": {k: v for k, v in report.to_log_row().items() if k != "detail"},
                    "warnings": warnings + report.errors,
                }
            warnings.append("Baca Sheets gagal setelah retry - fallback ke stok Supabase")
        with tr.step("baca_inventory_supabase", "tabel inventory") as rec:
            tracker = RetryResult()
            inv = call_with_retry(self.repo.fetch_inventory, result=tracker,
                                  **({"sleep": self.sleep} if self.sleep else {}))
            rec["attempts"] = tracker.attempts
            rec["output"] = f"{len(inv)} baris stok per SKU per cabang"
        stock = {
            _key(r["product_id"], r["branch_id"]): {
                "stock_qty": float(r["stock_qty"]),
                "updated_at": r.get("updated_at"),
                "source": "supabase",
            }
            for r in inv
        }
        return {"stock": stock, "stock_source": "Supabase inventory", "warnings": warnings}

    def node_load_history(self, state: AnalystState) -> AnalystState:
        tr = self.tracer
        retry_kw = {"sleep": self.sleep} if self.sleep else {}
        try:
            with tr.step("baca_master_data", "products, branches") as rec:
                tracker = RetryResult()
                products = call_with_retry(self.repo.fetch_products, result=tracker, **retry_kw)
                branches = call_with_retry(self.repo.fetch_branches, result=tracker, **retry_kw)
                rec["attempts"] = tracker.attempts
                rec["output"] = f"{len(products)} produk, {len(branches)} cabang"
        except Exception as exc:  # noqa: BLE001
            return {"products": [], "branches": [], "sales": {}, "status": "load_failed",
                    "warnings": [*state.get("warnings", []), f"Baca master data gagal: {exc}"]}
        with tr.step("baca_histori_penjualan", f"sales_history {HISTORY_DAYS} hari") as rec:
            no_hist = {"products": products, "branches": branches, "sales": {}, "status": "no_history"}
            try:
                as_of = date.fromisoformat(state["as_of"]) if state.get("as_of") else self.repo.latest_sales_date()
                rows = self.repo.fetch_sales_history(as_of, HISTORY_DAYS) if as_of else []
            except Exception as exc:  # noqa: BLE001 - mis. tabel belum dibuat (PGRST205)
                rec["status"], rec["output"] = "failed", LABEL_NOT_FOUND
                rec["error"] = f"Baca sales_history gagal (jalankan migrasi 002 + seed?): {exc}"
                return no_hist
            if as_of is None or not rows:
                rec["status"], rec["output"] = "failed", LABEL_NOT_FOUND
                rec["error"] = "Tabel sales_history kosong untuk periode ini (jalankan seed 002)"
                return no_hist
            sales: dict[str, list] = defaultdict(list)
            for r in rows:
                sales[_key(r["product_id"], r["branch_id"])].append([r["sale_date"], float(r["qty"])])
            start = date.fromordinal(as_of.toordinal() - HISTORY_DAYS + 1)
            rec["output"] = f"{len(rows)} baris, {len(sales)} kombinasi SKU-cabang, periode {start} s/d {as_of}"
        return {
            "products": products,
            "branches": branches,
            "sales": dict(sales),
            "as_of": as_of.isoformat(),
            "history_window": [start.isoformat(), as_of.isoformat()],
            "status": "history_loaded",
        }

    def node_forecast(self, state: AnalystState) -> AnalystState:
        tr = self.tracer
        as_of = date.fromisoformat(state["as_of"])
        win = state["history_window"]
        branches = {b["id"]: b["name"] for b in state["branches"]}
        results: list[dict] = []
        with tr.step("python_forecasting_tool", f"{len(state['products'])} SKU x {len(branches)} cabang") as rec:
            for p in state["products"]:
                for bid, bname in branches.items():
                    key = _key(p["id"], bid)
                    stock_row = state["stock"].get(key)
                    sales = state["sales"].get(key, [])
                    lead = int(p.get("lead_time_days") or 0)
                    safety_days = int(p.get("safety_days") or SAFETY_DAYS_DEFAULT)
                    base = {
                        "product_id": p["id"],
                        "branch_id": bid,
                        "sku": p["sku"],
                        "product_name": p["name"],
                        "branch": bname,
                        "lead_time_days": lead,
                        "safety_days": safety_days,
                        "margin": p.get("margin"),
                        "turnover_rate": p.get("turnover_rate"),
                        "as_of_date": state["as_of"],
                    }
                    if stock_row is None or not sales:
                        results.append({**base, "risk_level": F.RISK_NO_DATA,
                                        "missing": "stok" if stock_row is None else "histori penjualan"})
                        continue
                    stock = float(stock_row["stock_qty"])
                    series, dates, quality = build_daily_series(
                        [(date.fromisoformat(d), q) for d, q in sales], as_of, HISTORY_DAYS
                    )
                    total = float(series.sum())
                    avg = F.avg_daily_sales(total, HISTORY_DAYS)
                    dts = F.days_to_stockout(stock, avg)
                    ss = F.safety_stock(avg, safety_days)
                    need_raw = F.kebutuhan_restock(avg, lead, stock, safety_days)
                    qty = F.restock_qty(avg, lead, stock, safety_days)
                    fc = forecast_demand(series, dates, quality, horizon=max(30, lead + safety_days + 7))
                    dts_fc = simulate_days_to_stockout(stock, fc.projection)
                    lo, hi = stockout_range(stock, fc.projection, fc.residual_std)
                    qty_fc = F.restock_qty(fc.forecast_daily_demand, lead, stock, safety_days)
                    results.append({
                        **base,
                        "stock_qty": stock,
                        "stock_updated_at": stock_row.get("updated_at"),
                        "stock_origin": stock_row.get("source", "supabase"),
                        "total_sales_180d": total,
                        "avg_daily_sales": avg,
                        "days_to_stockout": dts,
                        "safety_stock": ss,
                        "kebutuhan_restock_raw": need_raw,
                        "restock_qty": qty,
                        "restock_qty_forecast": qty_fc,
                        "forecast_daily_demand": fc.forecast_daily_demand,
                        "days_to_stockout_forecast": dts_fc,
                        "days_to_stockout_low": lo,
                        "days_to_stockout_high": hi,
                        "forecast": fc.to_dict(),
                        "history_window": win,
                    })
            rec["output"] = f"{len(results)} kombinasi SKU-cabang diproses"
        return {"results": results}

    def node_detect_risk(self, state: AnalystState) -> AnalystState:
        tr = self.tracer
        with tr.step("deteksi_risiko_stockout", "ambang URGENT <= 4 hari, RISIKO <= 7 hari") as rec:
            results = []
            for r in state["results"]:
                if r.get("risk_level") == F.RISK_NO_DATA:
                    results.append(r)
                    continue
                level = F.classify_risk(r["days_to_stockout"], r["lead_time_days"])
                fc_level = F.classify_risk(r["days_to_stockout_forecast"], r["lead_time_days"])
                results.append({
                    **r,
                    "risk_level": level,
                    "forecast_warning": level == F.RISK_SAFE and fc_level != F.RISK_SAFE,
                })
            risk = sorted(
                [r for r in results if r["risk_level"] in (F.RISK_URGENT, F.RISK_WARNING)],
                key=lambda r: r["days_to_stockout"],
            )
            n_urgent = sum(r["risk_level"] == F.RISK_URGENT for r in risk)
            n_nodata = sum(r["risk_level"] == F.RISK_NO_DATA for r in results)
            rec["output"] = f"{len(risk)} item berisiko ({n_urgent} URGENT), {n_nodata} {LABEL_NOT_FOUND}"
        return {"results": results, "risk_items": [_risk_view(r) for r in risk]}

    def node_explain(self, state: AnalystState) -> AnalystState:
        tr = self.tracer
        with tr.step("susun_reason_trace", "evidence + asumsi per SKU-cabang") as rec:
            results = [{**r, "reason_trace": build_reason_trace(r, state.get("stock_source", ""))} for r in state["results"]]
            risk_keys = {(x["product_id"], x["branch_id"]) for x in state.get("risk_items", [])}
            risk_items = [_risk_view(r) for r in sorted(
                [r for r in results if (r["product_id"], r["branch_id"]) in risk_keys],
                key=lambda r: r["days_to_stockout"],
            )]
            rec["output"] = f"{len(results)} reason trace dibuat"
        return {"results": results, "risk_items": risk_items, "status": "completed"}

    def node_persist(self, state: AnalystState) -> AnalystState:
        tr = self.tracer
        warnings = list(state.get("warnings", []))
        persisted = False
        if self.persist_enabled:
            rows = [_to_db_row(state["run_id"], r) for r in state.get("results", []) if r.get("risk_level") != F.RISK_NO_DATA]
            try:
                with tr.step("update_database", "analyst_forecasts") as rec:
                    n = self.repo.insert_forecasts(rows)
                    rec["output"] = f"{n} baris forecast disimpan"
                persisted = True
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"Simpan analyst_forecasts gagal: {exc}")
        final_status = state.get("status") or "completed"
        tr.log("selesai", "success" if final_status == "completed" else "failed",
               input_summary=f"run {state['run_id']}",
               output_summary=f"status {final_status}, {len(state.get('risk_items', []))} item berisiko")
        if self.persist_enabled:
            try:
                self.repo.insert_agent_logs([s.to_db_row() for s in tr.steps])
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"Simpan agent_logs gagal: {exc}")
        return {"persisted": persisted, "warnings": warnings, "status": final_status}

    # ------------------------------------------------------------------ graph
    def build_graph(self):
        g = StateGraph(AnalystState)
        g.add_node("sync_stock", self.node_sync_stock)
        g.add_node("load_history", self.node_load_history)
        g.add_node("forecast", self.node_forecast)
        g.add_node("detect_risk", self.node_detect_risk)
        g.add_node("explain", self.node_explain)
        g.add_node("persist", self.node_persist)
        g.add_edge(START, "sync_stock")
        g.add_edge("sync_stock", "load_history")
        g.add_conditional_edges(
            "load_history",
            lambda s: "forecast" if s.get("status") == "history_loaded" else "persist",
            {"forecast": "forecast", "persist": "persist"},
        )
        g.add_edge("forecast", "detect_risk")
        g.add_edge("detect_risk", "explain")
        g.add_edge("explain", "persist")
        g.add_edge("persist", END)
        return g.compile()

    def run(self, as_of: date | None = None, run_id: str | None = None) -> dict:
        run_id = run_id or new_run_id("ANL")
        self.tracer = ExecutionTracer(AGENT_NAME, run_id)
        init: AnalystState = {"run_id": run_id, "warnings": []}
        if as_of:
            init["as_of"] = as_of.isoformat()
        final = self.build_graph().invoke(init)
        final["execution_trace"] = [s.to_dict() for s in self.tracer.steps]
        return final


# ---------------------------------------------------------------------- helpers
def _risk_view(r: dict) -> dict:
    keys = ["product_id", "branch_id", "sku", "product_name", "branch", "stock_qty", "avg_daily_sales",
            "days_to_stockout", "days_to_stockout_forecast", "days_to_stockout_low", "days_to_stockout_high",
            "forecast_daily_demand", "safety_stock", "restock_qty", "lead_time_days", "risk_level", "reason_trace"]
    return {k: r.get(k) for k in keys}


def build_reason_trace(r: dict, stock_source: str) -> dict:
    """Reason Trace terstruktur (F-10, SRS-F-15, NF-06). Semua angka + sumbernya, tanpa kalimat generik."""
    label = f"{r['product_name']} ({r['sku']}) di {r['branch']}"
    if r.get("risk_level") == F.RISK_NO_DATA:
        return {
            "summary": f"{label}: {LABEL_NOT_FOUND} - {r.get('missing', 'data')} tidak tersedia, tidak diprediksi.",
            "evidence": [],
            "assumptions": [],
        }
    fc = r["forecast"]
    q = fc["quality"]
    win = r["history_window"]
    stock_src = (
        "Google Sheets tab stok (menang konflik sinkron)" if r.get("stock_origin") == "sheets"
        else f"Supabase inventory ({stock_source})"
    )
    trend_txt = (
        f"Penjualan 7 hari terakhir {_fmt_pct(fc['trend_7d_pct'])} dibanding 7 hari sebelumnya; "
        f"28 hari terakhir {_fmt_pct(fc['trend_28d_pct'])} dibanding 28 hari sebelumnya."
    )
    if fc.get("peak_weekday"):
        trend_txt += f" Pola mingguan: puncak hari {fc['peak_weekday']} (indeks {max(fc['weekly_index']):.2f}x rata-rata)."
    pred_txt = (
        f"Rumus resmi: {r['stock_qty']:.0f} / {r['avg_daily_sales']:.2f} = {_fmt_days(r['days_to_stockout'])}. "
        f"Simulasi forecast ({fc['model_name']}): {_fmt_days(r['days_to_stockout_forecast'])} "
        f"(rentang {_fmt_days(r['days_to_stockout_low'])} - {_fmt_days(r['days_to_stockout_high'])})."
    )
    if r["restock_qty"] > 0:
        restock_txt = (
            f"Butuh {r['restock_qty']} unit = ({r['avg_daily_sales']:.2f} x ({r['lead_time_days']} + {r['safety_days']})) "
            f"- {r['stock_qty']:.0f}, termasuk safety stock {r['safety_stock']:.1f} unit. "
            f"Versi forecast: {r['restock_qty_forecast']} unit."
        )
    else:
        restock_txt = "Kebutuhan restock <= 0, tidak diusulkan pembelian."
    level = r.get("risk_level", "")
    summary = f"{label}: {level}, diprediksi habis dalam {_fmt_days(r['days_to_stockout'])}"
    if level in (F.RISK_URGENT, F.RISK_WARNING):
        summary += f", butuh {r['restock_qty']} unit sebelum lead time {r['lead_time_days']} hari + safety {r['safety_days']} hari."
        if fc["trend_7d_pct"] is not None:
            summary += f" Penjualan 7 hari terakhir {_fmt_pct(fc['trend_7d_pct'])}."
    elif r.get("forecast_warning"):
        summary += f"; PERINGATAN DINI: forecast tren memprediksi {_fmt_days(r['days_to_stockout_forecast'])}."
    else:
        summary += "."
    model_txt = (
        f"Backtest 14 hari: MAPE model {fc['model_mape']:.1f}% vs baseline rata-rata 180 hari {fc['baseline_mape']:.1f}%."
        if fc.get("model_mape") is not None and fc.get("baseline_mape") is not None
        else "Backtest tidak tersedia (data kurang)."
    )
    return {
        "summary": summary,
        "demand_trend": trend_txt,
        "stockout_prediction": pred_txt,
        "restock_recommendation": restock_txt,
        "evidence": [
            {"label": "Stok saat ini", "value": r["stock_qty"], "source": f"{stock_src}, updated_at {r.get('stock_updated_at')}"},
            {"label": "Total penjualan 180 hari", "value": r["total_sales_180d"], "source": f"Supabase sales_history {win[0]} s/d {win[1]}"},
            {"label": "Rata-rata penjualan harian", "value": round(r["avg_daily_sales"], 3), "source": "Analyst: total / 180"},
            {"label": "Forecast permintaan 7 hari (rata-rata/hari)", "value": round(r["forecast_daily_demand"], 3), "source": f"Analyst: {fc['model_name']}"},
            {"label": "Lead time supplier (hari)", "value": r["lead_time_days"], "source": "Supabase products.lead_time_days"},
            {"label": "Safety days", "value": r["safety_days"], "source": "Supabase products.safety_days"},
            {"label": "Margin (%)", "value": r.get("margin"), "source": "Supabase products.margin"},
            {"label": "Turnover rate", "value": r.get("turnover_rate"), "source": "Supabase products.turnover_rate"},
        ],
        "assumptions": [
            "rata_rata_penjualan_harian = total_penjualan_6_bulan / 180 (AGENTS.md)",
            "Status risiko memakai rumus resmi; forecast tren dipakai sebagai evidence & peringatan dini.",
            "URGENT jika <= 4 hari atau stok habis sebelum lead time; RISIKO jika <= 7 hari.",
            model_txt,
        ],
        "data_quality": {
            "observed_days": q["observed_days"],
            "missing_days": q["missing_days"],
            "anomaly_days": q["anomaly_days"][:10],
            "needs_validation": q["needs_validation"],
            "notes": q["notes"],
        },
    }


def _to_db_row(run_id: str, r: dict) -> dict:
    fc = r["forecast"]
    return to_jsonable({
        "run_id": run_id,
        "product_id": r["product_id"],
        "branch_id": r["branch_id"],
        "as_of_date": r["as_of_date"],
        "stock_qty": r["stock_qty"],
        "total_sales_180d": r["total_sales_180d"],
        "avg_daily_sales": round(r["avg_daily_sales"], 4),
        "forecast_daily_demand": round(r["forecast_daily_demand"], 4),
        "days_to_stockout": r["days_to_stockout"],
        "days_to_stockout_forecast": r["days_to_stockout_forecast"],
        "days_to_stockout_low": r["days_to_stockout_low"],
        "days_to_stockout_high": r["days_to_stockout_high"],
        "safety_stock": round(r["safety_stock"], 3),
        "restock_qty": r["restock_qty"],
        "lead_time_days": r["lead_time_days"],
        "risk_level": r["risk_level"],
        "trend_pct": fc["trend_7d_pct"],
        "model_name": fc["model_name"],
        "model_mape": fc["model_mape"],
        "baseline_mape": fc["baseline_mape"],
        "data_quality": r["reason_trace"].get("data_quality", {}),
        "reason_trace": {**r["reason_trace"], "projection": fc["projection"][:30], "projection_start": fc["projection_start"],
                         "forecast_warning": r.get("forecast_warning", False)},
        "created_at": datetime.now(timezone.utc).isoformat(),
    })


def run_analyst(persist: bool = True, use_sheets: bool = True, as_of: date | None = None) -> dict:
    """Entry point untuk Streamlit / CLI / n8n (P4)."""
    from src.services.sheets_client import SheetsClient, sheets_configured
    from src.services.supabase_client import SupabaseRepository

    factory = SheetsClient if (use_sheets and sheets_configured()) else None
    if use_sheets and factory is None:
        def factory():  # noqa: E306 - catat alasan skip ke trace
            raise RuntimeError("GOOGLE_SHEETS_SPREADSHEET_ID / service account belum diisi")
    return AnalystAgent(SupabaseRepository(), sheets_factory=factory, persist=persist).run(as_of=as_of)
