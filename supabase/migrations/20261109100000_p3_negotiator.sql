-- StockQu - P3 Negotiator
-- Jalankan SETELAH 20261107190000_p1_p2_security_hardening.sql.
-- Menambah:
--   supplier_documents + supplier_document_chunks  (kontrak PDF + chunk untuk RAG, SRS-F-53/54/56)
--   negotiation_runs + supplier_comparisons         (matriks >= 3 supplier + alasan pemilihan, SRS-F-27/28)
--   kolom draft PO di purchase_orders / po_items    (SRS-F-30, roadmap P3-15)
--   RPC persist_negotiation                         (validasi deterministik + budget recheck, SRS-F-31/36)
-- Draft PO BELUM dikirim dan BELUM masuk approval. Status 'draft' tidak dihitung committed;
-- reservasi plan Purchaser tetap aktif sampai P4 mengubah PO menjadi waiting_approval.

begin;

-- ---------------------------------------------------------------- 1. dokumen supplier
create table if not exists public.supplier_documents (
  id uuid primary key default gen_random_uuid(),
  supplier_id uuid not null references public.suppliers(id) on delete restrict,
  doc_type text not null check (doc_type in ('contract', 'catalog', 'quotation_history')),
  title text not null,
  storage_bucket text not null default 'supplier-documents',
  storage_path text not null,
  version integer not null default 1 check (version > 0),
  valid_from date,
  valid_until date,
  sha256 text not null,
  page_count integer not null default 0 check (page_count >= 0),
  source text not null default 'upload' check (source in ('upload', 'DUMMY-SEED')),
  uploaded_by text not null,
  created_at timestamptz not null default now(),
  unique (supplier_id, doc_type, version),
  unique (sha256)
);

create index if not exists idx_supplier_documents_supplier
  on public.supplier_documents (supplier_id, doc_type, version desc);

create table if not exists public.supplier_document_chunks (
  id bigint generated always as identity primary key,
  document_id uuid not null references public.supplier_documents(id) on delete cascade,
  chunk_index integer not null check (chunk_index >= 0),
  page integer,
  clause_ref text not null,
  content text not null check (length(content) > 0),
  created_at timestamptz not null default now(),
  unique (document_id, chunk_index)
);

create index if not exists idx_supplier_document_chunks_document
  on public.supplier_document_chunks (document_id);

-- ---------------------------------------------------------------- 2. hasil negosiasi
create table if not exists public.negotiation_runs (
  id uuid primary key default gen_random_uuid(),
  run_id text not null unique,
  plan_id uuid not null references public.procurement_plans(id) on delete restrict,
  period text not null check (period ~ '^[0-9]{4}-[0-9]{2}$'),
  status text not null check (status in ('completed', 'needs_review', 'no_action', 'failed')),
  po_total numeric(16,2) not null default 0 check (po_total >= 0),
  plan_reserved numeric(16,2) not null default 0 check (plan_reserved >= 0),
  validation jsonb not null default '{}'::jsonb,
  replanned boolean not null default false,
  requested_by uuid references auth.users(id) on delete restrict,
  requested_by_role text not null check (
    requested_by_role in ('super_admin', 'purchasing_staff', 'procurement_manager', 'system_agent')
  ),
  created_at timestamptz not null default now(),
  check (
    (requested_by_role = 'system_agent' and requested_by is null)
    or (requested_by_role <> 'system_agent' and requested_by is not null)
  )
);

create index if not exists idx_negotiation_runs_plan on public.negotiation_runs (plan_id, created_at desc);
create index if not exists idx_negotiation_runs_created on public.negotiation_runs (created_at desc);

create table if not exists public.supplier_comparisons (
  id bigint generated always as identity primary key,
  negotiation_run_id uuid not null references public.negotiation_runs(id) on delete cascade,
  product_id uuid not null references public.products(id) on delete restrict,
  branch_id uuid not null references public.branches(id) on delete restrict,
  order_qty integer not null check (order_qty >= 0),
  offers jsonb not null default '[]'::jsonb,
  valid_supplier_count integer not null default 0 check (valid_supplier_count >= 0),
  selected_supplier_id uuid references public.suppliers(id) on delete restrict,
  selected_contract_id uuid references public.contracts(id) on delete set null,
  selection_status text not null check (selection_status in ('selected', 'needs_review', 'no_offer')),
  citations jsonb not null default '[]'::jsonb,
  reason_trace jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  unique (negotiation_run_id, product_id, branch_id)
);

create index if not exists idx_supplier_comparisons_run on public.supplier_comparisons (negotiation_run_id);
create index if not exists idx_supplier_comparisons_branch on public.supplier_comparisons (branch_id);

