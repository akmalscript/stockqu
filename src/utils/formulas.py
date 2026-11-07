"""Rumus inti StockQu - SATU SUMBER SAJA.

Semua modul (Analyst, Purchaser, Streamlit) wajib import dari sini.
Rumus sesuai AGENTS.md / stockqu-domain:

- rata_rata_penjualan_harian = total_penjualan_6_bulan / 180
- days-to-stockout          = stok_saat_ini / rata_rata_penjualan_harian
- safety_stock              = rata_rata_penjualan_harian x safety_hari (default 3)
- kebutuhan_restock         = (rata_rata_penjualan_harian x (lead_time_hari + safety_hari)) - stok_saat_ini
- days-to-stockout <= 7 -> masuk prioritas (RISIKO); <= 4 -> URGENT
"""
from __future__ import annotations

import math

from src.config import HISTORY_DAYS, RISK_MAX_DAYS, SAFETY_DAYS_DEFAULT, URGENT_MAX_DAYS

RISK_URGENT = "URGENT"
RISK_WARNING = "RISIKO"
RISK_SAFE = "AMAN"
RISK_NO_DATA = "DATA TIDAK DITEMUKAN"


def avg_daily_sales(total_sales_6_months: float, history_days: int = HISTORY_DAYS) -> float:
    """rata_rata_penjualan_harian = total_penjualan_6_bulan / 180."""
    if history_days <= 0:
        raise ValueError("history_days harus > 0")
    if total_sales_6_months < 0:
        raise ValueError("total penjualan tidak boleh negatif")
    return total_sales_6_months / history_days


def days_to_stockout(stock: float, avg_daily: float) -> float:
    """days-to-stockout = stok / rata-rata penjualan harian. Tanpa penjualan -> tak hingga."""
    if stock < 0:
        raise ValueError("stok tidak boleh negatif")
    if avg_daily <= 0:
        return math.inf
    return stock / avg_daily


def safety_stock(avg_daily: float, safety_days: int = SAFETY_DAYS_DEFAULT) -> float:
    """safety_stock = rata-rata harian x safety_hari."""
    return avg_daily * safety_days


def kebutuhan_restock(
    avg_daily: float,
    lead_time_days: int,
    stock: float,
    safety_days: int = SAFETY_DAYS_DEFAULT,
) -> float:
    """kebutuhan_restock = (rata-rata x (lead_time + safety)) - stok. Nilai mentah, bisa negatif."""
    return avg_daily * (lead_time_days + safety_days) - stock


def restock_qty(
    avg_daily: float,
    lead_time_days: int,
    stock: float,
    safety_days: int = SAFETY_DAYS_DEFAULT,
) -> int:
    """Kuantitas yang diusulkan (unit bulat ke atas). Jika kebutuhan <= 0, jangan usulkan pembelian."""
    need = kebutuhan_restock(avg_daily, lead_time_days, stock, safety_days)
    if need <= 0:
        return 0
    return math.ceil(need - 1e-9)


def classify_risk(days: float | None, lead_time_days: int | None = None) -> str:
    """Kategori risiko stockout.

    - URGENT : days <= 4, atau stok habis sebelum kiriman replenishment tiba (days < lead time, SRS-F-12)
    - RISIKO : 4 < days <= 7
    - AMAN   : days > 7
    """
    if days is None or (isinstance(days, float) and math.isnan(days)):
        return RISK_NO_DATA
    if days <= URGENT_MAX_DAYS:
        return RISK_URGENT
    if lead_time_days is not None and days < lead_time_days:
        return RISK_URGENT
    if days <= RISK_MAX_DAYS:
        return RISK_WARNING
    return RISK_SAFE
