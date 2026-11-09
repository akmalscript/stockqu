"""Supplier search tool (SRS-F-25/26/57, roadmap P3-13).

Sumber penawaran yang dipakai SEKARANG: tabel Supabase ``contracts`` (harga kontrak resmi)
digabung ``suppliers`` (lead time, skor performa). Belum ada API/web scraping supplier;
sumber itu tidak dikarang. Setiap penawaran membawa status verifikasi:

- ``valid``      : kontrak masih berlaku, harga > 0, data wajib lengkap
- ``expired``    : valid_until sudah lewat -> tidak boleh dipilih
- ``incomplete`` : harga / lead time / supplier tidak ada -> DATA TIDAK DITEMUKAN
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from datetime import date

from src.config import LABEL_NOT_FOUND

OFFER_VALID = "valid"
OFFER_EXPIRED = "expired"
OFFER_INCOMPLETE = "incomplete"


def discount_fraction(value) -> float:
    """Diskon kontrak disimpan sebagai persen (2 = 2%) atau pecahan (0.02)."""
    try:
        number = float(value or 0)
    except (TypeError, ValueError):
        return 0.0
    if number < 0:
        return 0.0
    return min(number / 100.0 if number > 1 else number, 1.0)


@dataclass
class SupplierOffer:
    contract_id: str
    supplier_id: str
    supplier_name: str
    product_id: str
    list_price: float | None
    discount_pct: float
    effective_price: float | None
    moq: int
    pack_size: int
    lead_time_days: int | None
    performance_score: float | None
    valid_until: str | None
    status: str
    source: str
    issues: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def build_offers(contracts: list[dict], suppliers: list[dict], as_of: date) -> dict[str, list[SupplierOffer]]:
    """Kelompokkan penawaran per product_id. Satu penawaran terbaru per (supplier, produk)."""
    sup = {str(s["id"]): s for s in suppliers}
    latest: dict[tuple[str, str], dict] = {}
    for row in sorted(contracts, key=lambda r: str(r.get("created_at") or "")):
        latest[(str(row["product_id"]), str(row["supplier_id"]))] = row

    offers: dict[str, list[SupplierOffer]] = {}
    for (product_id, supplier_id), row in latest.items():
        supplier = sup.get(supplier_id)
        issues: list[str] = []
        try:
            price = float(row.get("price")) if row.get("price") is not None else None
        except (TypeError, ValueError):
            price = None
        if price is None or price <= 0:
            issues.append(f"harga {LABEL_NOT_FOUND}")
            price = None
        lead = supplier.get("lead_time_days") if supplier else None
        if supplier is None:
            issues.append(f"supplier {LABEL_NOT_FOUND}")
        if lead is None:
            issues.append(f"lead time {LABEL_NOT_FOUND}")
        valid_until = row.get("valid_until")
        expired = bool(valid_until) and date.fromisoformat(str(valid_until)) < as_of
        disc = discount_fraction(row.get("discount"))
        status = OFFER_INCOMPLETE if issues else (OFFER_EXPIRED if expired else OFFER_VALID)
        if expired:
            issues.append(f"kontrak kedaluwarsa {valid_until}")
        offers.setdefault(product_id, []).append(
            SupplierOffer(
                contract_id=str(row["id"]),
                supplier_id=supplier_id,
                supplier_name=(supplier or {}).get("name", LABEL_NOT_FOUND),
                product_id=product_id,
                list_price=price,
                discount_pct=round(disc * 100, 4),
                effective_price=None if price is None else round(price * (1 - disc), 2),
                moq=max(int(row.get("moq") or 1), 1),
                pack_size=max(int(row.get("pack_size") or 1), 1),
                lead_time_days=None if lead is None else int(lead),
                performance_score=None if not supplier or supplier.get("performance_score") is None
                else float(supplier["performance_score"]),
                valid_until=None if valid_until is None else str(valid_until),
                status=status,
                source=f"Supabase contracts.id={row['id']} + suppliers.id={supplier_id}",
                issues=issues,
            )
        )
    for rows in offers.values():
        rows.sort(key=lambda o: (o.status != OFFER_VALID, o.effective_price or math.inf, o.supplier_name))
    return offers


def order_qty_for(offer: SupplierOffer, needed_qty: int) -> int:
    """Kuantitas pesanan minimal >= kebutuhan, >= MOQ, kelipatan pack supplier."""
    if needed_qty <= 0:
        return 0
    qty = max(needed_qty, offer.moq)
    return int(math.ceil(qty / offer.pack_size) * offer.pack_size)
