import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
W2_PATH = ROOT / "n8n/w2_whatsapp_approval_notify.json"
W3_PATH = ROOT / "n8n/w3_whatsapp_inbound_reply.json"


def _workflow(path: Path) -> dict:
    return json.loads(path.read_text())


def _nodes(workflow: dict) -> dict[str, dict]:
    return {node["name"]: node for node in workflow["nodes"]}


def _assert_connections_resolve(workflow: dict) -> None:
    names = set(_nodes(workflow))
    for source, outputs in workflow["connections"].items():
        assert source in names
        for group in outputs["main"]:
            for connection in group:
                assert connection["node"] in names


def test_fonnte_workflows_are_valid_and_contain_no_api_token():
    for path in (W2_PATH, W3_PATH):
        workflow = _workflow(path)
        assert workflow["active"] is False
        assert workflow["settings"]["timezone"] == "Asia/Jakarta"
        _assert_connections_resolve(workflow)

        raw = path.read_text()
        assert "api.fonnte.com/send" in raw
        assert "FONNTE_TOKEN" not in raw
        assert "Authorization:" not in raw


def test_w2_sends_approval_through_credentialed_fonnte_request():
    workflow = _workflow(W2_PATH)
    nodes = _nodes(workflow)
    trigger = nodes["Approval request dari Agent Service"]
    assert trigger["parameters"]["authentication"] == "headerAuth"
    assert trigger["parameters"]["path"] == "stockqu-wa-notify"

    send = nodes["Fonnte kirim approval"]
    assert send["type"] == "n8n-nodes-base.httpRequest"
    assert send["parameters"]["url"] == "https://api.fonnte.com/send"
    assert send["parameters"]["genericAuthType"] == "httpHeaderAuth"
    assert send["retryOnFail"] is True and send["maxTries"] == 3
    assert send["onError"] == "continueRegularOutput"
    assert workflow["connections"]["Fonnte menerima pesan"]["main"][1][0]["node"] == "Stop Fonnte failure"


def test_w3_rejects_unsigned_payload_and_preserves_fonnte_inboxid():
    workflow = _workflow(W3_PATH)
    nodes = _nodes(workflow)
    verify = nodes["Validasi webhook Fonnte"]["parameters"]["jsCode"]
    assert "$env.FONNTE_WEBHOOK_SECRET" in verify
    assert "expected.length >= 32" in verify
    assert "inboxid" in verify

    agent = nodes["Validasi dan putuskan di Agent Service"]
    assert agent["parameters"]["url"].endswith("/webhooks/fonnte-reply")
    assert agent["parameters"]["genericAuthType"] == "httpHeaderAuth"
    assert agent["retryOnFail"] is True and agent["maxTries"] == 3

    send = nodes["Fonnte kirim balasan"]
    body = send["parameters"]["bodyParameters"]["parameters"]
    assert any(item["name"] == "inboxid" for item in body)
    assert send["retryOnFail"] is True and send["maxTries"] == 3
    assert workflow["connections"]["Webhook sah"]["main"][1][0]["node"] == "Tolak webhook Fonnte"


def test_agent_service_never_embeds_or_sends_fonnte_credentials():
    source = (ROOT / "src/service/api.py").read_text()
    assert "FONNTE_TOKEN" not in source
    assert "api.fonnte.com" not in source
    assert '@app.post("/webhooks/fonnte-reply", dependencies=[Depends(require_token)])' in source
