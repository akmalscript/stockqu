-- StockQu - Gmail delivery outbox for approved purchase orders
-- Prevent duplicate sends and keep the provider reference auditable.

alter table public.suppliers
  add column if not exists email text;

do $$
begin
  if not exists (
    select 1
    from pg_catalog.pg_constraint
    where conname = 'suppliers_email_format_check'
      and conrelid = 'public.suppliers'::regclass
  ) then
    alter table public.suppliers
      add constraint suppliers_email_format_check
      check (
        email is null
        or (
          pg_catalog.length(email) <= 254
          and pg_catalog.strpos(email, pg_catalog.chr(13)) = 0
          and pg_catalog.strpos(email, pg_catalog.chr(10)) = 0
          and email ~* '^[A-Z0-9.!#$%&''*+/=?^_`{|}~-]+@[A-Z0-9.-]+[.][A-Z]{2,63}$'
        )
      );
  end if;
end
$$;

create table if not exists public.po_email_deliveries (
  id uuid primary key default gen_random_uuid(),
  po_id uuid not null unique
    references public.purchase_orders(id) on delete restrict,
  recipient_email text not null,
  status text not null default 'pending'
    check (status in ('pending', 'sending', 'sent', 'failed')),
  attempt_count smallint not null default 0
    check (attempt_count between 0 and 3),
  n8n_execution_id text,
  provider_message_id text,
  last_error text,
  claimed_at timestamptz,
  sent_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  check (recipient_email !~ E'[\\r\\n]'),
  check ((status = 'sent') = (sent_at is not null)),
  check (provider_message_id is null or status = 'sent')
);

create unique index if not exists po_email_deliveries_provider_message_id_uidx
  on public.po_email_deliveries (provider_message_id)
  where provider_message_id is not null;

create index if not exists po_email_deliveries_status_updated_idx
  on public.po_email_deliveries (status, updated_at);

alter table public.po_email_deliveries enable row level security;

revoke all on table public.po_email_deliveries from anon, authenticated;
grant select, insert, update on table public.po_email_deliveries to service_role;

-- Claim is atomic: only an approved PO whose latest WhatsApp decision is OK
-- can enter the sending state. A failed delivery may be claimed again up to
-- three times; sending/sent rows are never claimed again.
create or replace function public.claim_approved_po_email_delivery(
  p_po_id uuid,
  p_n8n_execution_id text,
  p_recipient_email text
)
returns jsonb
language plpgsql
security invoker
set search_path = ''
as $$
declare
  v_po record;
  v_delivery record;
  v_existing_claim boolean := false;
  v_item_count integer;
  v_items_total numeric(16,2);
  v_items jsonb;
  v_recipient_email text;
