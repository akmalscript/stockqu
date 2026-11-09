"""Buat + upload kontrak DUMMY (PDF) per supplier untuk RAG Negotiator (roadmap P3-14).

    python -m scripts.seed_supplier_documents            # upload ke Storage + simpan chunk
    python -m scripts.seed_supplier_documents --out-dir data/contracts_dummy   # simpan PDF lokal juga

Isi PDF HANYA dari data Supabase (contracts, suppliers, products) dan diberi label
CONTOH SIMULASI. Idempotent: PDF identik (sha256 sama) tidak diupload ulang.
"""
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

from src.services.supabase_client import (
    SupabaseNotConfigured,
    SupabaseRepository,
    get_client,
    has_write_key,
)
from src.services.supplier_documents import (
    build_dummy_contract_pdf,
    ingest_supplier_pdf,
)


def main() -> int:
    ap = argparse.ArgumentParser(description="Seed kontrak dummy supplier")
    ap.add_argument("--out-dir", help="simpan salinan PDF ke folder ini (di-ignore git: data/*.pdf)")
    args = ap.parse_args()
    if not has_write_key():
        print("ERROR: SUPABASE_SERVICE_ROLE_KEY wajib.", file=sys.stderr)
        return 2
    try:
        repo = SupabaseRepository(read_client=get_client(write=True))
        suppliers = repo.fetch_suppliers()
        contracts = repo.fetch_reference_contracts()
        products = {str(p["id"]): p for p in repo.fetch_products()}
    except SupabaseNotConfigured as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    out_dir = Path(args.out_dir) if args.out_dir else None
    if out_dir:
        out_dir.mkdir(parents=True, exist_ok=True)
    for s in suppliers:
        rows = [c for c in contracts if str(c["supplier_id"]) == str(s["id"])]
        if not rows:
            print(f"- {s['name']}: kontrak DATA TIDAK DITEMUKAN, dilewati")
            continue
        pdf = build_dummy_contract_pdf(s, rows, products)
        if out_dir:
            (out_dir / f"kontrak-dummy-{s['name'].lower().replace(' ', '-')}.pdf").write_bytes(pdf)
        valid_until = max((str(r["valid_until"]) for r in rows if r.get("valid_until")), default=None)
        res = ingest_supplier_pdf(
            repo, supplier_id=str(s["id"]), doc_type="contract", title=f"Kontrak Pengadaan {s['name']} (DUMMY)",
            pdf_bytes=pdf, uploaded_by="system:seed-supplier-documents",
            valid_until=date.fromisoformat(valid_until) if valid_until else None, source="DUMMY-SEED",
        )
        print(f"- {s['name']}: {res['status']}, {res['chunks']} chunk, versi {res['document'].get('version')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
