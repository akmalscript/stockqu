"""Halaman Perbandingan Supplier & Draft PO - Agent 3 Negotiator (Roadmap P3-13..15).

Matriks >= 3 supplier per SKU-cabang, alasan pemilihan + kutipan kontrak (RAG),
draft PO dan draft email (BELUM DIKIRIM), ekspor PDF/Excel, dan Execution Trace.
"""
from __future__ import annotations

from datetime import date

import pandas as pd
import streamlit as st

from src.config import LABEL_NOT_FOUND, LABEL_SIMULASI, MIN_SUPPLIERS
from src.services.auth import (
    NEGOTIATOR_RUN_ROLES,
    NEGOTIATOR_VIEW_ROLES,
    SUPPLIER_DOCUMENT_ADMIN_ROLES,
)
from src.services.exports import comparison_xlsx_bytes, po_pdf_bytes
from src.services.supabase_client import has_write_key
from src.services.work_admission import (
    WorkflowDeadlineExceeded,
    WorkRejected,
    work_admission,
)
from src.ui.session import page_context
from src.utils.errors import safe_error

actor, user_client, repository = page_context(NEGOTIATOR_VIEW_ROLES, "melihat perbandingan supplier")
st.title("Perbandingan Supplier & Draft PO")
st.caption(
    f"Agent 3 - The Negotiator | minimal {MIN_SUPPLIERS} supplier per item | kutipan kontrak (RAG) | "
    "draft PO + email BELUM DIKIRIM - approval manager di tahap berikutnya"
)


def rupiah(value) -> str:
    return LABEL_NOT_FOUND if value is None else f"Rp {float(value):,.0f}".replace(",", ".")


# ------------------------------------------------------------------ sidebar
with st.sidebar:
    st.header("Agent Negotiator")
    can_write = has_write_key()
    can_run = actor.role in NEGOTIATOR_RUN_ROLES
    if not can_write:
        st.caption("SUPABASE_SERVICE_ROLE_KEY belum diisi: hasil hanya preview dan tidak tersimpan.")
    if st.button("Jalankan Agent Negotiator", type="primary", width="stretch", disabled=not can_run):
        from src.agents.negotiator import run_negotiator

        try:
            with work_admission.admit(actor, "negotiator"), st.status(
                "Negotiator membaca plan, membandingkan supplier, mengutip kontrak, membuat draft PO...",
                expanded=False,
            ) as status:
                output = run_negotiator(actor=actor, persist=can_write, read_client=user_client)
                ok = output.get("status") in ("completed", "needs_review", "no_action")
                status.update(label=f"Selesai: status {output.get('status')}", state="complete" if ok else "error")
            st.session_state["negotiator_preview"] = None if output.get("persisted") else output
            for warning in output.get("warnings", []):
                st.warning(warning)
        except (WorkRejected, WorkflowDeadlineExceeded, PermissionError) as exc:
            st.error(str(exc))

    if actor.role in SUPPLIER_DOCUMENT_ADMIN_ROLES:
        st.divider()
        st.subheader("Upload dokumen supplier")
        try:
            supplier_rows = repository.fetch_suppliers()
        except Exception:  # noqa: BLE001
            supplier_rows = []
        with st.form("upload_supplier_doc", clear_on_submit=True):
            names = {s["name"]: s["id"] for s in supplier_rows}
            supplier_name = st.selectbox("Supplier", list(names) or [LABEL_NOT_FOUND])
            doc_type = st.selectbox("Jenis", ["contract", "catalog", "quotation_history"])
            valid_until = st.date_input("Berlaku sampai", value=None)
            upload = st.file_uploader("PDF (maks 10 MB, harus berisi teks)", type=["pdf"])
            submitted = st.form_submit_button("Upload & indeks", disabled=not (can_write and names))
        if submitted and upload is not None:
            from src.services.supplier_documents import (
                DocumentRejected,
                ingest_supplier_pdf,
            )

            try:
                res = ingest_supplier_pdf(
                    repository, supplier_id=names[supplier_name], doc_type=doc_type,
                    title=upload.name.rsplit(".", 1)[0], pdf_bytes=upload.getvalue(),
                    uploaded_by=actor.user_id, valid_until=valid_until,
                )
                st.success(f"{res['status']}: {res['chunks']} chunk diindeks.")
            except DocumentRejected as exc:
                st.error(str(exc))
            except Exception as exc:  # noqa: BLE001
                st.error(f"Upload gagal ({safe_error(exc)}).")