begin
  if pg_catalog.btrim(coalesce(p_n8n_execution_id, '')) = '' then
    raise exception 'n8n execution id is required' using errcode = '23502';
  end if;

  select d.*, po.po_number
  into v_delivery
  from public.po_email_deliveries d
  join public.purchase_orders po on po.id = d.po_id
  where d.po_id = p_po_id;

  if found then
    if v_delivery.status = 'sent' then
      return jsonb_build_object(
        'claimed', false,
        'reason', 'ALREADY_SENT',
        'po_id', p_po_id,
        'po_number', v_delivery.po_number,
        'recipient_email', v_delivery.recipient_email,
        'provider_message_id', v_delivery.provider_message_id,
        'delivery_status', v_delivery.status,
        'attempt_count', v_delivery.attempt_count
      );
    end if;
    if v_delivery.status = 'sending'
      and v_delivery.n8n_execution_id <> p_n8n_execution_id then
      return jsonb_build_object(
        'claimed', false,
        'reason', 'ALREADY_CLAIMED',
        'delivery_status', v_delivery.status,
        'attempt_count', v_delivery.attempt_count
      );
    end if;
    if v_delivery.status = 'failed' and v_delivery.attempt_count >= 3 then
      return jsonb_build_object(
        'claimed', false,
        'reason', 'RETRY_LIMIT_REACHED',
        'delivery_status', v_delivery.status,
        'attempt_count', v_delivery.attempt_count
      );
    end if;
  end if;

  select
    po.po_number,
    po.total_amount,
    s.name as supplier_name,
    s.email as recipient_email,
    greatest(coalesce(s.lead_time_days, 0), 0) as lead_time_days
  into v_po
  from public.purchase_orders po
  join public.suppliers s on s.id = po.supplier_id
  join lateral (
    select a.decision, a.via
    from public.approvals a
    where a.po_id = po.id
    order by a.created_at desc, a.id desc
    limit 1
  ) latest_approval on true
  where po.id = p_po_id
    and pg_catalog.lower(po.status) = 'approved'
    and pg_catalog.upper(pg_catalog.btrim(latest_approval.decision)) = 'OK'
    and pg_catalog.lower(pg_catalog.btrim(latest_approval.via)) = 'whatsapp';

  if not found then
    return jsonb_build_object(
      'claimed', false,
      'reason', 'PO_NOT_APPROVED'
    );
  end if;

  if pg_catalog.btrim(coalesce(v_po.po_number, '')) = '' then
    return jsonb_build_object(
      'claimed', false,
      'reason', 'PO_NUMBER_MISSING'
    );
  end if;

  -- Alamat tujuan selalu berasal dari master supplier. Parameter dipertahankan
  -- untuk kompatibilitas RPC, tetapi tidak boleh mengalihkan PO production.
  v_recipient_email := pg_catalog.lower(
    pg_catalog.btrim(coalesce(v_po.recipient_email, ''))
  );

  if v_recipient_email = '' then
    return jsonb_build_object(
      'claimed', false,
      'reason', 'SUPPLIER_EMAIL_MISSING'
    );
  end if;

  if pg_catalog.length(v_recipient_email) > 254
    or pg_catalog.strpos(v_recipient_email, pg_catalog.chr(13)) > 0
    or pg_catalog.strpos(v_recipient_email, pg_catalog.chr(10)) > 0
    or v_recipient_email !~* '^[A-Z0-9.!#$%&''*+/=?^_`{|}~-]+@[A-Z0-9.-]+[.][A-Z]{2,63}$'
  then
    return jsonb_build_object(
      'claimed', false,
      'reason', 'RECIPIENT_EMAIL_INVALID'
    );
  end if;

  select
    count(*)::integer,
    coalesce(sum(pi.qty * pi.unit_price), 0)::numeric(16,2),
    coalesce(
      jsonb_agg(
        jsonb_build_object(
          'sku', p.sku,
          'name', p.name,
          'qty', pi.qty,
          'unit_price', pi.unit_price,
          'line_total', pi.qty * pi.unit_price
        )
        order by p.sku, pi.id
      ),
      '[]'::jsonb
    )
  into v_item_count, v_items_total, v_items
  from public.po_items pi
  join public.products p on p.id = pi.product_id
  where pi.po_id = p_po_id;

  if v_item_count = 0 then
    return jsonb_build_object(
      'claimed', false,
      'reason', 'PO_ITEMS_MISSING'
    );
  end if;

  if v_items_total <> coalesce(v_po.total_amount, 0)::numeric(16,2) then
    return jsonb_build_object(
      'claimed', false,
      'reason', 'PO_TOTAL_MISMATCH'
    );
  end if;

  insert into public.po_email_deliveries (
    po_id,
    recipient_email,
    status,
    attempt_count,
    n8n_execution_id,
    claimed_at,
    updated_at
  ) values (
    p_po_id,
    v_recipient_email,
    'sending',
    1,
    p_n8n_execution_id,
    now(),
    now()
  )
  on conflict (po_id) do update
    set recipient_email = excluded.recipient_email,
        status = 'sending',
        attempt_count = public.po_email_deliveries.attempt_count + 1,
        n8n_execution_id = excluded.n8n_execution_id,
        provider_message_id = null,
        last_error = null,
        claimed_at = now(),
        sent_at = null,
        updated_at = now()
  where public.po_email_deliveries.status = 'failed'
    and public.po_email_deliveries.attempt_count < 3
  returning * into v_delivery;

  if not found then
    select *
    into v_delivery
    from public.po_email_deliveries
    where po_id = p_po_id;

    if v_delivery.status = 'sending'
      and v_delivery.n8n_execution_id = p_n8n_execution_id then
      -- A network retry from the same n8n execution reuses its claim.
      v_existing_claim := true;
    else
      return jsonb_build_object(
        'claimed', false,
        'reason', case
          when v_delivery.status = 'sent' then 'ALREADY_SENT'
          when v_delivery.status = 'sending' then 'ALREADY_CLAIMED'
          when v_delivery.status = 'failed' and v_delivery.attempt_count >= 3
            then 'RETRY_LIMIT_REACHED'
          else 'DELIVERY_NOT_CLAIMED'
        end,
        'delivery_status', v_delivery.status,
        'attempt_count', v_delivery.attempt_count
      );
    end if;
  end if;

  if not v_existing_claim then
    insert into public.agent_logs (
      agent_name,
      run_id,
      tool_name,
      status,
      input_summary,
      output_summary,
      actor_id,
      actor_role
    ) values (
      'n8n',
      p_po_id::text,
      'gmail_send_po',
      'started',
      'Approved PO email delivery claimed',
      'Waiting for Gmail provider response',
      'system:n8n:' || p_n8n_execution_id,
      'system_agent'
    );
  end if;

  return jsonb_build_object(
    'claimed', true,
    'delivery_id', v_delivery.id,
    'po_id', p_po_id,
    'recipient_email', v_delivery.recipient_email,
    'po_number', v_po.po_number,
    'supplier_name', v_po.supplier_name,
    'total_amount', v_items_total,
    'requested_delivery_date', current_date + v_po.lead_time_days,
    'items', v_items,
    'attempt_count', v_delivery.attempt_count
  );
