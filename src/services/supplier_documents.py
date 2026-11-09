"""Manajemen dokumen supplier (SRS-F-53, roadmap P3-14).

- ``build_dummy_contract_pdf``: buat PDF kontrak DUMMY yang isinya HANYA dari data Supabase
  (contracts, suppliers, products). Tidak ada klausul/angka karangan.
- ``ingest_supplier_pdf``: validasi PDF -> ekstraksi teks -> chunk per Pasal -> upload ke
  Supabase Storage (bucket privat) -> simpan metadata + chunk. Idempotent via sha256.
"""
from __future__ import annotations

import hashlib
from datetime import date

from src.config import LABEL_SIMULASI
from src.tools.contract_rag import chunk_pages, extract_pdf_pages

BUCKET = "supplier-documents"
MAX_PDF_BYTES = 10 * 1024 * 1024
DOC_TYPES = ("contract", "catalog", "quotation_history")


class DocumentRejected(ValueError):
    pass


def _rp(value: float) -> str:
    return f"Rp {float(value):,.0f}".replace(",", ".")


def contract_text_sections(supplier: dict, rows: list[dict], products: dict[str, dict]) -> list[tuple[str, list[str]]]:
    """Isi kontrak dummy sebagai (judul pasal, baris). Semua angka dari tabel Supabase."""
    valid_until = max((str(r["valid_until"]) for r in rows if r.get("valid_until")), default="-")
    price_lines = []
    for r in sorted(rows, key=lambda r: products.get(str(r["product_id"]), {}).get("sku", "")):
        p = products.get(str(r["product_id"]), {})
        disc = float(r.get("discount") or 0)
        disc = disc * 100 if 0 < disc <= 1 else disc
        price_lines.append(
            f"{p.get('sku', '-')} | {p.get('name', '-')} | {_rp(r['price'])} | diskon {disc:g}% | "
            f"MOQ {int(r.get('moq') or 1)} | pack {int(r.get('pack_size') or 1)}"
        )
    return [
        ("Pasal 1 Para Pihak", [
            "Pihak Pertama: StockQu, distributor FMCG dengan 3 cabang gudang di Jawa Tengah (Branch 1, Branch 2, Branch 3).",
            f"Pihak Kedua: {supplier['name']}, kontak {supplier.get('contact') or '-'}.",
        ]),
        ("Pasal 2 Harga per SKU", [
            "Format: SKU | nama | harga list per unit | diskon kontrak | MOQ | kelipatan pack.",
            *price_lines,
        ]),
        ("Pasal 3 Diskon", [
            "Diskon pada Pasal 2 dihitung dari harga list per unit dan berlaku selama masa kontrak.",
        ]),
        ("Pasal 4 MOQ dan Kemasan", [
            "Pemesanan per SKU minimal sebesar MOQ dan dalam kelipatan pack sesuai Pasal 2.",
        ]),
        ("Pasal 5 Lead Time Pengiriman", [
            f"Barang dikirim paling lambat {int(supplier.get('lead_time_days') or 0)} hari kalender sejak PO diterima.",
        ]),
        ("Pasal 6 Masa Berlaku", [
            f"Harga dan diskon dalam kontrak ini berlaku sampai {valid_until}.",
        ]),
        ("Pasal 7 Kinerja Supplier", [
            f"Skor performa pengiriman tercatat {supplier.get('performance_score', '-')} dari 100 pada sistem StockQu.",
        ]),
    ]


def build_dummy_contract_pdf(supplier: dict, rows: list[dict], products: dict[str, dict]) -> bytes:
    from fpdf import FPDF

    pdf = FPDF(format="A4")
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 9)
    pdf.set_text_color(14, 124, 97)
    pdf.cell(0, 6, f"{LABEL_SIMULASI} - DOKUMEN DUMMY UNTUK DEMO", new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(30, 42, 68)
    pdf.set_font("Helvetica", "B", 14)
    pdf.cell(0, 10, f"Kontrak Pengadaan {supplier['name']}", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", size=9)
    pdf.cell(0, 6, "Sumber angka: Supabase contracts, suppliers, products", new_x="LMARGIN", new_y="NEXT")
    for title, lines in contract_text_sections(supplier, rows, products):
        pdf.ln(2)
        pdf.set_font("Helvetica", "B", 11)
        pdf.cell(0, 7, title, new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", size=10)
        for line in lines:
            pdf.multi_cell(0, 5.5, line, new_x="LMARGIN", new_y="NEXT")
    return bytes(pdf.output())


def validate_pdf(pdf_bytes: bytes) -> None:
    if not pdf_bytes or len(pdf_bytes) > MAX_PDF_BYTES:
        raise DocumentRejected("PDF kosong atau lebih dari 10 MB")
    if not pdf_bytes.startswith(b"%PDF"):
        raise DocumentRejected("File bukan PDF")


def ingest_supplier_pdf(
    repo,
    *,
    supplier_id: str,
    doc_type: str,
    title: str,
    pdf_bytes: bytes,
    uploaded_by: str,
    valid_from: date | None = None,
    valid_until: date | None = None,
    source: str = "upload",
) -> dict:
    if doc_type not in DOC_TYPES:
        raise DocumentRejected(f"doc_type harus salah satu {DOC_TYPES}")
    validate_pdf(pdf_bytes)
    sha = hashlib.sha256(pdf_bytes).hexdigest()
    existing = repo.find_supplier_document_by_sha(sha)
    if existing:
        return {"document": existing, "chunks": 0, "status": "duplicate"}

    pages = extract_pdf_pages(pdf_bytes)
    chunks = chunk_pages(pages)
    if not chunks:
        raise DocumentRejected("PDF tidak berisi teks yang bisa diekstrak (OCR belum didukung)")

    version = repo.next_document_version(supplier_id, doc_type)
    path = f"{supplier_id}/{doc_type}/v{version}-{sha[:12]}.pdf"
    repo.upload_document(BUCKET, path, pdf_bytes)
    doc = repo.insert_supplier_document({
        "supplier_id": supplier_id,
        "doc_type": doc_type,
        "title": title.strip()[:200] or f"{doc_type} v{version}",
        "storage_bucket": BUCKET,
        "storage_path": path,
        "version": version,
        "valid_from": valid_from.isoformat() if valid_from else None,
        "valid_until": valid_until.isoformat() if valid_until else None,
        "sha256": sha,
        "page_count": len(pages),
        "source": source,
        "uploaded_by": uploaded_by,
    })
    try:
        repo.insert_document_chunks([
            {"document_id": doc["id"], "chunk_index": c.chunk_index, "page": c.page,
             "clause_ref": c.clause_ref, "content": c.content}
            for c in chunks
        ])
    except Exception:
        repo.delete_supplier_document(doc["id"])  # jangan tinggalkan dokumen tanpa chunk
        raise
    return {"document": doc, "chunks": len(chunks), "status": "ingested"}
