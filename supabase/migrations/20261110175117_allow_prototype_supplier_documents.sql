alter table public.supplier_documents
  drop constraint if exists supplier_documents_source_check;

alter table public.supplier_documents
  add constraint supplier_documents_source_check
  check (source in ('upload', 'DUMMY-SEED', 'PROTOTYPE-SYNTHETIC'));
