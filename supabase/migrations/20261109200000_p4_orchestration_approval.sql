-- StockQu - P4 Orkestrasi & Aksi Nyata
-- Jalankan SETELAH 20261109100000_p3_negotiator.sql.
--
-- State machine PO (roadmap P4-20, SRS-F-44, NF-03, NF-24):
--   draft --request_po_approval--> waiting_approval --decide_approval(OK)--> approved --mark_po_sent--> sent
--                                        |--(TOLAK)--> rejected
--                                        |--(REVISI / kedaluwarsa)--> draft
-- Human-in-the-loop dijaga DI DATABASE: mark_po_sent menolak PO yang belum approved,
-- sehingga email PO dan update stok tidak bisa terjadi tanpa OK manager.
-- Barang PO terkirim dicatat sebagai incoming (in transit), BUKAN menambah stok fisik.

begin;

-- ---------------------------------------------------------------- 1. job harian (anti duplikat)
create table if not exists public.workflow_jobs (
  id uuid primary key default gen_random_uuid(),
  job_key text not null unique,
  trigger text not null check (trigger in ('cron', 'manual')),
  run_id text not null unique,
  status text not null check (status in (
    'running', 'waiting_approval', 'completed', 'no_action', 'failed', 'rejected', 'revision_requested', 'expired'
  )),
  analyst_run_id text,
  purchaser_run_id text,
  negotiator_run_id text,
  approval_code text,
  summary jsonb not null default '{}'::jsonb,
  error_message text,
  requested_by text not null,
  started_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  finished_at timestamptz
);

create index if not exists idx_workflow_jobs_started on public.workflow_jobs (started_at desc);
create unique index if not exists uq_workflow_jobs_one_running
  on public.workflow_jobs ((true)) where status = 'running';

-- ---------------------------------------------------------------- 2. approval request
create table if not exists public.approval_requests (
  id uuid primary key default gen_random_uuid(),
  approval_code text not null unique check (approval_code ~ '^APR-[0-9]{8}-[0-9]{2,}$'),
  workflow_run_id text not null,
  negotiation_run_id uuid not null unique references public.negotiation_runs(id) on delete restrict,
  plan_id uuid not null references public.procurement_plans(id) on delete restrict,
  po_ids uuid[] not null,
  total_amount numeric(16,2) not null check (total_amount > 0),
  item_count integer not null check (item_count > 0),
  most_urgent text not null,
  message text not null,
  status text not null default 'pending' check (status in (
    'pending', 'approved', 'rejected', 'revision_requested', 'expired'
  )),
  notification_status text not null default 'pending' check (notification_status in (
    'pending', 'sent', 'failed', 'skipped'
  )),
  expires_at timestamptz not null,
  decided_at timestamptz,
  decided_via text check (decided_via in ('whatsapp', 'dashboard')),
  decided_by text,
  decision_ref text unique,
  decision_note text,
  created_at timestamptz not null default now()
);

create index if not exists idx_approval_requests_status on public.approval_requests (status, created_at desc);

-- ---------------------------------------------------------------- 3. kolom lifecycle PO + audit transisi
alter table public.purchase_orders
  add column if not exists approval_code text,
  add column if not exists approved_at timestamptz,
  add column if not exists sent_at timestamptz,
  add column if not exists email_message_id text,
  add column if not exists status_updated_at timestamptz not null default now();

create table if not exists public.po_status_history (
  id bigint generated always as identity primary key,
  po_id uuid not null references public.purchase_orders(id) on delete cascade,
  from_status text not null,
  to_status text not null,
  actor text not null,
  reason text,
  created_at timestamptz not null default now()
);

create index if not exists idx_po_status_history_po on public.po_status_history (po_id, created_at);

-- ---------------------------------------------------------------- 4. incoming stock (SRS-F-07 ledger, F-44)
alter table public.inventory
  add column if not exists incoming_qty numeric not null default 0 check (incoming_qty >= 0);

create table if not exists public.inventory_transactions (
  id bigint generated always as identity primary key,
  product_id uuid not null references public.products(id) on delete restrict,
  branch_id uuid not null references public.branches(id) on delete restrict,
  po_id uuid not null references public.purchase_orders(id) on delete restrict,
  txn_type text not null check (txn_type in ('incoming_po')),
  qty numeric not null check (qty > 0),
  created_at timestamptz not null default now(),
  unique (po_id, product_id, txn_type)
);

create index if not exists idx_inventory_transactions_branch
  on public.inventory_transactions (branch_id, product_id, created_at desc);

