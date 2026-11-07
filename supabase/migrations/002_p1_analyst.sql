-- StockQu - P1 Data & Analyst
-- Jalankan di Supabase SQL Editor SETELAH 001_init_stockqu.sql.
-- Menambah: sales_history (histori penjualan 6 bulan, SRS-F-06),
--           sync_logs (status sinkron Sheets <-> Supabase, SRS-F-01),
--           analyst_forecasts (hasil Agent Analyst yang persisten, SRS-NF-26).
-- Penulisan hanya lewat service_role (bypass RLS) dari Python Agent Service.

-- 1. Histori penjualan harian per SKU per cabang (stok/penjualan TIDAK dijumlahkan antar cabang)
create table if not exists sales_history (
  id bigint generated always as identity primary key,
  product_id uuid not null references products(id) on delete cascade,
  branch_id uuid not null references branches(id) on delete cascade,
  sale_date date not null,
  qty numeric not null check (qty >= 0),
  source text not null default 'manual',
  created_at timestamptz not null default now(),
  unique (product_id, branch_id, sale_date)
);

create index if not exists idx_sales_history_date on sales_history (sale_date);
create index if not exists idx_sales_history_branch_product on sales_history (branch_id, product_id, sale_date);

-- 2. Log sinkronisasi Google Sheets <-> Supabase
create table if not exists sync_logs (
  id bigint generated always as identity primary key,
  run_id text,
  direction text not null check (direction in ('sheets_to_supabase', 'supabase_to_sheets', 'two_way')),
  status text not null check (status in ('success', 'partial', 'failed', 'skipped')),
  rows_read int not null default 0,
  rows_written int not null default 0,
  rows_rejected int not null default 0,
  conflicts int not null default 0,
  detail jsonb not null default '{}'::jsonb,
  error_message text,
  started_at timestamptz not null default now(),
  finished_at timestamptz
);

create index if not exists idx_sync_logs_started on sync_logs (started_at desc);

-- 3. Hasil Agent Analyst per run, per SKU, per cabang
create table if not exists analyst_forecasts (
  id bigint generated always as identity primary key,
  run_id text not null,
  product_id uuid not null references products(id) on delete cascade,
  branch_id uuid not null references branches(id) on delete cascade,
  as_of_date date not null,
  stock_qty numeric not null,
  total_sales_180d numeric not null,
  avg_daily_sales numeric not null,
  forecast_daily_demand numeric,
  days_to_stockout numeric,
  days_to_stockout_forecast numeric,
  days_to_stockout_low numeric,
  days_to_stockout_high numeric,
  safety_stock numeric not null,
  restock_qty numeric not null,
  lead_time_days int not null,
  risk_level text not null check (risk_level in ('URGENT', 'RISIKO', 'AMAN', 'DATA TIDAK DITEMUKAN')),
  trend_pct numeric,
  model_name text,
  model_mape numeric,
  baseline_mape numeric,
  data_quality jsonb not null default '{}'::jsonb,
  reason_trace jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  unique (run_id, product_id, branch_id)
);

create index if not exists idx_analyst_forecasts_run on analyst_forecasts (run_id);
create index if not exists idx_analyst_forecasts_created on analyst_forecasts (created_at desc);

-- 4. RLS - tidak ada anonymous read. User wajib Supabase Auth dan role berasal
--    dari app_metadata (bukan user_metadata). Write hanya service_role.
alter table sales_history enable row level security;
alter table sync_logs enable row level security;
alter table analyst_forecasts enable row level security;

drop policy if exists "read sales_history" on sales_history;
create policy "read sales_history" on sales_history for select to authenticated using (
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

drop policy if exists "read sync_logs" on sync_logs;
create policy "read sync_logs" on sync_logs for select to authenticated using (
  coalesce((select auth.jwt() -> 'app_metadata' ->> 'role'), '') in
    ('super_admin', 'warehouse_manager', 'purchasing_staff', 'procurement_manager')
);

drop policy if exists "read analyst_forecasts" on analyst_forecasts;
create policy "read analyst_forecasts" on analyst_forecasts for select to authenticated using (
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

revoke all on table sales_history, sync_logs, analyst_forecasts from anon, authenticated;
grant select on table sales_history, sync_logs, analyst_forecasts to authenticated;
grant all on table sales_history, sync_logs, analyst_forecasts to service_role;
grant usage, select on sequence sales_history_id_seq, sync_logs_id_seq,
  analyst_forecasts_id_seq to service_role;
