"""Test P3 Negotiator. CONTOH SIMULASI - BUKAN DATA REAL (data meniru seed dummy Supabase)."""
from datetime import date
from pathlib import Path

import pytest

from src.agents.negotiator import NegotiatorAgent
from src.services.exports import comparison_xlsx_bytes, po_pdf_bytes
from src.services.supplier_documents import (
    DocumentRejected,
    build_dummy_contract_pdf,
    ingest_supplier_pdf,
)
from src.tools.contract_rag import (
    chunk_pages,
    extract_pdf_pages,
    parse_price_terms,
    retrieve,
    verify_offer_against_clauses,
)
from src.tools.supplier_search import build_offers, discount_fraction, order_qty_for
from src.tools.supplier_selection import (
    build_po_drafts,
    compare_offers,
    select_supplier,
    validate_po_drafts,
)

AS_OF = date(2026, 11, 9)
ROOT = Path(__file__).resolve().parents[1]
SUPPLIERS = [
    {"id": "sA", "name": "Supplier A", "contact": "+62 800-0001", "lead_time_days": 3, "performance_score": 92},
    {"id": "sB", "name": "Supplier B", "contact": "+62 800-0002", "lead_time_days": 4, "performance_score": 88},
    {"id": "sC", "name": "Supplier C", "contact": "+62 800-0003", "lead_time_days": 5, "performance_score": 85},
]
PRODUCTS = [
    {"id": "pA", "sku": "SKU-A", "name": "Item A", "margin": 18, "turnover_rate": 1.2, "lead_time_days": 3},
    {"id": "pB", "sku": "SKU-B", "name": "Item B", "margin": 22, "turnover_rate": 1.5, "lead_time_days": 4},
]
BRANCHES = [{"id": "b1", "name": "Branch 1", "city": "Jawa Tengah"}, {"id": "b2", "name": "Branch 2", "city": "Jawa Tengah"}]
PRICES = {("pA", "sA"): (12000, 0), ("pA", "sB"): (11500, 2), ("pA", "sC"): (12500, 0),
          ("pB", "sA"): (8000, 0), ("pB", "sB"): (7800, 2), ("pB", "sC"): (8200, 0)}


def contracts():
    return [{"id": f"k{p}{s}", "product_id": p, "supplier_id": s, "price": price, "discount": disc,
             "valid_until": "2026-12-31", "moq": 1, "pack_size": 1, "created_at": "2026-11-07T00:00:00+00:00"}
            for (p, s), (price, disc) in PRICES.items()]


