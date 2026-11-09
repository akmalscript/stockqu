"""Admin master data (F-04, F-05). Hanya Super Admin.

Semua tulis memakai JWT user -> RLS migration P5 yang menegakkan (bukan UI).
Setiap perubahan tercatat otomatis di audit_log oleh trigger database.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import pandas as pd
import streamlit as st

from src.config import BUDGET_MONTHLY, LABEL_NOT_FOUND
from src.services.auth import SUPPLIER_DOCUMENT_ADMIN_ROLES
from src.services.supabase_client import has_write_key
from src.ui.components import local_time
from src.ui.session import page_context
from src.utils.errors import safe_error

actor, user_client, repository = page_context(SUPPLIER_DOCUMENT_ADMIN_ROLES, "mengelola master data")
st.title("Admin")
st.caption("CRUD master data. Hak tulis ditegakkan RLS Supabase; setiap perubahan masuk audit log.")


@dataclass(frozen=True)
class Field:
    name: str
    label: str
    kind: str  # text | float | int | date | month | select
    required: bool = False
    min: float | None = None
    max: float | None = None
    default: object = None
    options_key: str | None = None


SPECS: dict[str, tuple[str, list[Field]]] = {
    "products": ("Produk", [
        Field("sku", "SKU", "text", True), Field("name", "Nama", "text", True), Field("category", "Kategori", "text"),
        Field("margin", "Margin (%)", "float", True, 0, 100, 0.0),
        Field("turnover_rate", "Turnover rate", "float", True, 0, None, 0.0),
        Field("lead_time_days", "Lead time (hari)", "int", True, 0, 120, 3),
        Field("safety_days", "Safety days", "int", True, 0, 60, 3),
    ]),
    "suppliers": ("Supplier", [
        Field("name", "Nama", "text", True), Field("contact", "Kontak (telepon)", "text"),
        Field("email", "Email PO", "text"), Field("lead_time_days", "Lead time (hari)", "int", True, 0, 120, 3),
        Field("performance_score", "Skor performa (0-100)", "float", True, 0, 100, 0.0),
    ]),
    "contracts": ("Kontrak", [
        Field("supplier_id", "Supplier", "select", True, options_key="suppliers"),
        Field("product_id", "Produk", "select", True, options_key="products"),
        Field("price", "Harga list / unit (Rp)", "float", True, 1, None, 1.0),
        Field("discount", "Diskon (%)", "float", True, 0, 100, 0.0),
        Field("valid_until", "Berlaku sampai", "date"),
        Field("moq", "MOQ", "int", True, 1, None, 1), Field("pack_size", "Kelipatan pack", "int", True, 1, None, 1),
    ]),
    "budgets": ("Budget", [
        Field("month", "Periode (YYYY-MM)", "month", True),
        Field("total_budget", "Total budget (Rp)", "float", True, 0, BUDGET_MONTHLY, float(BUDGET_MONTHLY)),
        Field("used_budget", "Terpakai (Rp)", "float", True, 0, BUDGET_MONTHLY, 0.0),
    ]),
}


def options() -> dict[str, dict[str, str]]:
    return {
        "suppliers": {str(s["id"]): s["name"] for s in repository.fetch_suppliers()},
        "products": {str(p["id"]): f"{p['sku']} - {p['name']}" for p in repository.fetch_products()},
    }


def render_form(table: str, fields: list[Field], current: dict | None, opts: dict, form_key: str) -> dict | None:
    values: dict = {}
    with st.form(form_key, clear_on_submit=current is None):
        for f in fields:
            cur = (current or {}).get(f.name, f.default)
            label = f.label + (" *" if f.required else "")
            if f.kind == "select":
                choices = opts.get(f.options_key, {})
                keys = list(choices)
                idx = keys.index(str(cur)) if cur is not None and str(cur) in keys else None
                values[f.name] = st.selectbox(label, keys, index=idx, format_func=lambda k, c=choices: c[k])
            elif f.kind == "float":
                values[f.name] = st.number_input(
                    label, min_value=None if f.min is None else float(f.min),
                    max_value=None if f.max is None else float(f.max),
                    value=float(cur if cur is not None else (f.min or 0.0)))
            elif f.kind == "int":
                values[f.name] = st.number_input(label, min_value=int(f.min or 0),
                                                 max_value=None if f.max is None else int(f.max),
                                                 value=int(cur if cur is not None else (f.min or 0)), step=1)
            elif f.kind == "date":
                values[f.name] = st.date_input(label, value=date.fromisoformat(str(cur)) if cur else None)
            else:
                values[f.name] = st.text_input(label, value=str(cur or ""))
        submitted = st.form_submit_button("Simpan", type="primary", icon=":material/save:")
    if not submitted:
        return None
    errors = []
    for f in fields:
        v = values[f.name]
        if f.required and (v is None or v == ""):
            errors.append(f"{f.label} wajib diisi")
        if f.kind == "month" and v and (len(v) != 7 or v[4] != "-" or not (v[:4] + v[5:]).isdigit()):
            errors.append("Periode harus berformat YYYY-MM")
        if f.name == "email" and v and "@" not in v:
            errors.append("Email tidak valid")
        if isinstance(v, date):
            values[f.name] = v.isoformat()
        if v == "":
            values[f.name] = None
    if errors:
        for e in errors:
            st.error(e)
        return None
    return values


def run_write(fn, success: str) -> None:
    try:
        fn()
    except Exception as exc:  # noqa: BLE001 - pesan aman, detail di log server
        text = str(exc)
        reason = ("ditolak RLS (bukan Super Admin?)" if "42501" in text or "row-level security" in text
                  else "melanggar aturan data" if "23514" in text or "check constraint" in text
                  else "masih dipakai data lain" if "23503" in text
                  else "duplikat" if "23505" in text else safe_error(exc))
        st.error(f"Gagal: {reason}")
        return
    st.cache_data.clear()
    st.success(success)


tabs = st.tabs(["Produk", "Supplier", "Kontrak", "Budget", "Dokumen kontrak", "Audit log", "Pengguna"])
try:
    opts = options()
except Exception as exc:  # noqa: BLE001
    st.error(f"{LABEL_NOT_FOUND} - master data tidak bisa dibaca ({safe_error(exc)}).")
    st.stop()

for tab, table in zip(tabs[:4], SPECS):
    title, fields = SPECS[table]
    with tab:
        try:
            rows = repository.fetch_admin_rows(table)
        except Exception as exc:  # noqa: BLE001
            st.error(f"{title}: {safe_error(exc)}")
            continue
        frame = pd.DataFrame(rows)
        for f in fields:
            if f.kind == "select" and f.name in frame:
                frame[f.name] = frame[f.name].map(lambda v, o=opts[f.options_key]: o.get(str(v), v))
        show_cols = [f.name for f in fields if f.name in frame.columns]
        st.dataframe(frame[show_cols] if show_cols else frame, hide_index=True, width="stretch")
        mode = st.segmented_control("Aksi", ["Tambah", "Ubah", "Hapus"], default="Tambah", key=f"mode-{table}")
        if mode == "Tambah":
            new = render_form(table, fields, None, opts, f"add-{table}")
            if new:
                run_write(lambda t=table, n=new: repository.admin_insert(t, n), f"{title} ditambahkan.")
        elif rows:
            label_of = {str(r["id"]): " · ".join(str(r.get(f.name) or "") for f in fields[:2]) for r in rows}
            row_id = st.selectbox("Pilih baris", list(label_of), format_func=label_of.get, key=f"pick-{table}")
            current = next(r for r in rows if str(r["id"]) == row_id)
            if mode == "Ubah":
                upd = render_form(table, fields, current, opts, f"edit-{table}-{row_id}")
                if upd:
                    run_write(lambda t=table, i=row_id, u=upd: repository.admin_update(t, i, u), f"{title} diperbarui.")
            else:
                st.warning(f"Hapus {label_of[row_id]}? Data yang dipakai PO/kontrak akan ditolak database.")
                if st.button("Hapus permanen", key=f"del-{table}-{row_id}", icon=":material/delete:"):
                    run_write(lambda t=table, i=row_id: repository.admin_delete(t, i), f"{title} dihapus.")

with tabs[4]:
    st.caption("Upload PDF kontrak/katalog/histori penawaran -> ekstraksi teks, chunk per Pasal, indeks RAG Negotiator.")
    if not has_write_key():
        st.info("SUPABASE_SERVICE_ROLE_KEY server belum diisi: upload ke Storage privat tidak tersedia.")
    with st.form("upload-doc", clear_on_submit=True):
        sup = st.selectbox("Supplier", list(opts["suppliers"]), format_func=opts["suppliers"].get)
        doc_type = st.selectbox("Jenis", ["contract", "catalog", "quotation_history"])
        valid_until = st.date_input("Berlaku sampai", value=None)
        upload = st.file_uploader("PDF (maks 10 MB, berisi teks)", type=["pdf"])
        go = st.form_submit_button("Upload & indeks", disabled=not has_write_key())
    if go and upload is not None:
        from src.services.supplier_documents import (
            DocumentRejected,
            ingest_supplier_pdf,
        )

        try:
            res = ingest_supplier_pdf(repository, supplier_id=sup, doc_type=doc_type, title=upload.name.rsplit(".", 1)[0],
                                      pdf_bytes=upload.getvalue(), uploaded_by=actor.user_id, valid_until=valid_until)
            st.success(f"{res['status']}: {res['chunks']} chunk diindeks.")
        except DocumentRejected as exc:
            st.error(str(exc))
        except Exception as exc:  # noqa: BLE001
            st.error(f"Upload gagal ({safe_error(exc)}).")
    try:
        docs = repository.fetch_supplier_documents()
        st.dataframe(pd.DataFrame([{"Supplier": opts["suppliers"].get(str(d["supplier_id"]), "?"), "Judul": d["title"],
                                    "Jenis": d["doc_type"], "Versi": d["version"], "Berlaku s/d": d.get("valid_until"),
                                    "Sumber": d["source"]} for d in docs]), hide_index=True, width="stretch")
    except Exception as exc:  # noqa: BLE001
        st.caption(f"Dokumen: {LABEL_NOT_FOUND} ({safe_error(exc)})")

with tabs[5]:
    try:
        audit = repository.fetch_audit_log(200)
        st.dataframe(pd.DataFrame([{"Waktu": local_time(a["created_at"]), "Tabel": a["table_name"], "Aksi": a["action"],
                                    "Baris": a.get("row_id"), "Actor": a.get("actor_id"), "Role": a.get("actor_role")}
                                   for a in audit]), hide_index=True, width="stretch")
    except Exception as exc:  # noqa: BLE001
        st.caption(f"Audit log: {LABEL_NOT_FOUND} - jalankan migration P5 ({safe_error(exc)})")

with tabs[6]:
    st.markdown(
        "Pengguna dikelola di **Supabase Dashboard -> Authentication -> Users**. Role dan cabang disimpan di "
        "`app_metadata` (tidak bisa diubah user sendiri):"
    )
    st.code('{"role": "warehouse_manager", "branch_ids": ["<uuid Branch 1>"]}', language="json")
    st.caption("Role: super_admin, warehouse_manager, purchasing_staff, procurement_manager. "
               "Kunci service role tidak pernah dipakai di browser untuk mengelola user.")
