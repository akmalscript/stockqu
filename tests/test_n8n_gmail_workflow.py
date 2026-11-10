import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_PATH = ROOT / "n8n/w4_w5_po_email_stock_update.json"
N8N_README_PATH = ROOT / "n8n/README.md"
MIGRATION_PATH = (
    ROOT / "supabase/migrations/20261110110000_add_po_email_delivery_outbox.sql"
)


def _workflow() -> dict:
    return json.loads(WORKFLOW_PATH.read_text())


def test_gmail_workflow_has_valid_connections_and_no_committed_credentials():
    workflow = _workflow()
    node_names = {node["name"] for node in workflow["nodes"]}

    assert workflow["active"] is False
    assert workflow["settings"]["timezone"] == "Asia/Jakarta"

    for source, outputs in workflow["connections"].items():
        assert source in node_names
        for output_group in outputs["main"]:
            for connection in output_group:
                assert connection["node"] in node_names

    raw = WORKFLOW_PATH.read_text().lower()
    assert "sb_secret_" not in raw
    assert "sb_publishable_" not in raw
    assert '"credentials"' not in raw
    assert "replace_me" not in raw


def test_gmail_send_is_approval_gated_idempotent_and_has_failure_path():
    workflow = _workflow()
    nodes = {node["name"]: node for node in workflow["nodes"]}

    trigger = nodes["PO approved (dari Agent Service)"]
    assert trigger["type"] == "n8n-nodes-base.webhook"
    assert trigger["parameters"]["authentication"] == "headerAuth"
    assert trigger["parameters"]["path"] == "stockqu-po-email"

    gmail = nodes["W4 - Gmail kirim PO"]
    assert gmail["type"] == "n8n-nodes-base.gmail"
    assert gmail["parameters"]["authentication"] == "oAuth2"
    assert gmail["parameters"]["operation"] == "send"
    assert gmail["parameters"]["options"]["appendAttribution"] is False
    assert gmail["onError"] == "continueRegularOutput"
    # An automatic Gmail retry can duplicate a PO if the response is lost after send.
    assert "retryOnFail" not in gmail

    for name in (
        "Claim Approved PO",
        "Complete Delivery Audit",
        "Record Gmail Failure",
    ):
        node = nodes[name]
        assert node["type"] == "n8n-nodes-base.httpRequest"
        assert node["parameters"]["nodeCredentialType"] == "supabaseApi"
        assert node["retryOnFail"] is True
        assert node["maxTries"] == 3

    callback = nodes["W5 - Sinkronkan Agent Service + Sheets"]
    assert "$env.STOCKQU_AGENT_SERVICE_URL" in callback["parameters"]["url"]
    assert "callback_url" not in callback["parameters"]["url"]
    assert callback["retryOnFail"] is True
    assert callback["maxTries"] == 3

    assert "Complete Delivery Audit" in workflow["connections"]["Gmail Accepted?"][
        "main"
    ][0][0]["node"]
    assert "Record Gmail Failure" in workflow["connections"]["Gmail Accepted?"][
        "main"
    ][1][0]["node"]
    assert "Failure Recorded?" in workflow["connections"]["Record Gmail Failure"][
        "main"
    ][0][0]["node"]
    assert workflow["connections"]["Failure Recorded?"]["main"][0][0]["node"] == (
        "Stop Gmail Failure"
    )
    assert workflow["connections"]["Failure Recorded?"]["main"][1][0]["node"] == (
        "Stop Failure Audit Failure"
    )
    assert "W5 - Sinkronkan Agent Service + Sheets" in workflow["connections"][
        "Audit Completed?"
    ]["main"][0][0]["node"]
    assert "W5 - Sinkronkan Agent Service + Sheets" in workflow["connections"][
        "Already Sent?"
    ]["main"][0][0]["node"]


def test_email_outbox_migration_enforces_approval_and_service_role_only():
    sql = MIGRATION_PATH.read_text().lower()

    assert "create table if not exists public.po_email_deliveries" in sql
    assert "po_id uuid not null unique" in sql
    assert "alter table public.po_email_deliveries enable row level security" in sql
    assert "from anon, authenticated" in sql
    assert "to service_role" in sql
    assert "security invoker" in sql
    assert "latest_approval.decision" in sql
    assert "latest_approval.via" in sql
    assert "pg_catalog.lower(po.status) = 'approved'" in sql
    assert "'whatsapp'" in sql
    assert "on conflict (po_id) do update" in sql
    assert "public.po_email_deliveries.status = 'failed'" in sql
    assert "public.po_email_deliveries.attempt_count < 3" in sql
    assert "provider_message_id" in sql
    assert "gmail_send_po" in sql
    assert "perform public.mark_po_sent" in sql
    assert "v_delivery.status = 'sent'" in sql
    assert "v_delivery.status = 'failed'" in sql
    assert "replay" in sql
    assert "grant execute on function public.claim_approved_po_email_delivery" in sql
    assert "grant execute on function public.complete_po_email_delivery" in sql
    assert "grant execute on function public.fail_po_email_delivery" in sql
    assert "to anon" not in sql
    assert "to authenticated" not in sql


def test_local_compose_is_loopback_only_and_does_not_contain_secrets():
    compose = (ROOT / "n8n/docker-compose.yml").read_text()
    production_compose = (ROOT / "n8n/docker-compose.prod.yml").read_text()
    env_example = (ROOT / "n8n/.env.example").read_text()
    production_env = (ROOT / "n8n/.env.production.example").read_text()

    assert "127.0.0.1:${N8N_PORT:-5678}:5678" in compose
    assert "docker.n8n.io/n8nio/n8n:2.46.6" in compose
    assert "N8N_PUBLIC_API_DISABLED: \"true\"" in compose
    assert "N8N_COMMUNITY_PACKAGES_ENABLED: \"false\"" in compose
    assert "WEBHOOK_URL:" in compose
    assert "N8N_WEBHOOK_URL" not in compose
    assert "SUPABASE_URL=" in env_example
    assert "sb_secret_" not in env_example
    assert "client_secret" not in env_example.lower()
    assert "WEBHOOK_URL=https://" in production_env
    assert "localhost" not in production_env
    assert "N8N_ENCRYPTION_KEY" in production_compose
    assert 'N8N_SECURE_COOKIE: "true"' in production_compose


def test_gmail_oauth_documentation_uses_minimum_working_scopes():
    docs = N8N_README_PATH.read_text()
    normalized = " ".join(docs.split())

    assert "https://www.googleapis.com/auth/gmail.send" in docs
    assert "https://www.googleapis.com/auth/gmail.metadata" in docs
    assert "Jangan memakai scope penuh `https://mail.google.com/`." in normalized
