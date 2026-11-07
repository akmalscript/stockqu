-- StockQu - Skema awal, 10 tabel + RLS
-- Jalankan di Supabase SQL Editor

create table if not exists branches (
  id uuid primary key default gen_random_uuid(),
  name text not null,
  city text not null,
  address text,
  created_at timestamptz default now()
);

create table if not exists products (
  id uuid primary key default gen_random_uuid(),
  sku text unique not null,
  name text not null,
  category text,
  margin numeric default 0,
  turnover_rate numeric default 0,
  lead_time_days int default 3,
  safety_days int default 3,
  created_at timestamptz default now()
);

create table if not exists inventory (
  id uuid primary key default gen_random_uuid(),
  product_id uuid references products(id) on delete cascade,
  branch_id uuid references branches(id) on delete cascade,
  stock_qty numeric default 0,
  updated_at timestamptz default now(),
  unique(product_id, branch_id)
);

create table if not exists suppliers (
  id uuid primary key default gen_random_uuid(),
  name text not null,
  contact text,
  lead_time_days int default 3,
  performance_score numeric default 0,
  created_at timestamptz default now()
);

create table if not exists contracts (
  id uuid primary key default gen_random_uuid(),
  supplier_id uuid references suppliers(id) on delete cascade,
  product_id uuid references products(id) on delete cascade,
  price numeric not null,
  discount numeric default 0,
  valid_until date,
  file_url text,
  created_at timestamptz default now()
);

create table if not exists budgets (
  id uuid primary key default gen_random_uuid(),
  month text not null,
  total_budget numeric default 50000000,
  used_budget numeric default 0,
  remaining_budget numeric default 50000000,
  unique(month)
);

create table if not exists purchase_orders (
  id uuid primary key default gen_random_uuid(),
  supplier_id uuid references suppliers(id),
  total_amount numeric default 0,
  status text default 'draft',
  created_at timestamptz default now()
);

create table if not exists po_items (
  id uuid primary key default gen_random_uuid(),
  po_id uuid references purchase_orders(id) on delete cascade,
  product_id uuid references products(id),
  qty numeric not null,
  unit_price numeric not null
);

create table if not exists approvals (
  id uuid primary key default gen_random_uuid(),
  po_id uuid references purchase_orders(id) on delete cascade,
  approver_role text,
  decision text,
  via text default 'WhatsApp',
  created_at timestamptz default now()
);

create table if not exists agent_logs (
  id uuid primary key default gen_random_uuid(),
  agent_name text,
  run_id text,
  tool_name text,
  status text,
  input_summary text,
  output_summary text,
  error_message text,
  created_at timestamptz default now()
);

-- RLS: aktifkan, kebijakan detail diatur per role di Supabase Dashboard
alter table branches enable row level security;
alter table products enable row level security;
alter table inventory enable row level security;
alter table suppliers enable row level security;
alter table contracts enable row level security;
alter table budgets enable row level security;
alter table purchase_orders enable row level security;
alter table po_items enable row level security;
alter table approvals enable row level security;
alter table agent_logs enable row level security;