-- Email supplier untuk Gmail (SRS-F-43). Tidak diisi otomatis: kosong = DATA TIDAK DITEMUKAN,
-- PO tetap approved dan tidak dikirim sampai email diisi admin.
alter table public.suppliers add column if not exists email text
  check (email is null or email ~* '^[^@\s]+@[^@\s]+\.[^@\s]+$');

-- Checkpoint LangGraph (PostgresSaver) di schema terpisah, tidak diekspos ke Data API.
create schema if not exists langgraph;
revoke all on schema langgraph from public, anon, authenticated;

-- ---------------------------------------------------------------- 5. RLS + grants
alter table public.workflow_jobs enable row level security;
alter table public.approval_requests enable row level security;
alter table public.po_status_history enable row level security;
alter table public.inventory_transactions enable row level security;

drop policy if exists "read workflow_jobs" on public.workflow_jobs;
create policy "read workflow_jobs" on public.workflow_jobs for select to authenticated using (
  coalesce((select auth.jwt() -> 'app_metadata' ->> 'role'), '') in
    ('super_admin', 'warehouse_manager', 'purchasing_staff', 'procurement_manager')
);

drop policy if exists "read approval_requests" on public.approval_requests;
create policy "read approval_requests" on public.approval_requests for select to authenticated using (
  coalesce((select auth.jwt() -> 'app_metadata' ->> 'role'), '') in
    ('super_admin', 'warehouse_manager', 'purchasing_staff', 'procurement_manager')
);

drop policy if exists "read po_status_history" on public.po_status_history;
create policy "read po_status_history" on public.po_status_history for select to authenticated using (
  coalesce((select auth.jwt() -> 'app_metadata' ->> 'role'), '') in
    ('super_admin', 'warehouse_manager', 'purchasing_staff', 'procurement_manager')
);

drop policy if exists "read inventory_transactions" on public.inventory_transactions;
create policy "read inventory_transactions" on public.inventory_transactions for select to authenticated using (
  coalesce((select auth.jwt() -> 'app_metadata' ->> 'role'), '') = 'super_admin'
  or (
    coalesce((select auth.jwt() -> 'app_metadata' ->> 'role'), '') in
      ('warehouse_manager', 'purchasing_staff', 'procurement_manager')
    and branch_id::text in (
      select jsonb_array_elements_text(
        coalesce((select auth.jwt() -> 'app_metadata' -> 'branch_ids'), '[]'::jsonb)
      )
    )
  )
);

revoke all on table public.workflow_jobs, public.approval_requests, public.po_status_history,
  public.inventory_transactions from anon, authenticated;
grant select on table public.workflow_jobs, public.approval_requests, public.po_status_history,
  public.inventory_transactions to authenticated;
grant all on table public.workflow_jobs, public.approval_requests, public.po_status_history,
  public.inventory_transactions to service_role;
grant usage, select on all sequences in schema public to service_role;

-- ---------------------------------------------------------------- 6. RPC: draft -> waiting_approval
-- Budget recheck terakhir sebelum approval + kode APR-YYYYMMDD-NN unik + reservasi plan
-- dikonversi menjadi committed PO (tidak dihitung dua kali). Idempotent per negotiation run.
create or replace function public.request_po_approval(p_request jsonb)
returns jsonb
language plpgsql
security invoker
set search_path = ''
as $$
declare
  v_neg_id uuid := (p_request->>'negotiation_run_id')::uuid;
  v_existing public.approval_requests%rowtype;
  v_plan_id uuid;
  v_period text;
  v_period_start date;
  v_total numeric(16,2);
  v_recorded_used numeric(16,2);
  v_po_spent numeric(16,2);
  v_committed numeric(16,2);
  v_other_allocated numeric(16,2);
  v_available numeric(16,2);
  v_po_total numeric(16,2);
  v_po_ids uuid[];
  v_day text := to_char(now() at time zone 'Asia/Jakarta', 'YYYYMMDD');
  v_seq integer;
  v_code text;
  v_actor text := coalesce(p_request->>'actor', 'system:unknown');
  v_ttl_hours integer := greatest(coalesce((p_request->>'ttl_hours')::integer, 24), 1);
