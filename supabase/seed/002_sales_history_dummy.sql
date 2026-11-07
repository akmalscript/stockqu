-- StockQu - DUMMY SEED histori penjualan 6 bulan (180 hari)
-- CONTOH SIMULASI - BUKAN DATA REAL. Kolom source = 'DUMMY-SEED'.
-- Jalankan SETELAH supabase/migrations/002_p1_analyst.sql.
-- Deterministik (pakai hashtext, bukan random()) supaya demo bisa diulang (SRS-NF-29).
--
-- Konsisten dengan seed P0:
--   5 item kritis di Branch 1 (Item A-E) -> days-to-stockout <= 7 hari.
--   Target rata-rata harian (unit/hari): A=30, B=24, C=16, D=27, E=45
--   Stok P0 Branch 1: A=100, B=120, C=90, D=150, E=180
--   -> perkiraan days-to-stockout: A~3.3 (URGENT), B~5.0, C~5.6, D~5.6, E~4.0
--   Item kritis punya tren naik (x0.8 di awal -> x1.2 di akhir) supaya Analyst bisa
--   mendeteksi tren dari data, bukan dari kalimat karangan.
--   SKU/cabang lain: rata-rata = stok / (15..44) -> days-to-stockout 15-44 hari (AMAN).
--
-- Idempotent: aman dijalankan ulang (on conflict do nothing).

with params as (
  select (current_date - 180) as start_date
),
base as (
  select
    i.product_id,
    i.branch_id,
    p.sku,
    b.name as branch_name,
    case
      when b.name = 'Branch 1' and p.sku = 'SKU-A' then 30.0
      when b.name = 'Branch 1' and p.sku = 'SKU-B' then 24.0
      when b.name = 'Branch 1' and p.sku = 'SKU-C' then 16.0
      when b.name = 'Branch 1' and p.sku = 'SKU-D' then 27.0
      when b.name = 'Branch 1' and p.sku = 'SKU-E' then 45.0
      else greatest(1.0, i.stock_qty / (15 + (abs(hashtext(p.sku || b.name)::bigint) % 30)))
    end as avg_target,
    (b.name = 'Branch 1' and p.sku in ('SKU-A', 'SKU-B', 'SKU-C', 'SKU-D', 'SKU-E')) as is_critical
  from inventory i
  join products p on p.id = i.product_id
  join branches b on b.id = i.branch_id
),
days as (
  select
    d::date as sale_date,
    (d::date - params.start_date) as t   -- 0..179
  from params,
       generate_series(params.start_date, current_date - 1, interval '1 day') as d
)
insert into sales_history (product_id, branch_id, sale_date, qty, source)
select
  base.product_id,
  base.branch_id,
  days.sale_date,
  greatest(
    0,
    round(
      base.avg_target
      -- tren: item kritis naik linear 0.8 -> 1.2 (rata-rata 1.0); lainnya datar
      * case when base.is_critical then 0.8 + 0.4 * days.t / 179.0 else 1.0 end
      -- musiman mingguan: Sabtu/Minggu lebih ramai (rata-rata mingguan = 1.0)
      * case when extract(isodow from days.sale_date) in (6, 7) then 1.15 else 0.94 end
      -- noise deterministik 0.85 - 1.15
      * (0.85 + 0.30 * ((abs(hashtext(base.sku || base.branch_name || days.sale_date::text)::bigint) % 1000) / 1000.0))
    )
  ) as qty,
  'DUMMY-SEED' as source
from base
cross join days
on conflict (product_id, branch_id, sale_date) do nothing;

-- Cek cepat (harus 27000 baris = 50 SKU x 3 Branch x 180 hari):
-- select count(*) from sales_history where source = 'DUMMY-SEED';
