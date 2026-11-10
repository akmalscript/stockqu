"""Supplier comparison, selection, draft PO, draft email, dan validasi (SRS-F-27..31).

Semua keputusan deterministik dan bisa ditelusur:
- Matriks per SKU-cabang: harga list, diskon, harga efektif, MOQ/pack, qty pesanan,
  total biaya, lead time, tiba sebelum stok habis?, performa, status verifikasi.
- Aturan pemilihan (mode "balanced"):
    1. Hanya penawaran status valid (kontrak berlaku, data lengkap) dan tidak mismatch dokumen.
    2. Jika ada supplier yang tiba sebelum stok habis (lead time <= days-to-stockout),
       pilih hanya dari kelompok itu (risiko stockout lebih penting dari selisih harga).
    3. Skor = 40% biaya + 25% performa + 25% kecocokan lead time + 10% verifikasi dokumen.
- Mode "cheapest" dipakai saat replanning budget: total biaya terendah.
"""
from __future__ import annotations

import hashlib
from collections import defaultdict
from datetime import date, timedelta

from src.config import LABEL_NOT_FOUND, MIN_SUPPLIERS
from src.tools.supplier_search import OFFER_VALID, SupplierOffer, order_qty_for

NEGOTIATION_DEADLINE_DAYS = 2  # kebijakan draft: batas balasan penawaran H+2


def compare_offers(item: dict, offers: list[SupplierOffer], verifications: dict[str, dict]) -> list[dict]:
    """Baris matriks perbandingan untuk satu SKU-cabang."""
    dts = item.get("days_to_stockout")
    rows = []
    for o in offers:
        qty = order_qty_for(o, int(item["allocated_qty"])) if o.effective_price else 0
        total = round(qty * o.effective_price, 2) if o.effective_price else None
        arrives = None if o.lead_time_days is None or dts is None else o.lead_time_days <= float(dts)
        ver = verifications.get(o.contract_id, {"status": "unverified", "note": LABEL_NOT_FOUND})
        rows.append({
            **o.to_dict(),
            "order_qty": qty,
            "total_cost": total,
            "arrives_before_stockout": arrives,
            "stockout_gap_days": None if arrives is None or arrives else round(o.lead_time_days - float(dts), 2),
            "verification": ver,
            "eligible": o.status == OFFER_VALID and ver.get("status") != "mismatch",
        })
    return rows