begin
  select * into v_existing from public.approval_requests where negotiation_run_id = v_neg_id;
  if found then
    return jsonb_build_object('approval_code', v_existing.approval_code, 'status', v_existing.status,
                              'po_ids', to_jsonb(v_existing.po_ids), 'total_amount', v_existing.total_amount,
                              'message', v_existing.message, 'replay', true);
  end if;

  select plan_id, period into v_plan_id, v_period from public.negotiation_runs where id = v_neg_id;
  if v_plan_id is null then
    raise exception 'negotiation run not found' using errcode = '23503';
  end if;
  v_period_start := to_date(v_period || '-01', 'YYYY-MM-DD');

  perform pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtext('stockqu-budget'), pg_catalog.hashtext(v_period));
  perform pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtext('stockqu-approval'), pg_catalog.hashtext(v_day));

  select array_agg(id order by po_number), coalesce(sum(total_amount), 0)
    into v_po_ids, v_po_total
  from public.purchase_orders
  where negotiation_run_id = v_neg_id and status = 'draft';
  if v_po_ids is null or v_po_total <= 0 then
    raise exception 'no draft purchase orders to approve' using errcode = '55000';
  end if;

  select greatest(least(coalesce(total_budget, 50000000), 50000000), 0), greatest(coalesce(used_budget, 0), 0)
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
  if v_po_total > v_available then
    raise exception 'purchase orders exceed authoritative available budget' using errcode = '23514';
  end if;

  select count(*) + 1 into v_seq from public.approval_requests where approval_code like 'APR-' || v_day || '-%';
  v_code := 'APR-' || v_day || '-' || lpad(v_seq::text, 2, '0');

  update public.purchase_orders
  set status = 'waiting_approval', approval_code = v_code, status_updated_at = now()
  where id = any(v_po_ids);
  insert into public.po_status_history (po_id, from_status, to_status, actor, reason)
  select unnest(v_po_ids), 'draft', 'waiting_approval', v_actor, 'request approval ' || v_code;

  update public.procurement_budget_reservations
  set status = 'converted', updated_at = now()
  where plan_id = v_plan_id;

  insert into public.approval_requests (
    approval_code, workflow_run_id, negotiation_run_id, plan_id, po_ids, total_amount, item_count,
    most_urgent, message, expires_at
  ) values (
    v_code, p_request->>'workflow_run_id', v_neg_id, v_plan_id, v_po_ids, v_po_total,
    greatest(coalesce((p_request->>'item_count')::integer, 1), 1),
    coalesce(p_request->>'most_urgent', '-'),
    replace(coalesce(p_request->>'message_template', ''), '{{APPROVAL_CODE}}', v_code),
    now() + make_interval(hours => v_ttl_hours)
  );

  return jsonb_build_object('approval_code', v_code, 'status', 'pending', 'po_ids', to_jsonb(v_po_ids),
                            'total_amount', v_po_total,
                            'message', replace(coalesce(p_request->>'message_template', ''), '{{APPROVAL_CODE}}', v_code),
                            'replay', false);
end;
$$;

-- ---------------------------------------------------------------- 7. RPC: keputusan manager
-- decision_ref (Twilio MessageSid / nonce dashboard) unik -> replay webhook tidak memproses ulang.
create or replace function public.decide_approval(p_decision jsonb)
returns jsonb
language plpgsql
security invoker
set search_path = ''
as $$
declare
  v_req public.approval_requests%rowtype;
  v_decision text := p_decision->>'decision';
  v_ref text := p_decision->>'decision_ref';
  v_via text := p_decision->>'via';
  v_by text := coalesce(p_decision->>'decided_by', 'unknown');
  v_role text := coalesce(p_decision->>'approver_role', 'unknown');
  v_to text;
  v_label text;