class NegRepo:
    def __init__(self, budget_used=0, with_docs=True, plan=True, reserved=1_270_000):
        self.reserved = reserved
        self.with_docs = with_docs
        self.has_plan = plan
        self.budget_used = budget_used
        self.persisted = None
        self.logs = []
        self.docs, self.chunks = [], []
        if with_docs:
            products = {p["id"]: p for p in PRODUCTS}
            for s in SUPPLIERS:
                pdf = build_dummy_contract_pdf(s, [c for c in contracts() if c["supplier_id"] == s["id"]], products)
                ingest_supplier_pdf(self, supplier_id=s["id"], doc_type="contract", title=f"Kontrak {s['name']}",
                                    pdf_bytes=pdf, uploaded_by="test", valid_until=date(2026, 12, 31), source="DUMMY-SEED")

    # --- plan / analyst
    def latest_purchaser_run_id(self):
        return "PUR-1" if self.has_plan else None

    def fetch_procurement_plan(self, run_id):
        return {"id": "plan-1", "run_id": run_id, "analyst_run_id": "ANL-1", "period": "2026-11",
                "status": "completed", "proposed_total": 2_000_000}

    def fetch_procurement_plan_items(self, plan_id):
        return [
            {"product_id": "pA", "branch_id": "b1", "rank": 1, "allocated_qty": 80, "unit_price": 11270, "risk_level": "URGENT"},
            {"product_id": "pB", "branch_id": "b1", "rank": 2, "allocated_qty": 48, "unit_price": 7644, "risk_level": "RISIKO"},
            {"product_id": "pA", "branch_id": "b2", "rank": 3, "allocated_qty": 0, "unit_price": 11270, "risk_level": "AMAN"},
        ]

    def fetch_forecasts(self, run_id):
        return [{"product_id": "pA", "branch_id": "b1", "days_to_stockout": 3.35},
                {"product_id": "pB", "branch_id": "b1", "days_to_stockout": 5.02}]

    def fetch_products(self):
        return PRODUCTS

    def fetch_branches(self):
        return BRANCHES

    def fetch_reference_contracts(self):
        return contracts()

    def fetch_suppliers(self):
        return SUPPLIERS

    # --- budget
    def fetch_budget(self, period):
        return {"month": period, "total_budget": 50_000_000, "used_budget": self.budget_used}

    def fetch_purchase_orders_for_period(self, period):
        return []

    def fetch_active_budget_reservations(self, period):
        return [{"plan_id": "plan-1", "amount": self.reserved, "status": "active"}]

    def fetch_plan_reservation(self, plan_id):
        return {"plan_id": plan_id, "amount": self.reserved, "status": "active"}

    # --- documents
    def fetch_supplier_documents(self):
        return self.docs

    def fetch_document_chunks(self, ids):
        return [c for c in self.chunks if c["document_id"] in ids]

    def find_supplier_document_by_sha(self, sha):
        return next((d for d in self.docs if d["sha256"] == sha), None)

    def next_document_version(self, supplier_id, doc_type):
        return 1 + sum(d["supplier_id"] == supplier_id and d["doc_type"] == doc_type for d in self.docs)

    def upload_document(self, bucket, path, data):
        self.uploaded = (bucket, path, len(data))

    def insert_supplier_document(self, row):
        doc = {**row, "id": f"doc-{len(self.docs) + 1}"}
        self.docs.append(doc)
        return doc

    def insert_document_chunks(self, rows):
        self.chunks.extend(rows)
        return len(rows)

    def delete_supplier_document(self, document_id):
        self.docs = [d for d in self.docs if d["id"] != document_id]

    # --- writes
    def persist_negotiation(self, run, comparisons, pos):
        self.persisted = (run, comparisons, pos)
        return "neg-uuid"

    def insert_agent_logs(self, rows):
        self.logs.extend(rows)
        return len(rows)


# ---------------------------------------------------------------- tools
def test_discount_and_order_qty_rounding():
    assert discount_fraction(2) == 0.02 and discount_fraction(0.1) == 0.1 and discount_fraction(None) == 0
    offers = build_offers(contracts(), SUPPLIERS, AS_OF)["pA"]
    b = next(o for o in offers if o.supplier_id == "sB")
    assert b.effective_price == 11270 and b.status == "valid"
    b.moq, b.pack_size = 50, 24
    assert order_qty_for(b, 80) == 96
    assert order_qty_for(b, 10) == 72


def test_expired_and_incomplete_offers_not_selectable():
    rows = contracts()
    rows[0]["valid_until"] = "2026-10-01"
    rows[1]["price"] = None
    offers = build_offers(rows, SUPPLIERS, AS_OF)["pA"]
    status = {o.supplier_id: o.status for o in offers}
    assert status == {"sA": "expired", "sB": "incomplete", "sC": "valid"}


def test_dummy_contract_pdf_roundtrip_chunks_and_prices():
    products = {p["id"]: p for p in PRODUCTS}
    pdf = build_dummy_contract_pdf(SUPPLIERS[1], [c for c in contracts() if c["supplier_id"] == "sB"], products)
    pages = extract_pdf_pages(pdf)
    chunks = chunk_pages(pages)
    refs = [c.clause_ref for c in chunks]
    assert "Pasal 2" in refs and "Pasal 5" in refs
    terms = parse_price_terms("\n".join(c.content for c in chunks))
    assert terms["SKU-A"] == {"price": 11500.0, "discount_pct": 2.0, "moq": 1, "pack_size": 1}


def test_rag_cites_clause_and_detects_mismatch():
    repo = NegRepo()
    cites = retrieve("SKU-A Item A harga diskon", "sB", repo.docs, repo.chunks, AS_OF.isoformat(), top_k=1)
    assert cites and cites[0].clause_ref == "Pasal 2" and "SKU-A" in cites[0].quote
    assert cites[0].valid_until == "2026-12-31"
    lead = retrieve("lead time pengiriman hari", "sB", repo.docs, repo.chunks, AS_OF.isoformat(), top_k=1)
    assert lead[0].clause_ref == "Pasal 5" and "4 hari kalender" in lead[0].quote
    sup_chunks = [c for c in repo.chunks if c["document_id"] == cites[0].document_id]
    ok = verify_offer_against_clauses("SKU-A", {"list_price": 11500, "discount_pct": 2, "moq": 1, "pack_size": 1}, sup_chunks)
    bad = verify_offer_against_clauses("SKU-A", {"list_price": 9999, "discount_pct": 2, "moq": 1, "pack_size": 1}, sup_chunks)
    assert ok["status"] == "verified" and bad["status"] == "mismatch"