def select_supplier(item: dict, rows: list[dict], mode: str = "balanced") -> dict:
    eligible = [r for r in rows if r["eligible"] and r["total_cost"]]
    valid_count = sum(r["status"] == OFFER_VALID for r in rows)
    if not eligible:
        return {"selection_status": "no_offer", "selected": None, "valid_supplier_count": valid_count,
                "reason": f"Tidak ada penawaran valid untuk {item['sku']}: {LABEL_NOT_FOUND}", "scores": {}}

    pool = eligible
    lead_rule = False
    if mode == "balanced":
        on_time = [r for r in eligible if r["arrives_before_stockout"]]
        if on_time and len(on_time) < len(eligible):
            pool, lead_rule = on_time, True

    min_total = min(r["total_cost"] for r in pool)
    scores: dict[str, float] = {}
    for r in pool:
        cost = min_total / r["total_cost"]
        perf = (r["performance_score"] or 0) / 100.0
        if r["arrives_before_stockout"] is None:
            lead_fit = 0.5
        elif r["arrives_before_stockout"]:
            lead_fit = 1.0
        else:
            lead_fit = max(0.0, 1.0 - (r["stockout_gap_days"] or 0) / max(r["lead_time_days"] or 1, 1))
        ver = 1.0 if r["verification"].get("status") == "verified" else 0.5
        scores[r["supplier_id"]] = round(0.40 * cost + 0.25 * perf + 0.25 * lead_fit + 0.10 * ver, 6)

    if mode == "cheapest":
        chosen = min(pool, key=lambda r: (r["total_cost"], -(r["performance_score"] or 0), r["supplier_name"]))
    else:
        chosen = max(pool, key=lambda r: (scores[r["supplier_id"]], -r["total_cost"], r["supplier_name"]))

    status = "selected"
    review: list[str] = []
    if valid_count < MIN_SUPPLIERS:
        status = "needs_review"
        review.append(f"hanya {valid_count} supplier valid (minimal {MIN_SUPPLIERS})")
    if chosen["verification"].get("status") != "verified":
        status = "needs_review"
        review.append(f"harga belum terverifikasi dokumen kontrak ({chosen['verification'].get('note')})")
    if chosen["arrives_before_stockout"] is False:
        review.append(
            f"semua supplier tiba setelah stok habis; gap {chosen['stockout_gap_days']} hari - pertimbangkan transfer antar cabang"
        )

    cheapest = min(pool, key=lambda r: r["total_cost"])
    parts = [(
        f"{chosen['supplier_name']} dipilih untuk {item['sku']} di {item['branch']}: "
        f"{chosen['order_qty']} unit x Rp {chosen['effective_price']:,.0f} = Rp {chosen['total_cost']:,.0f} "
        f"(diskon kontrak {chosen['discount_pct']:g}%), lead time {chosen['lead_time_days']} hari, "
        f"performa {chosen['performance_score']:g}."
    )]
    if lead_rule:
        excluded = [r["supplier_name"] for r in eligible if r not in pool]
        parts.append(
            f"{', '.join(excluded)} tidak dipilih karena lead time melewati days-to-stockout "
            f"{float(item['days_to_stockout']):.2f} hari."
        )
    if cheapest is not chosen and mode != "cheapest":
        parts.append(
            f"Lebih mahal Rp {chosen['total_cost'] - cheapest['total_cost']:,.0f} dari {cheapest['supplier_name']}, "
            f"tetapi skor gabungan lebih tinggi ({scores[chosen['supplier_id']]:.3f} vs {scores[cheapest['supplier_id']]:.3f})."
        )
    if mode == "cheapest":
        parts.append("Mode replanning budget: dipilih total biaya terendah.")
    if review:
        parts.append("Perlu review: " + "; ".join(review) + ".")
    return {
        "selection_status": status,
        "selected": chosen,
        "valid_supplier_count": valid_count,
        "reason": " ".join(parts),
        "scores": scores,
        "review_notes": review,
    }


def build_po_drafts(selections: list[dict], branches: dict[str, str], as_of: date, run_id: str, plan_id: str) -> list[dict]:
    """Satu draft PO per (supplier, gudang tujuan). Nomor dokumen deterministik per run."""
    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for s in selections:
        if s["selected"] is not None:
            groups[(s["selected"]["supplier_id"], s["item"]["branch_id"])].append(s)
    run_parts = [part for part in run_id.split("-") if part]
    # Orkestrator memberi run ID seperti WF-YYYYMMDD-ABC123-NEG. Segmen
    # terakhir hanya nama agent dan sama pada setiap run, jadi ambil nonce
    # sebelumnya. Run standalone tetap memakai segmen terakhir seperti semula.
    agent_labels = {"ANL", "PUR", "NEG"}
    suffix_part = run_parts[-2] if len(run_parts) >= 2 and run_parts[-1].upper() in agent_labels else run_parts[-1]
    suffix = suffix_part[-6:].upper()
    pos = []
    for seq, ((supplier_id, branch_id), rows) in enumerate(
        sorted(groups.items(), key=lambda kv: (branches.get(kv[0][1], ""), kv[1][0]["selected"]["supplier_name"])),
        start=1,
    ):
        lead = max(int(r["selected"]["lead_time_days"] or 0) for r in rows)
        items = []
        for r in sorted(rows, key=lambda r: r["item"]["sku"]):
            sel = r["selected"]
            items.append({
                "product_id": r["item"]["product_id"],
                "sku": r["item"]["sku"],
                "product_name": r["item"]["product_name"],
                "qty": int(sel["order_qty"]),
                "list_price": sel["list_price"],
                "discount_pct": sel["discount_pct"],
                "unit_price": sel["effective_price"],
                "moq": sel["moq"],
                "pack_size": sel["pack_size"],
                "line_total": round(int(sel["order_qty"]) * sel["effective_price"], 2),
                "contract_id": sel["contract_id"],
            })
        total = round(sum(i["line_total"] for i in items), 2)
        key = hashlib.sha256(f"{plan_id}|{supplier_id}|{branch_id}".encode()).hexdigest()[:40]
        pos.append({
            "po_number": f"PO-{as_of:%Y%m%d}-{suffix}-{seq:02d}",
            "supplier_id": supplier_id,
            "supplier_name": rows[0]["selected"]["supplier_name"],
            "branch_id": branch_id,
            "branch": branches.get(branch_id, LABEL_NOT_FOUND),
            "items": items,
            "total_amount": total,
            "lead_time_days": lead,
            "expected_arrival": (as_of + timedelta(days=lead)).isoformat(),
            "idempotency_key": key,
            "status": "draft",
            "approval_status": "belum diajukan (approval WhatsApp di P4)",
        })
    return pos