-- ---------------------------------------------------------------- 3. draft PO
alter table public.purchase_orders
  add column if not exists po_number text,
  add column if not exists negotiation_run_id uuid references public.negotiation_runs(id) on delete set null,
  add column if not exists plan_id uuid references public.procurement_plans(id) on delete restrict,
  add column if not exists branch_id uuid references public.branches(id) on delete restrict,
  add column if not exists expected_arrival date,
  add column if not exists idempotency_key text,
  add column if not exists draft jsonb not null default '{}'::jsonb;

create unique index if not exists uq_purchase_orders_po_number
  on public.purchase_orders (po_number) where po_number is not null;
create unique index if not exists uq_purchase_orders_idempotency
  on public.purchase_orders (idempotency_key) where idempotency_key is not null;
create index if not exists idx_purchase_orders_plan on public.purchase_orders (plan_id);

do $$
begin
  if not exists (
    select 1 from pg_catalog.pg_constraint
    where conname = 'purchase_orders_status_check'
      and conrelid = 'public.purchase_orders'::regclass
  ) then
    alter table public.purchase_orders
      add constraint purchase_orders_status_check check (
        status in ('draft', 'waiting_approval', 'pending_approval', 'approved',
                   'sent', 'received', 'rejected', 'cancelled')
      );
  end if;
end $$;

alter table public.po_items
  add column if not exists discount_pct numeric(6,3) not null default 0,
  add column if not exists line_total numeric(16,2),
  add column if not exists contract_id uuid references public.contracts(id) on delete set null;

-- ---------------------------------------------------------------- 4. RLS + grants
alter table public.supplier_documents enable row level security;
alter table public.supplier_document_chunks enable row level security;
alter table public.negotiation_runs enable row level security;
alter table public.supplier_comparisons enable row level security;

drop policy if exists "read supplier_documents" on public.supplier_documents;
create policy "read supplier_documents" on public.supplier_documents for select to authenticated using (
  coalesce((select auth.jwt() -> 'app_metadata' ->> 'role'), '') in
    ('super_admin', 'purchasing_staff', 'procurement_manager')
);

drop policy if exists "read supplier_document_chunks" on public.supplier_document_chunks;
create policy "read supplier_document_chunks" on public.supplier_document_chunks for select to authenticated using (
  coalesce((select auth.jwt() -> 'app_metadata' ->> 'role'), '') in
    ('super_admin', 'purchasing_staff', 'procurement_manager')
);

drop policy if exists "read negotiation_runs" on public.negotiation_runs;
create policy "read negotiation_runs" on public.negotiation_runs for select to authenticated using (
  coalesce((select auth.jwt() -> 'app_metadata' ->> 'role'), '') in
    ('super_admin', 'purchasing_staff', 'procurement_manager')
);

drop policy if exists "read supplier_comparisons" on public.supplier_comparisons;
create policy "read supplier_comparisons" on public.supplier_comparisons for select to authenticated using (
  coalesce((select auth.jwt() -> 'app_metadata' ->> 'role'), '') = 'super_admin'
  or (
    coalesce((select auth.jwt() -> 'app_metadata' ->> 'role'), '') in
      ('purchasing_staff', 'procurement_manager')
    and branch_id::text in (
      select jsonb_array_elements_text(
        coalesce((select auth.jwt() -> 'app_metadata' -> 'branch_ids'), '[]'::jsonb)
      )
    )
  )
);

revoke all on table public.supplier_documents, public.supplier_document_chunks,
  public.negotiation_runs, public.supplier_comparisons from anon, authenticated;
grant select on table public.supplier_documents, public.supplier_document_chunks,
  public.negotiation_runs, public.supplier_comparisons to authenticated;
grant all on table public.supplier_documents, public.supplier_document_chunks,
  public.negotiation_runs, public.supplier_comparisons to service_role;
grant usage, select on all sequences in schema public to service_role;

-- Bucket privat untuk PDF kontrak. Tidak ada policy storage untuk anon/authenticated:
-- upload & download hanya lewat Agent Service (service_role).
insert into storage.buckets (id, name, public)
values ('supplier-documents', 'supplier-documents', false)
on conflict (id) do update set public = false;