def test_rejects_non_pdf_and_duplicate_is_idempotent():
    repo = NegRepo()
    with pytest.raises(DocumentRejected):
        ingest_supplier_pdf(repo, supplier_id="sA", doc_type="contract", title="x", pdf_bytes=b"hello", uploaded_by="t")
    products = {p["id"]: p for p in PRODUCTS}
    pdf = build_dummy_contract_pdf(SUPPLIERS[0], [c for c in contracts() if c["supplier_id"] == "sA"], products)
    again = ingest_supplier_pdf(repo, supplier_id="sA", doc_type="contract", title="x", pdf_bytes=pdf, uploaded_by="t")
    assert again["status"] == "duplicate" and len(repo.docs) == 3


def test_lead_time_rule_beats_cheaper_late_supplier():
    item = {"sku": "SKU-A", "branch": "Branch 1", "allocated_qty": 80, "days_to_stockout": 3.35}
    rows = compare_offers(item, build_offers(contracts(), SUPPLIERS, AS_OF)["pA"], {})
    chosen = select_supplier(item, rows)
    assert chosen["selected"]["supplier_name"] == "Supplier A"  # lead 3 <= 3.35; B lebih murah tapi lead 4
    assert "lead time melewati" in chosen["reason"]
    cheapest = select_supplier(item, rows, mode="cheapest")
    assert cheapest["selected"]["supplier_name"] == "Supplier B"


def test_fewer_than_three_suppliers_needs_review():
    item = {"sku": "SKU-A", "branch": "Branch 1", "allocated_qty": 10, "days_to_stockout": 10}
    offers = [o for o in build_offers(contracts(), SUPPLIERS, AS_OF)["pA"] if o.supplier_id != "sC"]
    out = select_supplier(item, compare_offers(item, offers, {}))
    assert out["selection_status"] == "needs_review" and "minimal 3" in out["reason"]


def test_validate_po_catches_over_allocation_and_budget():
    pos = [{"po_number": "PO-1", "supplier_id": "sA", "branch_id": "b1", "total_amount": 1_200_000,
            "items": [{"product_id": "pA", "sku": "SKU-A", "qty": 100, "unit_price": 12000, "line_total": 1_200_000,
                       "moq": 1, "pack_size": 1}]}]
    res = validate_po_drafts(pos, [{"product_id": "pA", "branch_id": "b1", "allocated_qty": 80}], 1_000_000)
    assert not res["valid"]
    assert any("melebihi alokasi" in e for e in res["errors"]) and any("melebihi budget" in e for e in res["errors"])


# ---------------------------------------------------------------- graph
def test_negotiator_end_to_end_drafts_and_persists():
    repo = NegRepo()
    out = NegotiatorAgent(repo).run(run_id="NEG-20261109-ABC123", as_of=AS_OF)
    assert out["status"] == "completed", out.get("validation")
    comps = {c["item"]["sku"]: c for c in out["comparisons"]}
    assert comps["SKU-A"]["selected"]["supplier_name"] == "Supplier A"
    assert comps["SKU-B"]["selected"]["supplier_name"] == "Supplier B"  # lead 4 <= 5.02 dan termurah
    assert all(c["valid_supplier_count"] == 3 for c in out["comparisons"])
    assert all(r["verification"]["status"] == "verified" for c in out["comparisons"] for r in c["rows"])

    pos = out["pos"]
    assert len(pos) == 2 and {p["supplier_name"] for p in pos} == {"Supplier A", "Supplier B"}
    a = next(p for p in pos if p["supplier_name"] == "Supplier A")
    assert a["total_amount"] == 80 * 12000 and a["expected_arrival"] == "2026-11-12"
    assert a["po_number"].startswith("PO-20261109-ABC123-")
    assert "BELUM DIKIRIM" in a["emails"]["purchase_order"]["status"]
    nego = a["emails"]["negotiation"]["body"]
    assert "Pasal 2" in nego and "Rp 11.270" in nego and "bukan komitmen pembelian" in nego

    run, comparisons, payload = repo.persisted
    assert run["po_total"] == 80 * 12000 + 48 * 7644 and run["requested_by_role"] == "system_agent"
    assert comparisons[0]["citations"] and comparisons[0]["reason_trace"]["evidence"]
    assert all(set(i) >= {"moq", "pack_size", "line_total"} for p in payload for i in p["items"])
    tools = [log["tool_name"] for log in repo.logs]
    assert tools[0] == "baca_procurement_plan" and "rag_kontrak_supplier" in tools and tools[-1] == "selesai"