# ------------------------------------------------------------------ data
try:
    products = {str(p["id"]): p for p in repository.fetch_products()}
    branches = {str(b["id"]): b["name"] for b in repository.fetch_branches()}
    suppliers = {str(s["id"]): s["name"] for s in repository.fetch_suppliers()}
    run = repository.latest_negotiation_run()
except Exception as exc:  # noqa: BLE001
    if "PGRST205" in str(exc) or "negotiation_runs" in str(exc):
        st.error(f"{LABEL_NOT_FOUND} - skema P3 belum tersedia. Jalankan migration "
                 "`supabase/migrations/20261109100000_p3_negotiator.sql`, lalu muat ulang halaman.")
    else:
        st.error(f"{LABEL_NOT_FOUND} - Supabase tidak bisa dibaca ({safe_error(exc)}).")
    st.stop()

preview = st.session_state.get("negotiator_preview")
if preview and preview.get("comparisons"):
    st.info(f"Menampilkan run {preview['run_id']} yang BELUM tersimpan.")
    run_id, run_status = preview["run_id"], preview["status"]
    comparisons = [{
        "sku": c["item"]["sku"], "product_name": c["item"]["product_name"], "branch": c["item"]["branch"],
        "offers": c["rows"], "selected_supplier_id": c["selected"]["supplier_id"] if c["selected"] else None,
        "selection_status": c["selection_status"], "valid_supplier_count": c["valid_supplier_count"],
        "reason_trace": {"summary": c["reason"]},
        "citations": preview.get("citations", {}).get(c["selected"]["contract_id"], []) if c["selected"] else [],
    } for c in preview["comparisons"]]
    pos = preview.get("pos", [])
    logs = preview["execution_trace"]
elif run:
    run_id, run_status = run["run_id"], run["status"]
    comparisons = [{
        **c, "sku": products.get(str(c["product_id"]), {}).get("sku", LABEL_NOT_FOUND),
        "product_name": products.get(str(c["product_id"]), {}).get("name", LABEL_NOT_FOUND),
        "branch": branches.get(str(c["branch_id"]), LABEL_NOT_FOUND),
    } for c in repository.fetch_supplier_comparisons(run["id"])]
    pos = [{**po["draft"], "status": po["status"]} for po in repository.fetch_draft_purchase_orders(run["id"])]
    logs = repository.fetch_agent_logs(run_id)
else:
    st.warning(f"{LABEL_NOT_FOUND} - belum ada hasil Negotiator. Jalankan Purchaser (halaman Rencana Pembelian), "
               "lalu klik **Jalankan Agent Negotiator** di sidebar.")
    st.stop()

try:
    is_dummy = repository.has_dummy_sales()
except Exception:  # noqa: BLE001
    is_dummy = True

# ------------------------------------------------------------------ KPI
review = sum(c["selection_status"] != "selected" for c in comparisons)
c1, c2, c3, c4 = st.columns(4)
c1.metric("SKU-cabang", len(comparisons), help="Item plan Purchaser yang dibandingkan")
c2.metric("Draft PO", len(pos), help="Satu PO per supplier x gudang tujuan")
c3.metric("Total draft", rupiah(sum(float(p["total_amount"]) for p in pos)))
c4.metric("Perlu review", review, help=f"< {MIN_SUPPLIERS} supplier valid, harga belum terverifikasi, atau tanpa penawaran")
st.caption(f"Run `{run_id}` | status **{run_status}** | sumber: Supabase contracts + suppliers + dokumen kontrak")

# ------------------------------------------------------------------ matriks
st.subheader("Matriks perbandingan supplier")
if is_dummy:
    st.warning(LABEL_SIMULASI)
