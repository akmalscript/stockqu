"""Jalankan Python Agent Service untuk n8n (P4).

    python -m scripts.run_agent_service              # 0.0.0.0:8000
    python -m scripts.run_agent_service --port 8080

Wajib env: SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, SUPABASE_DB_URL (checkpoint),
N8N_TO_SERVICE_TOKEN. Deploy di belakang HTTPS (reverse proxy / platform) - SRS-NF-19.
"""
from __future__ import annotations

import argparse


def main() -> int:
    import uvicorn

    from src.services.deployment import assert_agent_service_production_ready

    ap = argparse.ArgumentParser(description="StockQu Agent Service")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8000)
    args = ap.parse_args()
    assert_agent_service_production_ready()
    uvicorn.run("src.service.api:app", host=args.host, port=args.port, workers=1, proxy_headers=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