def test_orchestrator_runs_on_same_day_get_unique_po_numbers():
    selections = [{
        "item": {"product_id": "pA", "sku": "SKU-A", "product_name": "Item A", "branch_id": "b1"},
        "selected": {"supplier_id": "sA", "supplier_name": "Supplier A", "lead_time_days": 3,
                     "order_qty": 10, "list_price": 12000, "discount_pct": 0,
                     "effective_price": 12000, "moq": 1, "pack_size": 1, "contract_id": "k1"},
    }]

    first = build_po_drafts(selections, {"b1": "Branch 1"}, AS_OF, "WF-20261109-ABC123-NEG", "plan-1")
    second = build_po_drafts(selections, {"b1": "Branch 1"}, AS_OF, "WF-20261109-DEF456-NEG", "plan-2")

    assert first[0]["po_number"] == "PO-20261109-ABC123-01"
    assert second[0]["po_number"] == "PO-20261109-DEF456-01"
    assert first[0]["po_number"] != second[0]["po_number"]


def test_budget_recheck_replans_to_cheapest():
    # tersedia = 50jt - used - reservasi(1.27jt) ; batas plan = tersedia + 1.27jt
    repo = NegRepo(budget_used=50_000_000 - 1_300_000)
    out = NegotiatorAgent(repo).run(as_of=AS_OF)
    assert out["replanned"] is True and out["status"] == "completed"
    assert {c["selected"]["supplier_name"] for c in out["comparisons"]} == {"Supplier B"}
    assert any(s["tool_name"] == "replanning" for s in out["execution_trace"])


def test_budget_still_exceeded_after_replan_fails_without_persist():
    repo = NegRepo(budget_used=50_000_000 - 100_000, reserved=1_000_000)  # batas Rp 1 jt < termurah Rp 1.268.512
    out = NegotiatorAgent(repo).run(as_of=AS_OF)
    assert out["status"] == "failed" and repo.persisted is None
    assert any("jalankan ulang Purchaser" in w for w in out["warnings"])


def test_without_documents_prices_are_unverified_and_need_review():
    repo = NegRepo(with_docs=False)
    out = NegotiatorAgent(repo).run(as_of=AS_OF)
    assert out["status"] == "needs_review"
    step = next(s for s in out["execution_trace"] if s["tool_name"] == "rag_kontrak_supplier")
    assert step["status"] == "fallback"


def test_no_plan_is_no_action():
    out = NegotiatorAgent(NegRepo(plan=False, with_docs=False)).run(as_of=AS_OF)
    assert out["status"] == "no_action" and out.get("persisted") is False


def test_exports_render():
    repo = NegRepo()
    out = NegotiatorAgent(repo).run(as_of=AS_OF)
    assert po_pdf_bytes(out["pos"][0], is_dummy=True).startswith(b"%PDF")
    comps = [{"sku": c["item"]["sku"], "product_name": c["item"]["product_name"], "branch": c["item"]["branch"],
              "offers": c["rows"], "selected_supplier_id": c["selected"]["supplier_id"]} for c in out["comparisons"]]
    assert comparison_xlsx_bytes(comps, out["pos"], is_dummy=True)[:2] == b"PK"


def test_p3_migration_is_locked_down_and_rechecks_budget():
    sql = (ROOT / "supabase/migrations/20261109100000_p3_negotiator.sql").read_text().lower()
    assert " to anon" not in sql and "from anon, authenticated" in sql
    assert "auth.jwt() -> 'user_metadata'" not in sql
    assert "grant execute on function public.persist_negotiation(jsonb, jsonb, jsonb) to service_role" in sql
    assert sql.index("pg_advisory_xact_lock") < sql.index("delete from public.purchase_orders")
    assert "exceed authoritative available budget" in sql
    assert "not backed by procurement plan allocation" in sql
    assert "negotiation is locked" in sql
    assert "values ('supplier-documents', 'supplier-documents', false)" in sql
