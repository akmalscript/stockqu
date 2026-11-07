"""Python forecasting tool untuk Agent Analyst (F-06, F-07, SRS-F-08/09/10/11/14).

Model: tren linear (8 minggu terakhir) x indeks musiman mingguan (dari 180 hari).
- Dievaluasi dengan backtest holdout 14 hari terhadap baseline resmi StockQu
  (rata-rata 6 bulan = total/180). Model dipakai hanya jika MAPE-nya lebih baik (SRS-F-09).
- days-to-stockout versi forecast dihitung dengan simulasi penghabisan stok harian,
  plus rentang ketidakpastian (SRS-F-11).
- Deteksi kualitas data & anomali: hari hilang, nilai negatif, lonjakan z-score > 3 (SRS-F-14, NF-11).

Rumus resmi (src/utils/formulas.py) TETAP jadi dasar status risiko; forecast adalah evidence tambahan.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta

import numpy as np

TREND_WINDOW = 56  # 8 minggu
HOLDOUT_DAYS = 14
MAX_SIM_DAYS = 365


@dataclass
class DataQuality:
    expected_days: int
    observed_days: int
    missing_days: int
    negative_values: int
    anomaly_days: list[str] = field(default_factory=list)
    needs_validation: bool = False
    notes: list[str] = field(default_factory=list)


@dataclass
class ForecastResult:
    model_name: str
    forecast_daily_demand: float  # rata-rata proyeksi 7 hari ke depan
    projection: list[float]  # proyeksi harian ke depan (horizon)
    projection_start: str
    daily_trend_slope: float
    weekly_index: list[float]  # Senin..Minggu
    residual_std: float
    model_mape: float | None
    baseline_mape: float | None
    trend_7d_pct: float | None  # 7 hari terakhir vs 7 hari sebelumnya
    trend_28d_pct: float | None  # 28 hari terakhir vs 28 hari sebelumnya
    peak_weekday: str | None
    quality: DataQuality

    def to_dict(self) -> dict:
        return asdict(self)


WEEKDAYS_ID = ["Senin", "Selasa", "Rabu", "Kamis", "Jumat", "Sabtu", "Minggu"]


def build_daily_series(records: list[tuple[date, float]], as_of: date, days: int) -> tuple[np.ndarray, list[date], DataQuality]:
    """Susun deret harian lengkap [as_of-days+1 .. as_of]. Hari tanpa data = 0 dan dicatat sebagai missing."""
    start = as_of - timedelta(days=days - 1)
    dates = [start + timedelta(days=i) for i in range(days)]
    pos = {d: i for i, d in enumerate(dates)}
    values = np.zeros(days, dtype=float)
    seen = np.zeros(days, dtype=bool)
    negatives = 0
    for d, q in records:
        i = pos.get(d)
        if i is None:
            continue
        if q < 0:
            negatives += 1
            q = 0.0
        values[i] += float(q)
        seen[i] = True
    quality = DataQuality(
        expected_days=days,
        observed_days=int(seen.sum()),
        missing_days=int((~seen).sum()),
        negative_values=negatives,
    )
    if quality.missing_days:
        quality.notes.append(f"{quality.missing_days} hari tanpa data penjualan (dianggap 0)")
    if negatives:
        quality.notes.append(f"{negatives} nilai negatif diabaikan")
    return values, dates, quality


def _weekly_index(values: np.ndarray, dates: list[date]) -> np.ndarray:
    overall = values.mean()
    idx = np.ones(7)
    if overall <= 0:
        return idx
    for wd in range(7):
        sel = [v for v, d in zip(values, dates) if d.weekday() == wd]
        if sel:
            idx[wd] = np.mean(sel) / overall
    idx = idx / idx.mean()  # normalisasi rata-rata 1
    return idx


def _fit(values: np.ndarray, dates: list[date]):
    """Kembalikan (level_akhir, slope, weekly_idx, residual_std) dari data deseasonalized."""
    widx = _weekly_index(values, dates)
    deseason = np.array([v / widx[d.weekday()] for v, d in zip(values, dates)])
    window = deseason[-TREND_WINDOW:] if len(deseason) >= TREND_WINDOW else deseason
    x = np.arange(len(window), dtype=float)
    if len(window) >= 2:
        slope, intercept = np.polyfit(x, window, 1)
    else:
        slope, intercept = 0.0, float(window.mean()) if len(window) else 0.0
    fitted = intercept + slope * x
    resid_std = float(np.std(window - fitted)) if len(window) > 2 else 0.0
    level_end = intercept + slope * (len(window) - 1)
    return float(level_end), float(slope), widx, resid_std


def _project(level_end: float, slope: float, widx: np.ndarray, first_date: date, horizon: int) -> list[float]:
    out = []
    for h in range(1, horizon + 1):
        d = first_date + timedelta(days=h - 1)
        out.append(max(0.0, (level_end + slope * h) * widx[d.weekday()]))
    return out


def _mape(actual: np.ndarray, pred: np.ndarray) -> float | None:
    mask = actual > 0
    if not mask.any():
        return None
    return float(np.mean(np.abs(actual[mask] - pred[mask]) / actual[mask]) * 100)


def _pct_change(recent: float, before: float) -> float | None:
    if before <= 0:
        return None
    return (recent - before) / before * 100


def forecast_demand(values: np.ndarray, dates: list[date], quality: DataQuality, horizon: int = 30) -> ForecastResult:
    n = len(values)
    baseline_avg = float(values.sum() / n) if n else 0.0

    # Anomali: z-score > 3 terhadap residual musiman
    if n >= 14 and values.std() > 0:
        z = (values - values.mean()) / values.std()
        quality.anomaly_days = [dates[i].isoformat() for i in np.where(np.abs(z) > 3)[0]]
        if quality.anomaly_days:
            quality.notes.append(f"{len(quality.anomaly_days)} hari lonjakan/penurunan tidak wajar (|z|>3)")
    quality.needs_validation = quality.missing_days > 0.1 * quality.expected_days or len(quality.anomaly_days) > 5

    # Backtest holdout (SRS-F-09)
    model_mape = baseline_mape = None
    use_model = True
    if n >= TREND_WINDOW + HOLDOUT_DAYS:
        train_v, test_v = values[:-HOLDOUT_DAYS], values[-HOLDOUT_DAYS:]
        train_d = dates[:-HOLDOUT_DAYS]
        lvl, slp, widx, _ = _fit(train_v, train_d)
        pred_model = np.array(_project(lvl, slp, widx, dates[-HOLDOUT_DAYS], HOLDOUT_DAYS))
        pred_base = np.full(HOLDOUT_DAYS, train_v.mean())
        model_mape = _mape(test_v, pred_model)
        baseline_mape = _mape(test_v, pred_base)
        if model_mape is not None and baseline_mape is not None:
            use_model = model_mape <= baseline_mape
    else:
        use_model = False
        quality.notes.append("Data < 70 hari, forecast memakai baseline rata-rata")

    first_future = dates[-1] + timedelta(days=1) if dates else date.today()
    level_end, slope, widx, resid_std = _fit(values, dates) if n else (0.0, 0.0, np.ones(7), 0.0)
    if use_model:
        projection = _project(level_end, slope, widx, first_future, horizon)
        model_name = "linear_trend_x_weekly_seasonality"
    else:
        projection = [baseline_avg * widx[(first_future + timedelta(days=h)).weekday()] for h in range(horizon)]
        model_name = "baseline_avg_180d_x_weekly_seasonality"

    trend_7 = _pct_change(values[-7:].sum(), values[-14:-7].sum()) if n >= 14 else None
    trend_28 = _pct_change(values[-28:].sum(), values[-56:-28].sum()) if n >= 56 else None
    peak = WEEKDAYS_ID[int(np.argmax(widx))] if n and widx.max() > 1.05 else None

    return ForecastResult(
        model_name=model_name,
        forecast_daily_demand=float(np.mean(projection[:7])) if projection else 0.0,
        projection=[round(p, 2) for p in projection],
        projection_start=first_future.isoformat(),
        daily_trend_slope=slope,
        weekly_index=[round(float(w), 3) for w in widx],
        residual_std=resid_std,
        model_mape=model_mape,
        baseline_mape=baseline_mape,
        trend_7d_pct=trend_7,
        trend_28d_pct=trend_28,
        peak_weekday=peak,
        quality=quality,
    )


def simulate_days_to_stockout(stock: float, projection: list[float]) -> float:
    """Hari (pecahan) sampai stok habis bila permintaan mengikuti proyeksi. inf jika tidak habis."""
    remaining = stock
    if remaining <= 0:
        return 0.0
    daily = list(projection)
    tail = daily[-7:] if len(daily) >= 7 else daily or [0.0]
    for day in range(MAX_SIM_DAYS):
        demand = daily[day] if day < len(daily) else tail[day % len(tail)]
        if demand <= 0:
            continue
        if remaining <= demand:
            return day + remaining / demand
        remaining -= demand
    return math.inf


def stockout_range(stock: float, projection: list[float], residual_std: float) -> tuple[float, float]:
    """Rentang days-to-stockout (~80%): simulasi ulang dengan proyeksi +/- band ketidakpastian.

    Band = 1.28 x std residual / sqrt(7) (ketidakpastian rata-rata permintaan mingguan).
    Selalu mengapit hasil simulate_days_to_stockout(stock, projection).
    """
    band = 1.28 * residual_std / math.sqrt(7)
    earliest = simulate_days_to_stockout(stock, [p + band for p in projection])
    latest = simulate_days_to_stockout(stock, [max(p - band, 0.0) for p in projection])
    return earliest, latest