begin
  if v_decision not in ('approved', 'rejected', 'revision_requested') then
    raise exception 'invalid decision' using errcode = '22023';
  end if;
  if v_via not in ('whatsapp', 'dashboard') or coalesce(v_ref, '') = '' then
    raise exception 'decision channel and reference are required' using errcode = '22023';
  end if;

  select * into v_req from public.approval_requests where decision_ref = v_ref;
  if found then
    return jsonb_build_object('approval_code', v_req.approval_code, 'status', v_req.status,
                              'po_ids', to_jsonb(v_req.po_ids), 'replay', true);
  end if;

  select * into v_req from public.approval_requests where approval_code = p_decision->>'approval_code' for update;
  if not found then
    raise exception 'approval code not found' using errcode = '23503';
  end if;
  if v_req.status <> 'pending' then
    raise exception 'approval already decided' using errcode = '55000';
  end if;

  if v_req.expires_at < now() then
    update public.purchase_orders set status = 'draft', status_updated_at = now()
    where id = any(v_req.po_ids) and status = 'waiting_approval';
    insert into public.po_status_history (po_id, from_status, to_status, actor, reason)
    select unnest(v_req.po_ids), 'waiting_approval', 'draft', 'system:expiry', 'approval expired ' || v_req.approval_code;
    update public.procurement_budget_reservations set status = 'active', updated_at = now()
    where plan_id = v_req.plan_id;
    update public.approval_requests set status = 'expired', decided_at = now() where id = v_req.id;
    return jsonb_build_object('approval_code', v_req.approval_code, 'status', 'expired',
                              'po_ids', to_jsonb(v_req.po_ids), 'replay', false);
  end if;

  v_to := case v_decision when 'approved' then 'approved' when 'rejected' then 'rejected' else 'draft' end;
  v_label := case v_decision when 'approved' then 'OK' when 'rejected' then 'REJECT' else 'REVISION' end;

  if exists (select 1 from public.purchase_orders where id = any(v_req.po_ids) and status <> 'waiting_approval') then
    raise exception 'purchase order state changed outside approval' using errcode = '55000';
  end if;

  update public.purchase_orders
  set status = v_to, status_updated_at = now(),
      approved_at = case when v_to = 'approved' then now() else approved_at end
  where id = any(v_req.po_ids);
  insert into public.po_status_history (po_id, from_status, to_status, actor, reason)
  select unnest(v_req.po_ids), 'waiting_approval', v_to, v_via || ':' || v_by, v_label || ' ' || v_req.approval_code;
  insert into public.approvals (po_id, approver_role, decision, via)
  select unnest(v_req.po_ids), v_role, v_label, case v_via when 'whatsapp' then 'WhatsApp' else 'Dashboard' end;

  update public.procurement_budget_reservations
  set status = case v_decision when 'revision_requested' then 'active' else 'released' end, updated_at = now()
  where plan_id = v_req.plan_id;

  update public.approval_requests
  set status = v_decision, decided_at = now(), decided_via = v_via, decided_by = v_by,
      decision_ref = v_ref, decision_note = left(p_decision->>'note', 500)
  where id = v_req.id;

  return jsonb_build_object('approval_code', v_req.approval_code, 'status', v_decision,
                            'po_ids', to_jsonb(v_req.po_ids), 'replay', false);
end;
$$;

-- ---------------------------------------------------------------- 8. RPC: approved -> sent + incoming stock
create or replace function public.mark_po_sent(p_po_id uuid, p_email_ref text, p_actor text)
returns jsonb
language plpgsql
security invoker
set search_path = ''
as $$
declare
  v_po public.purchase_orders%rowtype;
  v_items jsonb;
begin
  select * into v_po from public.purchase_orders where id = p_po_id for update;
  if not found then
    raise exception 'purchase order not found' using errcode = '23503';
  end if;
  if v_po.status = 'sent' then
    return jsonb_build_object('po_id', v_po.id, 'po_number', v_po.po_number, 'status', 'sent',
                              'replay', true, 'incoming', '[]'::jsonb);
  end if;
  if v_po.status <> 'approved' then
    raise exception 'purchase order is not approved; email and stock update are blocked' using errcode = '42501';
  end if;
  if v_po.branch_id is null then
    raise exception 'purchase order has no destination branch' using errcode = '23502';
  end if;

  update public.purchase_orders
  set status = 'sent', sent_at = now(), email_message_id = left(p_email_ref, 200), status_updated_at = now()
  where id = v_po.id;
  insert into public.po_status_history (po_id, from_status, to_status, actor, reason)
  values (v_po.id, 'approved', 'sent', coalesce(p_actor, 'system:unknown'), 'email ' || coalesce(p_email_ref, '-'));

  with inserted as (
    insert into public.inventory_transactions (product_id, branch_id, po_id, txn_type, qty)
    select i.product_id, v_po.branch_id, v_po.id, 'incoming_po', sum(i.qty)
    from public.po_items i where i.po_id = v_po.id
    group by i.product_id
    on conflict (po_id, product_id, txn_type) do nothing
    returning product_id, branch_id, qty
  ), bumped as (
    insert into public.inventory (product_id, branch_id, stock_qty, incoming_qty, updated_at)
    select product_id, branch_id, 0, qty, now() from inserted
    on conflict (product_id, branch_id) do update
      set incoming_qty = public.inventory.incoming_qty + excluded.incoming_qty
    returning product_id, branch_id, incoming_qty
  )
  select coalesce(jsonb_agg(jsonb_build_object('product_id', product_id, 'branch_id', branch_id,
                                               'incoming_qty', incoming_qty)), '[]'::jsonb)
    into v_items from bumped;

  return jsonb_build_object('po_id', v_po.id, 'po_number', v_po.po_number, 'status', 'sent',
                            'replay', false, 'incoming', v_items);
end;
$$;

revoke execute on function public.request_po_approval(jsonb) from public, anon, authenticated;
revoke execute on function public.decide_approval(jsonb) from public, anon, authenticated;
revoke execute on function public.mark_po_sent(uuid, text, text) from public, anon, authenticated;
grant execute on function public.request_po_approval(jsonb) to service_role;
grant execute on function public.decide_approval(jsonb) to service_role;
grant execute on function public.mark_po_sent(uuid, text, text) to service_role;

commit;
