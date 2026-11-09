-- Run after migrations inside a transaction. The caller must roll it back.
do $$
declare
  v_result jsonb;
  v_supplier_id uuid := '00000000-0000-0000-0000-000000009101';
  v_product_id uuid := '00000000-0000-0000-0000-000000009102';
  v_po_id uuid := '00000000-0000-0000-0000-000000009103';
  v_branch_id uuid := '00000000-0000-0000-0000-000000009104';
begin
  insert into public.branches (id, name, city)
  values (v_branch_id, 'Branch Gmail Test', 'Test');

  insert into public.suppliers (id, name, email, lead_time_days)
  values (v_supplier_id, 'Supplier Test n8n', 'supplier.test@example.com', 3);

  insert into public.products (id, sku, name)
  values (v_product_id, 'TEST-N8N-GMAIL', 'Item Test n8n');

  insert into public.inventory (product_id, branch_id, stock_qty, incoming_qty)
  values (v_product_id, v_branch_id, 0, 0);

  insert into public.purchase_orders (
    id, supplier_id, branch_id, po_number, total_amount, status
  ) values (
    v_po_id, v_supplier_id, v_branch_id, 'PO-N8N-GMAIL-TEST', 20000, 'draft'
  );

  insert into public.po_items (po_id, product_id, qty, unit_price)
  values (v_po_id, v_product_id, 2, 10000);

  select public.claim_approved_po_email_delivery(
    v_po_id, 'exec-1', 'internal.test@example.com'
  )
  into v_result;
  assert v_result->>'reason' = 'PO_NOT_APPROVED',
    'a draft PO must never be claimed for Gmail';

  update public.purchase_orders set status = 'approved' where id = v_po_id;
  insert into public.approvals (po_id, approver_role, decision, via)
  values (v_po_id, 'procurement_manager', 'OK', 'WhatsApp');

  select public.claim_approved_po_email_delivery(
    v_po_id, 'exec-1', 'internal.test@example.com'
  )
  into v_result;
  assert (v_result->>'claimed')::boolean,
    'an approved PO with WhatsApp OK must be claimable';
  assert jsonb_array_length(v_result->'items') = 1,
    'the claim must include trusted PO line items';

  select public.claim_approved_po_email_delivery(
    v_po_id, 'exec-1', 'internal.test@example.com'
  )
  into v_result;
  assert (v_result->>'claimed')::boolean,
    'the same n8n execution must reuse its claim after a network retry';

  select public.claim_approved_po_email_delivery(
    v_po_id, 'exec-other', 'internal.test@example.com'
  )
  into v_result;
  assert v_result->>'reason' = 'ALREADY_CLAIMED',
    'a concurrent execution must not send the same PO';

  select public.fail_po_email_delivery(v_po_id, 'exec-1', 'GMAIL_SEND_FAILED')
  into v_result;
  assert (v_result->>'failed')::boolean,
    'a Gmail failure must be persisted';

  select public.fail_po_email_delivery(v_po_id, 'exec-1', 'GMAIL_SEND_FAILED')
  into v_result;
  assert (v_result->>'replay')::boolean,
    'a repeated failure callback must be idempotent';

  select public.claim_approved_po_email_delivery(
    v_po_id, 'exec-2', 'internal.test@example.com'
  )
  into v_result;
  assert (v_result->>'claimed')::boolean,
    'a failed Gmail delivery may be reclaimed safely';
  assert (v_result->>'attempt_count')::integer = 2,
    'the retry must increment the attempt count';

  select public.complete_po_email_delivery(v_po_id, 'exec-2', 'gmail-message-1')
  into v_result;
  assert (v_result->>'sent')::boolean,
    'the Gmail provider message ID must complete the delivery';

  select public.complete_po_email_delivery(v_po_id, 'exec-2', 'gmail-message-1')
  into v_result;
  assert (v_result->>'replay')::boolean,
    'a repeated completion callback must be idempotent';

  select public.claim_approved_po_email_delivery(
    v_po_id, 'exec-3', 'internal.test@example.com'
  )
  into v_result;
  assert v_result->>'reason' = 'ALREADY_SENT',
    'a sent PO must be recognized without sending it again';

  assert (
    select status = 'sent'
    from public.purchase_orders
    where id = v_po_id
  ), 'completion must mark the PO sent';

  assert (
    select incoming_qty = 2 and stock_qty = 0
    from public.inventory
    where product_id = v_product_id and branch_id = v_branch_id
  ), 'completion must update only branch-specific incoming stock';

  assert (
    select status = 'sent' and provider_message_id = 'gmail-message-1'
    from public.po_email_deliveries
    where po_id = v_po_id
  ), 'the outbox must retain the Gmail provider reference';
end
$$;