def _rp(x: float) -> str:
    return f"Rp {x:,.0f}".replace(",", ".")


def draft_po_email(po: dict) -> dict:
    """Template 11.2 stockqu-domain. DRAFT - tidak dikirim sebelum approval manager."""
    lines = [f"Kepada Yth. {po['supplier_name']},", "",
             f"Bersama ini kami sampaikan Purchase Order {po['po_number']} untuk gudang {po['branch']}:", ""]
    for i in po["items"]:
        lines.append(f"- {i['sku']} ({i['product_name']}): {i['qty']} unit x {_rp(i['unit_price'])} = {_rp(i['line_total'])}")
    lines += ["", f"Total: {_rp(po['total_amount'])}",
              f"Tanggal kirim yang diminta: {po['expected_arrival']} (lead time {po['lead_time_days']} hari)",
              "", "Mohon konfirmasi penerimaan PO ini.", "", "Hormat kami,", "Tim Purchasing StockQu"]
    return {"subject": f"Purchase Order {po['po_number']} - StockQu", "body": "\n".join(lines),
            "status": "DRAFT - BELUM DIKIRIM (menunggu approval manager)"}


def draft_negotiation_email(po: dict, selections: list[dict], citations: dict[str, list[dict]], as_of: date) -> dict:
    """Template 11.3: kutipan kontrak (RAG) + harga kompetitor dari matriks + permintaan + batas waktu."""
    deadline = as_of + timedelta(days=NEGOTIATION_DEADLINE_DAYS)
    lines = [f"Kepada Yth. {po['supplier_name']},", "",
             f"Terkait rencana PO {po['po_number']} untuk gudang {po['branch']}, kami ingin meninjau penawaran berikut:", ""]
    for s in selections:
        sel = s["selected"]
        if sel is None or sel["supplier_id"] != po["supplier_id"] or s["item"]["branch_id"] != po["branch_id"]:
            continue
        cites = citations.get(sel["contract_id"]) or []
        if cites:
            c = cites[0]
            lines.append(f"- {s['item']['sku']}: sesuai {c['document_title']} {c['clause_ref']} "
                         f"(berlaku s/d {c['valid_until'] or LABEL_NOT_FOUND}): \"{c['quote']}\"")
        else:
            lines.append(f"- {s['item']['sku']}: kutipan kontrak {LABEL_NOT_FOUND}")
        others = [r for r in s["rows"] if r["supplier_id"] != sel["supplier_id"] and r["eligible"] and r["effective_price"]]
        if others:
            best = min(others, key=lambda r: r["effective_price"])
            if best["effective_price"] < sel["effective_price"]:
                lines.append(f"  Harga kompetitor terendah {_rp(best['effective_price'])}/unit (kontrak lain di sistem kami). "
                             f"Mohon pertimbangkan penyesuaian harga atau diskon tambahan.")
            else:
                lines.append(f"  Harga Anda sudah terendah di antara {len(others) + 1} supplier yang kami bandingkan.")
        if sel["arrives_before_stockout"] is False:
            lines.append(f"  Stok kami diperkirakan habis dalam {float(s['item']['days_to_stockout']):.1f} hari; "
                         f"mohon kemungkinan pengiriman lebih cepat dari {sel['lead_time_days']} hari.")
    lines += ["", f"Mohon balasan paling lambat {deadline.isoformat()}.",
              "Email ini bukan komitmen pembelian; PO resmi dikirim setelah persetujuan manager.",
              "", "Hormat kami,", "Tim Purchasing StockQu"]
    return {"subject": f"Permintaan peninjauan penawaran - {po['po_number']}", "body": "\n".join(lines),
            "deadline": deadline.isoformat(), "status": "DRAFT - BELUM DIKIRIM"}