-- ---------------------------------------------------------------- 5. RPC transaksional
-- Validasi ulang di database (bukan percaya Python):
--   * plan ada, dan PO plan ini belum masuk approval (hanya draft yang boleh diganti)
--   * total tiap PO = jumlah line; qty >= MOQ dan kelipatan pack
--   * qty per SKU-cabang tidak melebihi allocated_qty plan (dibulatkan ke MOQ/pack supplier)
--   * total semua PO <= budget tersedia untuk plan ini (budget recheck, advisory lock periode)
create or replace function public.persist_negotiation(
  p_run jsonb,
  p_comparisons jsonb,
  p_pos jsonb
)
returns uuid
language plpgsql
security invoker
set search_path = ''
as $$
declare
  v_run_id uuid;
  v_plan_id uuid := (p_run->>'plan_id')::uuid;
  v_period text;
  v_period_start date;
  v_total numeric(16,2);
  v_recorded_used numeric(16,2);
  v_po_spent numeric(16,2);
  v_committed numeric(16,2);
  v_other_allocated numeric(16,2);
  v_available numeric(16,2);
  v_po_total numeric(16,2);
  v_requested_by uuid := nullif(p_run->>'requested_by', '')::uuid;
  v_requested_by_role text := p_run->>'requested_by_role';
  v_po jsonb;
  v_po_id uuid;