for comp in comparisons:
    sel_id = comp.get("selected_supplier_id")
    sel_name = suppliers.get(str(sel_id), LABEL_NOT_FOUND) if sel_id else "tidak ada"
    with st.expander(f"[{comp['selection_status']}] {comp['sku']} - {comp['product_name']} | {comp['branch']} "
                     f"-> {sel_name}", expanded=comp["selection_status"] != "selected"):
        table = pd.DataFrame([{
            "Dipilih": "✓" if o["supplier_id"] == sel_id else "",
            "Supplier": o["supplier_name"],
            "Status": o["status"],
            "Harga list": o.get("list_price"),
            "Diskon %": o.get("discount_pct"),
            "Harga efektif": o.get("effective_price"),
            "MOQ/pack": f"{o.get('moq')}/{o.get('pack_size')}",
            "Qty": o.get("order_qty"),
            "Total": o.get("total_cost"),
            "Lead time": o.get("lead_time_days"),
            "Tiba sebelum habis": {True: "Ya", False: "Tidak"}.get(o.get("arrives_before_stockout"), "-"),
            "Performa": o.get("performance_score"),
            "Dokumen": (o.get("verification") or {}).get("status", "-"),
        } for o in comp["offers"]])
        st.dataframe(table, hide_index=True, width="stretch", column_config={
            "Harga list": st.column_config.NumberColumn(format="%.0f"),
            "Harga efektif": st.column_config.NumberColumn(format="%.0f"),
            "Total": st.column_config.NumberColumn(format="%.0f"),
        })
        trace = comp.get("reason_trace") or {}
        st.markdown(f"**Alasan:** {trace.get('summary', LABEL_NOT_FOUND)}")
        for cite in comp.get("citations") or []:
            st.markdown(f"> \"{cite['quote']}\"  \n"
                        f"> — {cite['document_title']} v{cite['version']}, {cite['clause_ref']}, hal. {cite['page']}, "
                        f"berlaku s/d {cite['valid_until'] or LABEL_NOT_FOUND}")
        if not comp.get("citations"):
            st.caption(f"Kutipan kontrak: {LABEL_NOT_FOUND}")
        if trace.get("evidence"):
            with st.popover("Evidence & asumsi"):
                st.dataframe(pd.DataFrame(trace["evidence"]).astype(str), hide_index=True, width="stretch")
                st.markdown("\n".join(f"- {a}" for a in trace.get("assumptions", [])))

st.download_button(
    "Unduh matriks + draft PO (Excel)",
    data=comparison_xlsx_bytes(comparisons, pos, is_dummy),
    file_name=f"perbandingan-supplier-{run_id}.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
)

# ------------------------------------------------------------------ draft PO
st.subheader("Draft Purchase Order")
st.caption("DRAFT - BELUM DIKIRIM. PO dan email hanya dikirim setelah manager membalas OK (P4).")
for po in pos:
    with st.expander(f"{po['po_number']} | {po['supplier_name']} -> {po['branch']} | {rupiah(po['total_amount'])}"):
        st.markdown(f"Estimasi tiba **{po['expected_arrival']}** (lead time {po['lead_time_days']} hari) · "
                    f"status `{po.get('status', 'draft')}` · {po.get('approval_status', '')}")
        st.dataframe(pd.DataFrame([{
            "SKU": i["sku"], "Nama": i["product_name"], "Qty": i["qty"], "Harga/unit": i["unit_price"],
            "Diskon %": i["discount_pct"], "Subtotal": i["line_total"],
        } for i in po["items"]]), hide_index=True, width="stretch")
        emails = po.get("emails", {})
        t1, t2 = st.tabs(["Draft email PO", "Draft email negosiasi"])
        for tab, key in ((t1, "purchase_order"), (t2, "negotiation")):
            with tab:
                mail = emails.get(key)
                if mail:
                    st.caption(mail.get("status", ""))
                    st.text_input("Subjek", mail["subject"], disabled=True, key=f"{po['po_number']}-{key}-subj")
                    st.code(mail["body"], language="text")
                else:
                    st.caption(LABEL_NOT_FOUND)
        st.download_button("Unduh PDF draft PO", data=po_pdf_bytes(po, is_dummy),
                           file_name=f"{po['po_number']}.pdf", mime="application/pdf", key=f"pdf-{po['po_number']}")

# ------------------------------------------------------------------ trace + dokumen
st.subheader("Execution Trace - Negotiator")
if logs:
    df = pd.DataFrame(logs)
    cols = [c for c in ["created_at", "tool_name", "status", "input_summary", "output_summary", "error_message"]
            if c in df.columns]
    st.dataframe(df[cols], hide_index=True, width="stretch")
else:
    st.caption(LABEL_NOT_FOUND)

with st.expander("Dokumen kontrak terindeks (RAG)"):
    try:
        docs = repository.fetch_supplier_documents()
        if docs:
            st.dataframe(pd.DataFrame([{
                "Supplier": suppliers.get(str(d["supplier_id"]), LABEL_NOT_FOUND), "Judul": d["title"],
                "Jenis": d["doc_type"], "Versi": d["version"], "Halaman": d["page_count"],
                "Berlaku s/d": d.get("valid_until") or "-", "Sumber": d["source"],
            } for d in docs]), hide_index=True, width="stretch")
        else:
            st.caption(f"{LABEL_NOT_FOUND} - jalankan `python -m scripts.seed_supplier_documents`.")
    except Exception as exc:  # noqa: BLE001
        st.caption(f"{LABEL_NOT_FOUND} ({safe_error(exc)})")
st.caption(f"Data per {date.today().isoformat()}")