def validate_po_drafts(pos: list[dict], plan_items: list[dict], budget_limit: float) -> dict:
    """SRS-F-31/36: validasi deterministik sebelum persist (DB memvalidasi ulang di RPC)."""
    errors: list[str] = []
    warnings: list[str] = []
    allocated = {(str(p["product_id"]), str(p["branch_id"])): int(p.get("allocated_qty") or 0) for p in plan_items}
    seen_groups: set[tuple[str, str]] = set()
    ordered: dict[tuple[str, str], int] = defaultdict(int)
    rounding: dict[tuple[str, str], int] = defaultdict(int)
    for po in pos:
        group = (po["supplier_id"], po["branch_id"])
        if group in seen_groups:
            errors.append(f"PO ganda untuk supplier/gudang yang sama ({po['po_number']})")
        seen_groups.add(group)
        line_sum = 0.0
        for i in po["items"]:
            key = (str(i["product_id"]), str(po["branch_id"]))
            ordered[key] += int(i["qty"])
            rounding[key] = max(rounding[key], int(i["moq"]), int(i["pack_size"]))
            if i["qty"] <= 0 or not i["unit_price"] or i["unit_price"] <= 0:
                errors.append(f"{po['po_number']} {i['sku']}: qty/harga tidak valid")
            if i["qty"] < i["moq"]:
                errors.append(f"{po['po_number']} {i['sku']}: qty {i['qty']} < MOQ {i['moq']}")
            if i["qty"] % i["pack_size"]:
                errors.append(f"{po['po_number']} {i['sku']}: qty {i['qty']} bukan kelipatan pack {i['pack_size']}")
            if round(i["qty"] * i["unit_price"], 2) != round(i["line_total"], 2):
                errors.append(f"{po['po_number']} {i['sku']}: line total tidak cocok")
            line_sum += i["line_total"]
        if round(line_sum, 2) != round(po["total_amount"], 2):
            errors.append(f"{po['po_number']}: total header tidak sama dengan jumlah line")
    for key, qty in ordered.items():
        alloc = allocated.get(key, 0)
        if alloc <= 0:
            errors.append(f"SKU-cabang {key} tidak ada di alokasi plan Purchaser")
        elif qty > alloc + rounding[key]:
            errors.append(f"SKU-cabang {key}: qty PO {qty} melebihi alokasi plan {alloc}")
        elif qty > alloc:
            warnings.append(f"SKU-cabang {key}: qty dibulatkan {alloc} -> {qty} karena MOQ/pack supplier")
    total = round(sum(po["total_amount"] for po in pos), 2)
    if total > round(budget_limit, 2):
        errors.append(f"Total draft PO {_rp(total)} melebihi budget tersedia untuk plan {_rp(budget_limit)}")
    return {"valid": not errors, "errors": errors, "warnings": warnings,
            "po_total": total, "budget_limit": round(budget_limit, 2)}
