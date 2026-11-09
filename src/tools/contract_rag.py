"""RAG kontrak supplier (SRS-F-24/53..57, roadmap P3-14).

Pipeline nyata: PDF -> ekstraksi teks (pypdf) -> chunk per "Pasal" -> simpan ke
supplier_document_chunks -> retrieval.

Retrieval = metadata filter (supplier, dokumen terbaru yang masih berlaku) + keyword
scoring BM25. Ini BUKAN semantic vector search: pgvector + embedding (SRS-F-54) butuh
provider embedding yang belum dipilih, jadi tidak diklaim. Setiap kutipan membawa
referensi dokumen, pasal, halaman, supplier, dan tanggal berlaku (SRS-F-56).

Verifikasi komersial (SRS-F-57): angka harga/diskon/MOQ/pack yang tertulis di klausul
dicocokkan dengan data terstruktur ``contracts``. Beda = ditandai, bukan ditebak.
"""
from __future__ import annotations

import io
import math
import re
from collections import Counter
from dataclasses import asdict, dataclass

CLAUSE_RE = re.compile(r"^\s*(Pasal\s+\d+[A-Za-z]?)\b[^\n]*", re.IGNORECASE | re.MULTILINE)
TOKEN_RE = re.compile(r"[a-z0-9]+(?:[-.][a-z0-9]+)*")
QUOTE_MAX = 320

# Baris harga di klausul: "SKU-A | Item A | Rp 11.500 | diskon 2% | MOQ 1 | pack 1"
PRICE_LINE_RE = re.compile(
    r"(?P<sku>SKU-[A-Z0-9]+)\s*\|[^|\n]*\|\s*Rp\s*(?P<price>[\d.]+)(?:,\d+)?\s*\|\s*diskon\s*(?P<disc>[\d.,]+)\s*%"
    r"\s*\|\s*MOQ\s*(?P<moq>\d+)\s*\|\s*pack\s*(?P<pack>\d+)",
    re.IGNORECASE,
)


@dataclass
class Chunk:
    chunk_index: int
    page: int | None
    clause_ref: str
    content: str


@dataclass
class Citation:
    document_id: str
    document_title: str
    supplier_id: str
    clause_ref: str
    page: int | None
    quote: str
    valid_until: str | None
    version: int
    score: float

    def to_dict(self) -> dict:
        return asdict(self)


def extract_pdf_pages(pdf_bytes: bytes) -> list[str]:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(pdf_bytes))
    return [(page.extract_text() or "") for page in reader.pages]


def chunk_pages(pages: list[str]) -> list[Chunk]:
    """Pecah per 'Pasal N'. Teks sebelum pasal pertama jadi chunk 'Pembukaan'."""
    chunks: list[Chunk] = []
    for page_no, text in enumerate(pages, start=1):
        text = text.replace("\r", "")
        marks = list(CLAUSE_RE.finditer(text))
        if not marks:
            if text.strip():
                ref = chunks[-1].clause_ref if chunks else "Pembukaan"
                chunks.append(Chunk(len(chunks), page_no, ref, _clean(text)))
            continue
        head = text[: marks[0].start()].strip()
        if head:
            ref = chunks[-1].clause_ref if chunks else "Pembukaan"
            chunks.append(Chunk(len(chunks), page_no, ref, _clean(head)))
        for i, m in enumerate(marks):
            end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
            body = _clean(text[m.start():end])
            if body:
                chunks.append(Chunk(len(chunks), page_no, _title_case(m.group(1)), body))
    return chunks


def _clean(text: str) -> str:
    return re.sub(r"[ \t]+", " ", re.sub(r"\n{2,}", "\n", text)).strip()


def _title_case(ref: str) -> str:
    return re.sub(r"\s+", " ", ref.strip()).capitalize()


def tokenize(text: str) -> list[str]:
    return TOKEN_RE.findall(text.lower())


def bm25_rank(query: str, docs: list[str], k1: float = 1.5, b: float = 0.75) -> list[float]:
    tokens = [tokenize(d) for d in docs]
    if not docs:
        return []
    avgdl = sum(len(t) for t in tokens) / len(tokens) or 1.0
    df = Counter(term for t in tokens for term in set(t))
    n = len(docs)
    q = tokenize(query)
    scores = []
    for t in tokens:
        tf = Counter(t)
        s = 0.0
        for term in q:
            if term not in tf:
                continue
            idf = math.log(1 + (n - df[term] + 0.5) / (df[term] + 0.5))
            s += idf * tf[term] * (k1 + 1) / (tf[term] + k1 * (1 - b + b * len(t) / avgdl))
        scores.append(s)
    return scores