end
$$;

create or replace function public.complete_po_email_delivery(
  p_po_id uuid,
  p_n8n_execution_id text,
  p_provider_message_id text
)
returns jsonb
language plpgsql
security invoker
set search_path = ''
as $$
declare
  v_delivery public.po_email_deliveries%rowtype;
  v_po_number text;
begin
  if pg_catalog.btrim(coalesce(p_provider_message_id, '')) = '' then
    raise exception 'provider message id is required' using errcode = '23502';
  end if;

  select *
  into v_delivery
  from public.po_email_deliveries
  where po_id = p_po_id
  for update;

  if not found then
    raise exception 'email delivery claim is missing' using errcode = '55000';
  end if;

  select po_number
  into v_po_number
  from public.purchase_orders
  where id = p_po_id;

  if v_delivery.status = 'sent' then
    if v_delivery.provider_message_id = pg_catalog.btrim(p_provider_message_id) then
      return jsonb_build_object(
        'sent', true,
        'replay', true,
        'po_id', p_po_id,
        'po_number', v_po_number,
        'recipient_email', v_delivery.recipient_email,
        'provider_message_id', v_delivery.provider_message_id
      );
    end if;
    raise exception 'email delivery was completed with a different provider message id'
      using errcode = '55000';
  end if;

  if v_delivery.status <> 'sending'
    or v_delivery.n8n_execution_id <> p_n8n_execution_id then
    raise exception 'email delivery claim belongs to another execution'
      using errcode = '55000';
  end if;

  -- mark_po_sent is the authoritative P4 state transition. It also writes
  -- po_status_history and branch-specific incoming stock atomically.
  perform public.mark_po_sent(
    p_po_id,
    pg_catalog.btrim(p_provider_message_id),
    'system:n8n:' || p_n8n_execution_id
  );

  update public.po_email_deliveries
  set status = 'sent',
      provider_message_id = pg_catalog.btrim(p_provider_message_id),
      last_error = null,
      sent_at = now(),
      updated_at = now()
  where po_id = p_po_id
    and status = 'sending'
    and n8n_execution_id = p_n8n_execution_id
  returning * into v_delivery;

  if not found then
    raise exception 'email delivery claim is missing or belongs to another execution'
      using errcode = '55000';
  end if;

  insert into public.agent_logs (
    agent_name,
    run_id,
    tool_name,
    status,
    input_summary,
    output_summary,
    actor_id,
    actor_role
  ) values (
    'n8n',
    p_po_id::text,
    'gmail_send_po',
    'success',
    'Approved PO email delivery',
    'Gmail accepted the message for ' || v_delivery.recipient_email,
    'system:n8n:' || p_n8n_execution_id,
    'system_agent'
  );

  return jsonb_build_object(
    'sent', true,
    'replay', false,
    'po_id', p_po_id,
    'po_number', v_po_number,
    'recipient_email', v_delivery.recipient_email,
    'provider_message_id', v_delivery.provider_message_id
  );
