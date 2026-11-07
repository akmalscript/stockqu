# test_supabase.py - P0-5 Tes Koneksi Supabase
# Jalankan: python test_supabase.py
# Kalau berhasil, artinya .env + RLS + anon key sudah benar
import os
from dotenv import load_dotenv
from supabase import create_client

load_dotenv()

url = os.getenv("SUPABASE_URL")
key = os.getenv("SUPABASE_ANON_KEY")

print("SUPABASE_URL ada:", bool(url))
print("SUPABASE_ANON_KEY ada:", bool(key))

if not url or not key:
    print("ERROR: .env belum keisi atau file .env tidak ketemu di folder ini.")
    print("Pastikan file .env ada di stockqu/.env, bukan .env.example")
    raise SystemExit(1)

supabase = create_client(url, key)

# Tes 1: baca budgets
print("\n--- Tes budgets ---")
res = supabase.table("budgets").select("*").execute()
print(res.data)
assert len(res.data) == 1, "budgets harus 1 baris"
assert res.data[0]["total_budget"] == 50000000
assert res.data[0]["remaining_budget"] == 12000000
print("OK budgets: total 50jt, sisa 12jt")

# Tes 2: baca branches
print("\n--- Tes branches ---")
res2 = supabase.table("branches").select("name, city").execute()
print(res2.data)
assert len(res2.data) == 3
print("OK branches: 3")

# Tes 3: baca products (cek 50)
print("\n--- Tes products count ---")
res3 = supabase.table("products").select("sku, name").execute()
print(f"Jumlah products: {len(res3.data)}")
assert len(res3.data) == 50
print("OK products: 50")

# Tes 4: baca inventory Item A di Branch 1 (harus 100, kritis)
print("\n--- Tes inventory kritis ---")
# Ambil id Item A dan Branch 1 dulu
prod = supabase.table("products").select("id").eq("sku", "SKU-A").execute().data[0]
branch = supabase.table("branches").select("id").eq("name", "Branch 1").execute().data[0]
inv = supabase.table("inventory").select("stock_qty").eq("product_id", prod["id"]).eq("branch_id", branch["id"]).execute()
print(f"Item A di Branch 1 stock: {inv.data}")
assert inv.data[0]["stock_qty"] == 100
print("OK inventory kritis: Item A Branch 1 = 100")

print("\n=== P0-5 BERHASIL: Supabase bisa dibaca dari Python ===")