def latest_documents(documents: list[dict], supplier_id: str, as_of_iso: str) -> list[dict]:
    """Metadata filter: dokumen supplier ini, versi terbaru per jenis, belum kedaluwarsa."""
    best: dict[str, dict] = {}
    for doc in documents:
        if str(doc["supplier_id"]) != str(supplier_id):
            continue
        if doc.get("valid_until") and str(doc["valid_until"]) < as_of_iso:
            continue
        key = doc["doc_type"]
        if key not in best or int(doc.get("version") or 1) > int(best[key].get("version") or 1):
            best[key] = doc
    return list(best.values())


def retrieve(
    query: str,
    supplier_id: str,
    documents: list[dict],
    chunks: list[dict],
    as_of_iso: str,
    top_k: int = 2,
) -> list[Citation]:
    docs = {str(d["id"]): d for d in latest_documents(documents, supplier_id, as_of_iso)}
    pool = [c for c in chunks if str(c["document_id"]) in docs]
    scores = bm25_rank(query, [f"{c['clause_ref']} {c['content']}" for c in pool])
    ranked = sorted(zip(scores, pool), key=lambda x: (-x[0], x[1]["chunk_index"]))
    out: list[Citation] = []
    for score, c in ranked[:top_k]:
        if score <= 0:
            continue
        doc = docs[str(c["document_id"])]
        out.append(Citation(
            document_id=str(doc["id"]),
            document_title=doc["title"],
            supplier_id=str(doc["supplier_id"]),
            clause_ref=c["clause_ref"],
            page=c.get("page"),
            quote=_quote_for(c["content"], query),
            valid_until=None if doc.get("valid_until") is None else str(doc["valid_until"]),
            version=int(doc.get("version") or 1),
            score=round(score, 4),
        ))
    return out


def _quote_for(content: str, query: str) -> str:
    """Kutip baris yang paling relevan (mis. baris SKU), maksimal QUOTE_MAX karakter."""
    terms = set(tokenize(query))
    lines = [ln.strip() for ln in content.split("\n") if ln.strip()]
    if not lines:
        return ""
    header, body = lines[0], lines[1:]
    if not body:
        quote = header
    else:
        # Kutip baris isi paling relevan (bukan judul pasal saja)
        best = max(body, key=lambda ln: (len(terms & set(tokenize(ln))), -len(ln)))
        quote = f"{header} ... {best}"
    return quote if len(quote) <= QUOTE_MAX else quote[: QUOTE_MAX - 3] + "..."


def parse_price_terms(text: str) -> dict[str, dict]:
    """Ambil angka harga per SKU dari teks klausul (format baris harga kontrak StockQu)."""
    out: dict[str, dict] = {}
    for m in PRICE_LINE_RE.finditer(text):
        out[m.group("sku").upper()] = {
            "price": float(m.group("price").replace(".", "")),
            "discount_pct": float(m.group("disc").replace(",", ".")),
            "moq": int(m.group("moq")),
            "pack_size": int(m.group("pack")),
        }
    return out


def verify_offer_against_clauses(sku: str, offer: dict, supplier_chunks: list[dict]) -> dict:
    """SRS-F-57: cocokkan data terstruktur contracts dengan angka di dokumen kontrak."""
    found = None
    ref = None
    for c in supplier_chunks:
        terms = parse_price_terms(c["content"]).get(sku.upper())
        if terms:
            found, ref = terms, c["clause_ref"]
            break
    if found is None:
        return {"status": "unverified", "note": f"Klausul harga {sku} di dokumen kontrak: DATA TIDAK DITEMUKAN"}
    mismatches = []
    if offer.get("list_price") is not None and abs(found["price"] - float(offer["list_price"])) > 0.5:
        mismatches.append(f"harga dokumen Rp {found['price']:,.0f} vs contracts Rp {float(offer['list_price']):,.0f}")
    if abs(found["discount_pct"] - float(offer.get("discount_pct") or 0)) > 0.01:
        mismatches.append(f"diskon dokumen {found['discount_pct']}% vs contracts {offer.get('discount_pct')}%")
    if found["moq"] != int(offer.get("moq") or 1):
        mismatches.append(f"MOQ dokumen {found['moq']} vs contracts {offer.get('moq')}")
    if found["pack_size"] != int(offer.get("pack_size") or 1):
        mismatches.append(f"pack dokumen {found['pack_size']} vs contracts {offer.get('pack_size')}")
    if mismatches:
        return {"status": "mismatch", "clause_ref": ref, "note": "; ".join(mismatches)}
    return {"status": "verified", "clause_ref": ref, "note": f"Cocok dengan {ref} dokumen kontrak"}
