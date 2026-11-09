"""Ekspor draft PO (PDF) dan matriks perbandingan supplier (Excel). Roadmap P3-15, F-35/SRS-F-51.

Dokumen ditandai DRAFT - BELUM DIKIRIM. Label CONTOH SIMULASI ditambahkan jika data dummy.
"""
from __future__ import annotations

import io

from src.config import LABEL_NOT_FOUND, LABEL_SIMULASI

TEAL_RGB = (14, 124, 97)
NAVY_RGB = (30, 42, 68)
MINT_RGB = (239, 244, 241)


def _rp(value) -> str:
    if value is None:
        return LABEL_NOT_FOUND
    return f"Rp {float(value):,.0f}".replace(",", ".")


def po_pdf_bytes(po: dict, is_dummy: bool) -> bytes:
    from fpdf import FPDF

    pdf = FPDF(format="A4")
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 9)
    pdf.set_text_color(*TEAL_RGB)
    label = "DRAFT - BELUM DIKIRIM - MENUNGGU APPROVAL MANAGER"
    if is_dummy:
        label = f"{LABEL_SIMULASI} | {label}"
    pdf.cell(0, 6, label, new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(*NAVY_RGB)
    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 10, f"Purchase Order {po['po_number']}", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", size=10)
    for label_text, value in (
        ("Supplier", po.get("supplier_name")),
        ("Gudang tujuan", po.get("branch")),
        ("Estimasi kedatangan", f"{po.get('expected_arrival')} (lead time {po.get('lead_time_days')} hari)"),
        ("Status", po.get("status", "draft")),
        ("Status approval", po.get("approval_status", "-")),
    ):
        pdf.cell(45, 6, label_text)
        pdf.cell(0, 6, str(value if value is not None else LABEL_NOT_FOUND), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(3)

    widths = (22, 48, 16, 28, 16, 28, 32)
    headers = ("SKU", "Nama", "Qty", "Harga list", "Disk %", "Harga/unit", "Subtotal")
    pdf.set_fill_color(*NAVY_RGB)
    pdf.set_text_color(255, 255, 255)
    pdf.set_font("Helvetica", "B", 9)
    for w, h in zip(widths, headers):
        pdf.cell(w, 7, h, border=1, fill=True)
    pdf.ln()
    pdf.set_text_color(*NAVY_RGB)
    pdf.set_font("Helvetica", size=9)
    for n, item in enumerate(po["items"]):
        fill = n % 2 == 1
        pdf.set_fill_color(*MINT_RGB)
        values = (item["sku"], str(item["product_name"])[:26], str(item["qty"]), _rp(item.get("list_price")),
                  f"{float(item.get('discount_pct') or 0):g}", _rp(item["unit_price"]), _rp(item["line_total"]))
        for w, v in zip(widths, values):
            pdf.cell(w, 7, v, border=1, fill=fill)
        pdf.ln()
    pdf.set_font("Helvetica", "B", 10)
    pdf.cell(sum(widths[:-1]), 8, "Total", border=1)
    pdf.cell(widths[-1], 8, _rp(po["total_amount"]), border=1, new_x="LMARGIN", new_y="NEXT")
    pdf.ln(4)
    pdf.set_font("Helvetica", size=8)
    pdf.multi_cell(0, 4.5, "Sumber harga: Supabase contracts (lihat matriks perbandingan supplier). "
                           "PO ini tidak mengikat sebelum disetujui manager via WhatsApp / dashboard.")
    return bytes(pdf.output())


def comparison_xlsx_bytes(comparisons: list[dict], pos: list[dict], is_dummy: bool) -> bytes:
    import xlsxwriter

    buf = io.BytesIO()
    wb = xlsxwriter.Workbook(buf, {"in_memory": True})
    head = wb.add_format({"bold": True, "font_color": "#FFFFFF", "bg_color": "#1E2A44", "border": 1})
    sel = wb.add_format({"bg_color": "#EFF4F1", "border": 1})
    cell = wb.add_format({"border": 1})
    money = wb.add_format({"border": 1, "num_format": "#,##0"})
    money_sel = wb.add_format({"border": 1, "num_format": "#,##0", "bg_color": "#EFF4F1"})
    note = wb.add_format({"bold": True, "font_color": "#0E7C61"})

    ws = wb.add_worksheet("Perbandingan Supplier")
    ws.write(0, 0, LABEL_SIMULASI if is_dummy else "Matriks perbandingan supplier StockQu", note)
    cols = ["SKU", "Nama", "Cabang", "Supplier", "Status", "Harga list", "Diskon %", "Harga efektif",
            "MOQ", "Pack", "Qty pesan", "Total biaya", "Lead time (hari)", "Tiba sebelum stok habis",
            "Performa", "Verifikasi dokumen", "Dipilih", "Sumber"]
    for c, h in enumerate(cols):
        ws.write(2, c, h, head)
    r = 3
    for comp in comparisons:
        for o in comp["offers"]:
            chosen = o["supplier_id"] == comp.get("selected_supplier_id")
            f, fm = (sel, money_sel) if chosen else (cell, money)
            vals = [comp["sku"], comp["product_name"], comp["branch"], o["supplier_name"], o["status"]]
            for c, v in enumerate(vals):
                ws.write(r, c, v, f)
            ws.write(r, 5, o.get("list_price") or LABEL_NOT_FOUND, fm)
            ws.write(r, 6, o.get("discount_pct"), f)
            ws.write(r, 7, o.get("effective_price") or LABEL_NOT_FOUND, fm)
            ws.write(r, 8, o.get("moq"), f)
            ws.write(r, 9, o.get("pack_size"), f)
            ws.write(r, 10, o.get("order_qty"), f)
            ws.write(r, 11, o.get("total_cost") or LABEL_NOT_FOUND, fm)
            ws.write(r, 12, o.get("lead_time_days") if o.get("lead_time_days") is not None else LABEL_NOT_FOUND, f)
            arrives = o.get("arrives_before_stockout")
            ws.write(r, 13, "-" if arrives is None else ("Ya" if arrives else "Tidak"), f)
            ws.write(r, 14, o.get("performance_score"), f)
            ws.write(r, 15, (o.get("verification") or {}).get("status", "-"), f)
            ws.write(r, 16, "YA" if chosen else "", f)
            ws.write(r, 17, o.get("source"), f)
            r += 1
    ws.set_column(0, 2, 12)
    ws.set_column(3, 16, 14)
    ws.set_column(17, 17, 60)

    ws2 = wb.add_worksheet("Draft PO")
    ws2.write(0, 0, "DRAFT - BELUM DIKIRIM" + (f" | {LABEL_SIMULASI}" if is_dummy else ""), note)
    cols2 = ["No PO", "Supplier", "Gudang", "SKU", "Qty", "Harga/unit", "Subtotal", "Total PO", "Estimasi tiba"]
    for c, h in enumerate(cols2):
        ws2.write(2, c, h, head)
    r = 3
    for po in pos:
        for item in po["items"]:
            for c, v in enumerate([po["po_number"], po["supplier_name"], po["branch"], item["sku"], item["qty"]]):
                ws2.write(r, c, v, cell)
            ws2.write(r, 5, item["unit_price"], money)
            ws2.write(r, 6, item["line_total"], money)
            ws2.write(r, 7, po["total_amount"], money)
            ws2.write(r, 8, po["expected_arrival"], cell)
            r += 1
    ws2.set_column(0, 8, 16)
    wb.close()
    return buf.getvalue()
