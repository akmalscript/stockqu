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


def comparison_pdf_bytes(comparisons: list[dict], is_dummy: bool, run_id: str) -> bytes:
    """Laporan perbandingan supplier (PDF, F-35). Satu tabel per SKU-cabang, supplier terpilih ditandai."""
    from fpdf import FPDF

    pdf = FPDF(format="A4", orientation="L")
    pdf.set_auto_page_break(auto=True, margin=12)
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 9)
    pdf.set_text_color(*TEAL_RGB)
    pdf.cell(0, 6, (f"{LABEL_SIMULASI} | " if is_dummy else "") + f"Run Negotiator {run_id}",
             new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(*NAVY_RGB)
    pdf.set_font("Helvetica", "B", 15)
    pdf.cell(0, 10, "Laporan Perbandingan Supplier", new_x="LMARGIN", new_y="NEXT")
    widths = (8, 38, 26, 18, 26, 18, 24, 30, 20, 22, 20, 26)
    headers = ("", "Supplier", "Harga list", "Disk %", "Harga efektif", "Qty", "MOQ/pack", "Total",
               "Lead", "Tiba?", "Perf.", "Dokumen")
    for comp in comparisons:
        pdf.ln(2)
        pdf.set_font("Helvetica", "B", 10)
        pdf.cell(0, 7, f"{comp['sku']} - {comp['product_name']} | {comp['branch']} | {comp.get('selection_status', '-')}",
                 new_x="LMARGIN", new_y="NEXT")
        pdf.set_fill_color(*NAVY_RGB)
        pdf.set_text_color(255, 255, 255)
        pdf.set_font("Helvetica", "B", 8)
        for w, h in zip(widths, headers):
            pdf.cell(w, 6, h, border=1, fill=True)
        pdf.ln()
        pdf.set_text_color(*NAVY_RGB)
        pdf.set_font("Helvetica", size=8)
        for o in comp["offers"]:
            chosen = o["supplier_id"] == comp.get("selected_supplier_id")
            pdf.set_fill_color(*MINT_RGB)
            arrives = o.get("arrives_before_stockout")
            vals = ("*" if chosen else "", str(o["supplier_name"])[:22], _rp(o.get("list_price")),
                    f"{float(o.get('discount_pct') or 0):g}", _rp(o.get("effective_price")), str(o.get("order_qty")),
                    f"{o.get('moq')}/{o.get('pack_size')}", _rp(o.get("total_cost")), f"{o.get('lead_time_days')} hr",
                    "-" if arrives is None else ("Ya" if arrives else "Tidak"), f"{o.get('performance_score')}",
                    (o.get("verification") or {}).get("status", "-"))
            for w, v in zip(widths, vals):
                pdf.cell(w, 6, v, border=1, fill=chosen)
            pdf.ln()
        reason = (comp.get("reason_trace") or {}).get("summary")
        if reason:
            pdf.set_font("Helvetica", "I", 8)
            pdf.multi_cell(0, 4.5, f"Alasan: {reason}", new_x="LMARGIN", new_y="NEXT")
    return bytes(pdf.output())


def procurement_report_xlsx(*, plan: dict | None, plan_items: list[dict], comparisons: list[dict],
                            pos: list[dict], is_dummy: bool) -> bytes:
    """Excel DESIGN 16.2: Ringkasan, Item PO, Matriks Supplier, Alasan Prioritas."""
    import xlsxwriter

    buf = io.BytesIO()
    wb = xlsxwriter.Workbook(buf, {"in_memory": True})
    head = wb.add_format({"bold": True, "font_color": "#FFFFFF", "bg_color": "#1E2A44", "border": 1})
    cell = wb.add_format({"border": 1, "text_wrap": True, "valign": "top"})
    money = wb.add_format({"border": 1, "num_format": "#,##0"})
    note = wb.add_format({"bold": True, "font_color": "#0E7C61"})

    def sheet(name: str, cols: list[str], rows: list[list], money_cols: tuple[int, ...] = ()):
        ws = wb.add_worksheet(name)
        ws.write(0, 0, LABEL_SIMULASI if is_dummy else f"StockQu - {name}", note)
        for c, h in enumerate(cols):
            ws.write(2, c, h, head)
        for r, row in enumerate(rows, start=3):
            for c, v in enumerate(row):
                ws.write(r, c, LABEL_NOT_FOUND if v is None else v, money if c in money_cols else cell)
        ws.set_column(0, len(cols) - 1, 18)
        return ws

    p = plan or {}
    sheet("Ringkasan", ["Metrik", "Nilai"], [
        ["Run Purchaser", p.get("run_id")], ["Periode", p.get("period")], ["Status plan", p.get("status")],
        ["Budget tersedia", p.get("budget_available")], ["Total plan", p.get("proposed_total")],
        ["Jumlah draft PO", len(pos)], ["Total draft PO", round(sum(float(x["total_amount"]) for x in pos), 2)],
    ], money_cols=())
    sheet("Item PO", ["No PO", "Supplier", "Gudang", "SKU", "Nama", "Qty", "Harga/unit", "Subtotal", "Status"],
          [[po["po_number"], po.get("supplier_name"), po.get("branch"), i["sku"], i["product_name"], i["qty"],
            i["unit_price"], i["line_total"], po.get("status", "draft")] for po in pos for i in po["items"]],
          money_cols=(6, 7))
    sheet("Matriks Supplier", ["SKU", "Cabang", "Supplier", "Harga efektif", "Total", "Lead time", "Performa",
                               "Dokumen", "Dipilih"],
          [[c["sku"], c["branch"], o["supplier_name"], o.get("effective_price"), o.get("total_cost"),
            o.get("lead_time_days"), o.get("performance_score"), (o.get("verification") or {}).get("status"),
            "YA" if o["supplier_id"] == c.get("selected_supplier_id") else ""]
           for c in comparisons for o in c["offers"]], money_cols=(3, 4))
    sheet("Alasan Prioritas", ["Rank", "SKU", "Cabang", "Keputusan", "Qty alokasi", "Total", "Alasan"],
          [[i.get("rank"), i.get("sku"), i.get("branch"), i.get("decision"), i.get("allocated_qty"), i.get("line_total"),
            (i.get("reason_trace") or {}).get("summary")] for i in plan_items], money_cols=(5,))
    wb.close()
    return buf.getvalue()


def analyst_xlsx(forecasts: list[dict], is_dummy: bool) -> bytes:
    """Hasil analisis Analyst (SRS-F-51)."""
    import xlsxwriter

    buf = io.BytesIO()
    wb = xlsxwriter.Workbook(buf, {"in_memory": True})
    head = wb.add_format({"bold": True, "font_color": "#FFFFFF", "bg_color": "#1E2A44", "border": 1})
    note = wb.add_format({"bold": True, "font_color": "#0E7C61"})
    ws = wb.add_worksheet("Prediksi")
    ws.write(0, 0, LABEL_SIMULASI if is_dummy else "StockQu - hasil Agent Analyst", note)
    cols = ["SKU", "Cabang", "Risiko", "Stok", "Rata-rata/hari", "Forecast/hari", "Days-to-stockout",
            "DTS forecast", "Restock", "Lead time", "Ringkasan"]
    for c, h in enumerate(cols):
        ws.write(2, c, h, head)
    for r, f in enumerate(forecasts, start=3):
        vals = [f.get("sku"), f.get("branch"), f.get("risk_level"), f.get("stock_qty"), f.get("avg_daily_sales"),
                f.get("forecast_daily_demand"), f.get("days_to_stockout"), f.get("days_to_stockout_forecast"),
                f.get("restock_qty"), f.get("lead_time_days"), (f.get("reason_trace") or {}).get("summary")]
        for c, v in enumerate(vals):
            ws.write(r, c, LABEL_NOT_FOUND if v is None else v)
    ws.set_column(0, 9, 14)
    ws.set_column(10, 10, 80)
    wb.close()
    return buf.getvalue()
