import math
from datetime import date, timedelta

import numpy as np

from src.tools.forecasting import (
    build_daily_series,
    forecast_demand,
    simulate_days_to_stockout,
    stockout_range,
)

AS_OF = date(2026, 11, 6)


def _records(fn, days=180):
    start = AS_OF - timedelta(days=days - 1)
    return [(start + timedelta(days=i), fn(i, start + timedelta(days=i))) for i in range(days)]


def test_series_marks_missing_days():
    recs = [r for r in _records(lambda i, d: 10.0) if r[0].day != 1]
    values, dates, q = build_daily_series(recs, AS_OF, 180)
    assert len(values) == 180 and dates[-1] == AS_OF
    assert q.missing_days > 0 and q.observed_days == 180 - q.missing_days


def test_upward_trend_detected_and_beats_baseline():
    recs = _records(lambda i, d: 20 + 0.2 * i)
    values, dates, q = build_daily_series(recs, AS_OF, 180)
    fc = forecast_demand(values, dates, q)
    assert fc.model_name.startswith("linear_trend")
    assert fc.model_mape < fc.baseline_mape
    assert fc.trend_28d_pct > 0
    # proyeksi ke depan > rata-rata 6 bulan karena tren naik
    assert fc.forecast_daily_demand > values.mean()


def test_flat_series_falls_back_or_matches_baseline():
    recs = _records(lambda i, d: 15.0)
    values, dates, q = build_daily_series(recs, AS_OF, 180)
    fc = forecast_demand(values, dates, q)
    assert abs(fc.forecast_daily_demand - 15.0) < 0.5


def test_weekly_seasonality_peak_weekend():
    recs = _records(lambda i, d: 30.0 if d.weekday() >= 5 else 10.0)
    values, dates, q = build_daily_series(recs, AS_OF, 180)
    fc = forecast_demand(values, dates, q)
    assert fc.peak_weekday in ("Sabtu", "Minggu")


def test_anomaly_flagged():
    recs = _records(lambda i, d: 500.0 if i == 100 else 10.0)
    values, dates, q = build_daily_series(recs, AS_OF, 180)
    fc = forecast_demand(values, dates, q)
    assert fc.quality.anomaly_days


def test_simulate_days_to_stockout():
    assert simulate_days_to_stockout(100, [25.0] * 30) == 4.0
    assert math.isinf(simulate_days_to_stockout(100, [0.0] * 30))
    assert simulate_days_to_stockout(0, [10.0]) == 0.0


def test_stockout_range_brackets_point():
    projection = [15.0, 25.0, 20.0, 30.0, 10.0, 20.0, 20.0] * 4
    point = simulate_days_to_stockout(100, projection)
    lo, hi = stockout_range(100, projection, 5.0)
    assert lo < point < hi
    assert np.isfinite(lo) and np.isfinite(hi)
