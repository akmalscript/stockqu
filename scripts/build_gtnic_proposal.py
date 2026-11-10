"""Build the StockQu GTNIC proposal from the checked-in HTML source."""
from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

from pypdf import PdfReader, PdfWriter

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "docs/proposal/proposal_gtnic_2026.html"
OUTPUT = ROOT / "docs/Proposal_GTNIC_2026_StockQu_Final.pdf"
CHROME_CANDIDATES = (
    Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
    Path("/Applications/Chromium.app/Contents/MacOS/Chromium"),
)

OUTLINES = (
    ("1. Latar Belakang Masalah", 1),
    ("2. Tujuan, Manfaat, dan Solusi", 2),
    ("3. Target Pengguna", 3),
    ("4. Fitur Utama dan Keunikan", 4),
    ("5. Arsitektur, Database, dan Teknologi", 5),
    ("5.2 Arsitektur Multi-Agent", 6),
    ("5.3 Model Data dan Retrieval", 7),
    ("5.4 Logika Agent dan Observability", 8),
    ("5.7 Human-in-the-Loop dan Evaluasi", 9),
    ("6. Batasan Sistem", 10),
    ("7. Screenshot Inventory dan Analyst", 11),
    ("7. Screenshot Purchaser dan Negotiator", 12),
    ("7. Screenshot Trace dan Approval", 13),
    ("8–9. Future Development dan Lampiran", 14),
)


def find_chrome() -> Path:
    for candidate in CHROME_CANDIDATES:
        if candidate.exists():
            return candidate
    found = shutil.which("google-chrome") or shutil.which("chromium")
    if found:
        return Path(found)
    raise SystemExit("Google Chrome/Chromium tidak ditemukan")


def main() -> None:
    chrome = find_chrome()
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="stockqu-proposal-") as temp_dir:
        raw_pdf = Path(temp_dir) / "proposal-raw.pdf"
        subprocess.run(
            [
                str(chrome),
                "--headless=new",
                "--disable-gpu",
                "--allow-file-access-from-files",
                "--no-pdf-header-footer",
                "--export-tagged-pdf",
                f"--print-to-pdf={raw_pdf}",
                SOURCE.resolve().as_uri(),
            ],
            check=True,
        )
        reader = PdfReader(raw_pdf)
        if len(reader.pages) != 15:
            raise SystemExit(f"Layout menghasilkan {len(reader.pages)} halaman, seharusnya 15")

        # Clone the tagged Chrome document so the structure tree survives.
        writer = PdfWriter(clone_from=raw_pdf)
        writer.add_metadata(
            {
                "/Title": "StockQu — Proposal GTNIC 2026",
                "/Author": "Tim Suddenly — Universitas Diponegoro",
                "/Subject": "AI Agent Development — Multi-Agent Procurement System",
                "/Keywords": "StockQu, GTNIC 2026, LangGraph, procurement, inventory",
                "/Language": "id-ID",
            }
        )
        for title, page_index in OUTLINES:
            writer.add_outline_item(title, page_index)
        with OUTPUT.open("wb") as handle:
            writer.write(handle)
    print(OUTPUT)


if __name__ == "__main__":
    main()
