-- StockQu - dataset prototype transparan untuk demo, bukan data bisnis nyata.
-- Hanya 5 item prioritas x 3 cabang x 180 hari = 2.700 baris.
-- Angka deterministik agar demo dapat diulang dan diaudit.
-- Untuk production, ganti seluruh baris source PROTOTYPE-SYNTHETIC dengan
-- histori transaksi nyata sebelum mengirim PO ke alamat supplier eksternal.

begin;

delete from public.sales_history
where source in ('DUMMY-SEED', 'PROTOTYPE-SYNTHETIC');

with params as (
  select ((current_timestamp at time zone 'Asia/Jakarta')::date - 180) as start_date
),
base as (
  select
    i.product_id,
    i.branch_id,
    p.sku,
    b.name as branch_name,
    case p.sku
      when 'SKU-A' then 30.0
      when 'SKU-B' then 24.0
      when 'SKU-C' then 16.0
      when 'SKU-D' then 27.0
      when 'SKU-E' then 45.0
    end as daily_average
  from public.inventory i
  join public.products p on p.id = i.product_id
  join public.branches b on b.id = i.branch_id
  where p.sku in ('SKU-A', 'SKU-B', 'SKU-C', 'SKU-D', 'SKU-E')
),
days as (
  select
    d::date as sale_date,
    (d::date - params.start_date) as day_number
  from params,
       generate_series(
         params.start_date,
         (current_timestamp at time zone 'Asia/Jakarta')::date - 1,
         interval '1 day'
       ) as d
)
insert into public.sales_history (product_id, branch_id, sale_date, qty, source)
select
  base.product_id,
  base.branch_id,
  days.sale_date,
  greatest(
    0,
    round(
      base.daily_average
      * case when base.branch_name = 'Branch 1'
          then 0.8 + 0.4 * days.day_number / 179.0
          else 0.55
        end
      * case when extract(isodow from days.sale_date) in (6, 7) then 1.15 else 0.94 end
      * (0.85 + 0.30 * ((abs(hashtext(base.sku || base.branch_name || days.sale_date::text)::bigint) % 1000) / 1000.0))
    )
  ) as qty,
  'PROTOTYPE-SYNTHETIC' as source
from base
cross join days
on conflict (product_id, branch_id, sale_date)
do update set
  qty = excluded.qty,
  source = excluded.source;

commit;