begin
  if coalesce(p_run->>'run_id', '') = '' then
    raise exception 'run_id is required' using errcode = '23502';
  end if;
  if v_requested_by_role is null or v_requested_by_role not in (
    'super_admin', 'purchasing_staff', 'procurement_manager', 'system_agent'
  ) then
    raise exception 'requester role is not allowed to persist negotiation' using errcode = '42501';
  end if;
  if (v_requested_by_role = 'system_agent') <> (v_requested_by is null) then
    raise exception 'requester identity does not match requester role' using errcode = '42501';
  end if;

  select period into v_period from public.procurement_plans where id = v_plan_id;
  if v_period is null then
    raise exception 'procurement plan not found' using errcode = '23503';
  end if;
  v_period_start := to_date(v_period || '-01', 'YYYY-MM-DD');

  perform pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtext('stockqu-budget'),
    pg_catalog.hashtext(v_period)
  );

  if exists (
    select 1 from public.purchase_orders
    where plan_id = v_plan_id and status <> 'draft'
  ) then
    raise exception 'plan already has purchase orders beyond draft; negotiation is locked'
      using errcode = '55000';
  end if;

  -- Budget recheck (sama dengan RPC P2, reservasi plan ini dikecualikan)
  select greatest(least(coalesce(total_budget, 50000000), 50000000), 0),
         greatest(coalesce(used_budget, 0), 0)
    into v_total, v_recorded_used
  from public.budgets where month = v_period for update;
  if not found then
    v_total := 50000000;
    v_recorded_used := 0;
  end if;

  select coalesce(sum(greatest(coalesce(total_amount, 0), 0)) filter (
           where lower(status) in ('approved', 'sent', 'received')), 0),
         coalesce(sum(greatest(coalesce(total_amount, 0), 0)) filter (
           where lower(status) in ('waiting_approval', 'pending_approval')), 0)
    into v_po_spent, v_committed
  from public.purchase_orders
  where created_at >= (v_period_start::timestamp at time zone 'Asia/Jakarta')
    and created_at < ((v_period_start + interval '1 month')::timestamp at time zone 'Asia/Jakarta');

  select coalesce(sum(amount), 0) into v_other_allocated
  from public.procurement_budget_reservations
  where period = v_period and status = 'active' and plan_id <> v_plan_id;

  v_available := greatest(v_total - greatest(v_recorded_used, v_po_spent) - v_committed - v_other_allocated, 0);

  -- Invariant line & header PO
  if exists (
    select 1
    from jsonb_array_elements(coalesce(p_pos, '[]'::jsonb)) po,
         jsonb_to_recordset(po->'items') as x(qty integer, unit_price numeric, moq integer,
                                              pack_size integer, line_total numeric)
    where x.qty is null or x.qty <= 0
       or x.unit_price is null or x.unit_price <= 0
       or x.moq is null or x.qty < x.moq
       or x.pack_size is null or x.pack_size <= 0 or x.qty % x.pack_size <> 0
       or round(coalesce(x.line_total, -1), 2) <> round(x.qty * x.unit_price, 2)
  ) then
    raise exception 'po line violates quantity, price, MOQ, pack, or line total invariant'
      using errcode = '23514';
  end if;

  if exists (
    select 1
    from jsonb_array_elements(coalesce(p_pos, '[]'::jsonb)) po
    where round((po->>'total_amount')::numeric, 2) <> round((
      select coalesce(sum((x->>'line_total')::numeric), 0) from jsonb_array_elements(po->'items') x
    ), 2)
  ) then
    raise exception 'po total does not match line totals' using errcode = '23514';
  end if;

  -- Qty tidak boleh melebihi alokasi plan lebih dari pembulatan MOQ/pack supplier
  if exists (
    select 1
    from (
      select (x->>'product_id')::uuid as product_id, (po->>'branch_id')::uuid as branch_id,
             sum((x->>'qty')::integer) as qty,
             max(greatest((x->>'moq')::integer, (x->>'pack_size')::integer)) as rounding
      from jsonb_array_elements(coalesce(p_pos, '[]'::jsonb)) po,
           jsonb_array_elements(po->'items') x
      group by 1, 2
    ) q
    left join public.procurement_plan_items pi
      on pi.plan_id = v_plan_id and pi.product_id = q.product_id and pi.branch_id = q.branch_id
    where pi.id is null or pi.allocated_qty <= 0 or q.qty > pi.allocated_qty + q.rounding
  ) then
    raise exception 'po quantity is not backed by procurement plan allocation' using errcode = '23514';
  end if;

  select coalesce(sum((po->>'total_amount')::numeric), 0) into v_po_total
  from jsonb_array_elements(coalesce(p_pos, '[]'::jsonb)) po;
  if round(v_po_total, 2) <> round(coalesce((p_run->>'po_total')::numeric, -1), 2) then
    raise exception 'run po_total does not match purchase orders' using errcode = '23514';
  end if;
  if v_po_total > v_available then
    raise exception 'draft purchase orders exceed authoritative available budget' using errcode = '23514';
  end if;

  -- Simpan. Draft lama plan ini diganti (idempotent untuk rerun Negotiator).
  delete from public.purchase_orders where plan_id = v_plan_id and status = 'draft';

  insert into public.negotiation_runs (
    run_id, plan_id, period, status, po_total, plan_reserved, validation, replanned,
    requested_by, requested_by_role
  ) values (
    p_run->>'run_id', v_plan_id, v_period, p_run->>'status', v_po_total,
    coalesce((select amount from public.procurement_budget_reservations where plan_id = v_plan_id), 0),
    coalesce(p_run->'validation', '{}'::jsonb), coalesce((p_run->>'replanned')::boolean, false),
    v_requested_by, v_requested_by_role
  )
  returning id into v_run_id;

  insert into public.supplier_comparisons (
    negotiation_run_id, product_id, branch_id, order_qty, offers, valid_supplier_count,
    selected_supplier_id, selected_contract_id, selection_status, citations, reason_trace
  )
  select v_run_id, x.product_id, x.branch_id, x.order_qty, coalesce(x.offers, '[]'::jsonb),
         x.valid_supplier_count, x.selected_supplier_id, x.selected_contract_id,
         x.selection_status, coalesce(x.citations, '[]'::jsonb), coalesce(x.reason_trace, '{}'::jsonb)
  from jsonb_to_recordset(coalesce(p_comparisons, '[]'::jsonb)) as x(
    product_id uuid, branch_id uuid, order_qty integer, offers jsonb, valid_supplier_count integer,
    selected_supplier_id uuid, selected_contract_id uuid, selection_status text,
    citations jsonb, reason_trace jsonb
  );

  for v_po in select * from jsonb_array_elements(coalesce(p_pos, '[]'::jsonb)) loop
    insert into public.purchase_orders (
      supplier_id, total_amount, status, po_number, negotiation_run_id, plan_id, branch_id,
      expected_arrival, idempotency_key, draft
    ) values (
      (v_po->>'supplier_id')::uuid, (v_po->>'total_amount')::numeric, 'draft', v_po->>'po_number',
      v_run_id, v_plan_id, (v_po->>'branch_id')::uuid, (v_po->>'expected_arrival')::date,
      v_po->>'idempotency_key', coalesce(v_po->'draft', '{}'::jsonb)
    )
    returning id into v_po_id;

    insert into public.po_items (po_id, product_id, qty, unit_price, discount_pct, line_total, contract_id)
    select v_po_id, x.product_id, x.qty, x.unit_price, coalesce(x.discount_pct, 0), x.line_total, x.contract_id
    from jsonb_to_recordset(v_po->'items') as x(
      product_id uuid, qty integer, unit_price numeric, discount_pct numeric,
      line_total numeric, contract_id uuid
    );
  end loop;

  -- Reservasi plan mengikuti total draft PO (selisih dari harga referensi dilepas).
  update public.procurement_budget_reservations
  set amount = v_po_total,
      status = case when v_po_total > 0 then 'active' else 'released' end,
      updated_at = now()
  where plan_id = v_plan_id;

  return v_run_id;
end;
$$;

revoke execute on function public.persist_negotiation(jsonb, jsonb, jsonb) from public, anon, authenticated;
grant execute on function public.persist_negotiation(jsonb, jsonb, jsonb) to service_role;

commit;