end
$$;

create or replace function public.fail_po_email_delivery(
  p_po_id uuid,
  p_n8n_execution_id text,
  p_error_code text
)
returns jsonb
language plpgsql
security invoker
set search_path = ''
as $$
declare
  v_delivery public.po_email_deliveries%rowtype;
begin
  select *
  into v_delivery
  from public.po_email_deliveries
  where po_id = p_po_id
  for update;

  if not found then
    raise exception 'email delivery claim is missing' using errcode = '55000';
  end if;

  if v_delivery.status = 'failed'
    and v_delivery.n8n_execution_id = p_n8n_execution_id then
    return jsonb_build_object(
      'failed', true,
      'replay', true,
      'po_id', p_po_id,
      'attempt_count', v_delivery.attempt_count,
      'retry_allowed', v_delivery.attempt_count < 3
    );
  end if;

  update public.po_email_deliveries
  set status = 'failed',
      provider_message_id = null,
      last_error = pg_catalog.left(
        coalesce(nullif(pg_catalog.btrim(p_error_code), ''), 'GMAIL_SEND_FAILED'),
        200
      ),
      sent_at = null,
      updated_at = now()
  where po_id = p_po_id
    and status = 'sending'
    and n8n_execution_id = p_n8n_execution_id
  returning * into v_delivery;

  if not found then
    raise exception 'email delivery claim is missing or belongs to another execution'
      using errcode = '55000';
  end if;

  insert into public.agent_logs (
    agent_name,
    run_id,
    tool_name,
    status,
    input_summary,
    output_summary,
    error_message,
    actor_id,
    actor_role
  ) values (
    'n8n',
    p_po_id::text,
    'gmail_send_po',
    'failed',
    'Approved PO email delivery',
    'Gmail did not accept the message',
    pg_catalog.left(
      coalesce(nullif(pg_catalog.btrim(p_error_code), ''), 'GMAIL_SEND_FAILED'),
      200
    ),
    'system:n8n:' || p_n8n_execution_id,
    'system_agent'
  );

  return jsonb_build_object(
    'failed', true,
    'replay', false,
    'po_id', p_po_id,
    'attempt_count', v_delivery.attempt_count,
    'retry_allowed', v_delivery.attempt_count < 3
  );
end
$$;

revoke all on function public.claim_approved_po_email_delivery(uuid, text, text)
  from public, anon, authenticated;
revoke all on function public.complete_po_email_delivery(uuid, text, text)
  from public, anon, authenticated;
revoke all on function public.fail_po_email_delivery(uuid, text, text)
  from public, anon, authenticated;

grant execute on function public.claim_approved_po_email_delivery(uuid, text, text)
  to service_role;
grant execute on function public.complete_po_email_delivery(uuid, text, text)
  to service_role;
grant execute on function public.fail_po_email_delivery(uuid, text, text)
  to service_role;
