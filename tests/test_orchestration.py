"""Test P4: graph penuh, approval WA/dashboard, state machine PO, Agent Service API.

FakeRepo meniru semantik RPC Postgres (request_po_approval, decide_approval, mark_po_sent)
supaya aturan human-in-the-loop dan idempotensi bisa diuji tanpa jaringan.
CONTOH SIMULASI - BUKAN DATA REAL.
"""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver

from src.orchestration.procurement_graph import OrchestratorDeps
from src.orchestration.runtime import ProcurementRuntime
from src.service import api
from src.services.approval import (
    ApprovalRejected,
    build_approval_message,
    normalize_phone,
    parse_reply,
    twilio_signature,
    verify_twilio_signature,
)
from src.services.n8n_client import DispatchResult

ROOT = Path(__file__).resolve().parents[1]
MANAGER = "+6281200000001"
POS = [
    {"po_number": "PO-1", "supplier_id": "sA", "supplier_name": "Supplier A", "branch_id": "b1", "branch": "Branch 1",
     "total_amount": 972_000.0, "expected_arrival": "2026-11-12",
     "items": [{"product_id": "pA", "sku": "SKU-A", "product_name": "Item A", "qty": 81, "unit_price": 12000,
                "line_total": 972_000.0, "list_price": 12000, "discount_pct": 0}]},
    {"po_number": "PO-2", "supplier_id": "sB", "supplier_name": "Supplier B", "branch_id": "b1", "branch": "Branch 1",
     "total_amount": 359_268.0, "expected_arrival": "2026-11-13",
     "items": [{"product_id": "pB", "sku": "SKU-B", "product_name": "Item B", "qty": 47, "unit_price": 7644,
                "line_total": 359_268.0, "list_price": 7800, "discount_pct": 2}]},
]


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setenv("APPROVER_WHATSAPP_NUMBERS", f"{MANAGER}, 0812-0000-0002")
    monkeypatch.setenv("N8N_TO_SERVICE_TOKEN", "svc-token")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "twilio-secret")
    monkeypatch.setenv("AGENT_SERVICE_PUBLIC_URL", "https://agent.example.test")
    monkeypatch.delenv("PO_EMAIL_TEST_RECIPIENT", raising=False)


class FakeNotifier:
    def __init__(self, status="sent"):
        self.calls = []
        self.status = status

    def post(self, url_setting, payload):
        self.calls.append((url_setting, payload))
        return DispatchResult(self.status, "HTTP 200" if self.status == "sent" else "down", 1)


class P4Repo:
    """State machine PO di memori, aturan sama dengan RPC migration P4."""

    def __init__(self, budget_used=38_000_000):
        self.budget_used = budget_used
        self.pos = {f"po{n}": {**p, "id": f"po{n}", "status": "draft", "draft": {**p, "emails": {
            "purchase_order": {"subject": f"Purchase Order {p['po_number']} - StockQu", "body": "isi"}}}}
            for n, p in enumerate(POS, start=1)}
        self.reservation = {"plan_id": "plan-1", "amount": 1_331_268.0, "status": "active"}
        self.requests: dict[str, dict] = {}
        self.jobs: dict[str, dict] = {}
        self.incoming: dict[tuple, float] = {}
        self.logs = []

    # budget + plan
    def fetch_procurement_plan(self, run_id):
        return {"id": "plan-1", "period": "2026-11", "run_id": run_id}

    def fetch_budget(self, period):
        return {"month": period, "total_budget": 50_000_000, "used_budget": self.budget_used}

    def fetch_purchase_orders_for_period(self, period):
        return [{"total_amount": p["total_amount"], "status": p["status"]} for p in self.pos.values()]

    def fetch_active_budget_reservations(self, period):
        return [self.reservation] if self.reservation["status"] == "active" else []

    # RPC request_po_approval
    def request_po_approval(self, payload):
        for r in self.requests.values():
            if r["negotiation_run_id"] == payload["negotiation_run_id"]:
                return {**r, "replay": True}
        drafts = [p for p in self.pos.values() if p["status"] == "draft"]
        assert drafts, "no draft purchase orders to approve"
        code = f"APR-20261109-{len(self.requests) + 1:02d}"
        for p in drafts:
            p["status"] = "waiting_approval"
            p["approval_code"] = code
        self.reservation["status"] = "converted"
        req = {"approval_code": code, "status": "pending", "po_ids": [p["id"] for p in drafts],
               "total_amount": sum(p["total_amount"] for p in drafts), "workflow_run_id": payload["workflow_run_id"],
               "negotiation_run_id": payload["negotiation_run_id"],
               "message": payload["message_template"].replace("{{APPROVAL_CODE}}", code), "refs": set()}
        self.requests[code] = req
        return {k: v for k, v in req.items() if k != "refs"} | {"replay": False}

    def set_approval_notification(self, code, status):
        self.requests[code]["notification"] = status

    def fetch_approval_request(self, code):
        return self.requests.get(code)

    # RPC decide_approval
    def decide_approval(self, d):
        for r in self.requests.values():
            if d["decision_ref"] in r["refs"]:
                return {"approval_code": r["approval_code"], "status": r["status"], "po_ids": r["po_ids"], "replay": True}
        req = self.requests[d["approval_code"]]
        if req["status"] != "pending":
            raise RuntimeError("approval already decided")
        to = {"approved": "approved", "rejected": "rejected", "revision_requested": "draft"}[d["decision"]]
        for pid in req["po_ids"]:
            assert self.pos[pid]["status"] == "waiting_approval"
            self.pos[pid]["status"] = to
        self.reservation["status"] = "active" if d["decision"] == "revision_requested" else "released"
        req["status"] = d["decision"]
        req["refs"].add(d["decision_ref"])
        return {"approval_code": req["approval_code"], "status": d["decision"], "po_ids": req["po_ids"], "replay": False}

    # RPC mark_po_sent
    def fetch_purchase_order_by_number(self, number):
        return next((p for p in self.pos.values() if p["po_number"] == number), None)

    def mark_po_sent(self, po_id, ref, actor):
        po = self.pos[po_id]
        if po["status"] == "sent":
            return {"po_id": po_id, "status": "sent", "replay": True, "incoming": []}
        if po["status"] != "approved":
            raise RuntimeError("purchase order is not approved; email and stock update are blocked")
        po["status"] = "sent"
        for i in po["items"]:
            key = (i["product_id"], po["branch_id"])
            self.incoming[key] = self.incoming.get(key, 0) + i["qty"]
        return {"po_id": po_id, "status": "sent", "replay": False, "incoming": [{"product_id": k[0], "qty": v}
                                                                                   for k, v in self.incoming.items()]}

    # dispatch helpers
    def fetch_supplier_emails(self):
        return {"sA": "a@supplier.test", "sB": None}

    def fetch_purchase_orders_by_ids(self, ids):
        return [{**self.pos[i], "draft": self.pos[i]["draft"]} for i in ids]

    def has_dummy_sales(self):
        return True

    # jobs + logs
    def create_workflow_job(self, row):
        for j in self.jobs.values():
            if j["job_key"] == row["job_key"] or j["status"] == "running":
                return j, False
        self.jobs[row["run_id"]] = dict(row)
        return self.jobs[row["run_id"]], True

    def update_workflow_job(self, run_id, fields):
        self.jobs[run_id].update(fields)

    def insert_agent_logs(self, rows):
        self.logs.extend(rows)
        return len(rows)


def make_deps(repo, notifier=None, risk=2, plan_total=1_331_268.0):
    calls = []

    def analyst(run_id):
        calls.append("analyst")
        return {"run_id": run_id, "status": "completed", "persisted": True, "risk_items": [{}] * risk}

    def purchaser(analyst_run_id, run_id):
        calls.append("purchaser")
        return {"run_id": run_id, "status": "completed", "persisted": True, "plan_id": "plan-1",
                "optimization": {"proposed_total": plan_total}}

    def negotiator(purchaser_run_id, run_id):
        calls.append("negotiator")
        return {"run_id": run_id, "status": "completed", "persisted": True, "negotiation_id": "neg-1",
                "po_total": 1_331_268.0, "pos": POS,
                "comparisons": [
                    {"selected": {}, "selection_status": "selected",
                     "item": {"sku": "SKU-A", "branch": "Branch 1", "days_to_stockout": 3.32}},
                    {"selected": {}, "selection_status": "selected",
                     "item": {"sku": "SKU-B", "branch": "Branch 1", "days_to_stockout": 5.04}},
                ]}

    deps = OrchestratorDeps(repo=repo, run_analyst=analyst, run_purchaser=purchaser,
                            run_negotiator=negotiator, notifier=notifier or FakeNotifier())
    return deps, calls


def runtime_with(repo, notifier=None, **kw):
    deps, calls = make_deps(repo, notifier, **kw)
    return ProcurementRuntime(deps, InMemorySaver(), sheets_factory=None), calls


def start(rt):
    job, created = rt.create_job("cron", "test", day="2026-11-09")
    assert created
    return job, rt.run_job(job)


# ---------------------------------------------------------------- approval helpers
def test_parse_reply_requires_code():
    assert parse_reply("ok apr-20261109-01").approval_code == "APR-20261109-01"
    assert parse_reply("TOLAK APR-20261109-01").decision == "rejected"
    assert parse_reply("Revisi APR-20261109-12.").decision == "revision_requested"
    for bad in ("OK", "ok saja", "HAPUS APR-20261109-01", ""):
        with pytest.raises(ApprovalRejected):
            parse_reply(bad)


def test_phone_normalization_and_twilio_signature():
    assert normalize_phone("whatsapp:+62 812-0000-0001") == MANAGER
    assert normalize_phone("0812-0000-0002") == "+6281200000002"
    params = {"From": f"whatsapp:{MANAGER}", "Body": "OK APR-20261109-01", "MessageSid": "SM1"}
    sig = twilio_signature("twilio-secret", "https://n8n.test/webhook/wa", params)
    verify_twilio_signature("https://n8n.test/webhook/wa", params, sig)
    with pytest.raises(ApprovalRejected):
        verify_twilio_signature("https://n8n.test/webhook/wa", {**params, "Body": "OK APR-20261109-02"}, sig)


def test_approval_message_uses_template_numbers():
    msg = build_approval_message(POS, {"sku": "SKU-A", "branch": "Branch 1", "days_to_stockout": 3.32})
    assert "Rp 1.331.268 untuk 2 item, paling urgent SKU-A (Branch 1, habis 3.3 hari)" in msg["template"]
    assert "Balas OK {{APPROVAL_CODE}}" in msg["template"]


# ---------------------------------------------------------------- graph
def test_full_flow_waits_for_ok_then_emails_and_updates_stock():
    repo, notifier = P4Repo(), FakeNotifier()
    rt, calls = runtime_with(repo, notifier)
    job, out = start(rt)
    assert calls == ["analyst", "purchaser", "negotiator"]
    assert out["waiting"] is True and out["status"] == "waiting_approval"
    code = out["approval"]["approval_code"]
    assert code == "APR-20261109-01" and repo.jobs[job["run_id"]]["status"] == "waiting_approval"
    assert all(p["status"] == "waiting_approval" for p in repo.pos.values())
    wa = notifier.calls[0]
    assert wa[0] == "N8N_WA_NOTIFY_WEBHOOK_URL" and f"OK {code}" in wa[1]["message"] and MANAGER in wa[1]["to"]
    assert not any(c[0] == "N8N_PO_EMAIL_WEBHOOK_URL" for c in notifier.calls)  # belum ada OK -> belum ada email

    # Tanpa approval, callback sent ditolak (stok tidak berubah)
    with pytest.raises(RuntimeError, match="not approved"):
        rt.mark_sent("PO-1", "gmail-1")
    assert repo.incoming == {}

    res = rt.submit_decision(code, {"decision": "approved", "via": "whatsapp", "decided_by": MANAGER,
                                    "decision_ref": "twilio:SM1"})
    assert res["status"] == "approved" and "DISETUJUI" in res["reply"]
    emails = [c for c in notifier.calls if c[0] == "N8N_PO_EMAIL_WEBHOOK_URL"]
    assert [c[1]["po_number"] for c in emails] == ["PO-1"]  # PO-2 diblokir: email supplier kosong
    assert emails[0][1]["callback_url"] == "https://agent.example.test/purchase-orders/PO-1/sent"
    assert emails[0][1]["pdf_base64"]
    blocked = next(d for d in res["dispatch"] if d["po_number"] == "PO-2")
    assert blocked["status"] == "blocked" and "DATA TIDAK DITEMUKAN" in blocked["detail"]
    assert repo.jobs[job["run_id"]]["status"] == "completed"

    sent = rt.mark_sent("PO-1", "gmail-1")
    assert sent["status"] == "sent" and repo.incoming == {("pA", "b1"): 81}
    again = rt.mark_sent("PO-1", "gmail-1")
    assert again["replay"] is True and repo.incoming == {("pA", "b1"): 81}  # tidak dobel


def test_reject_stops_without_email():
    repo, notifier = P4Repo(), FakeNotifier()
    rt, _ = runtime_with(repo, notifier)
    _, out = start(rt)
    res = rt.submit_decision(out["approval"]["approval_code"], {"decision": "rejected", "via": "dashboard",
                                                               "decided_by": "u1", "decision_ref": "dashboard:n1"})
    assert res["status"] == "rejected"
    assert all(p["status"] == "rejected" for p in repo.pos.values())
    assert not any(c[0] == "N8N_PO_EMAIL_WEBHOOK_URL" for c in notifier.calls)


def test_replayed_webhook_is_idempotent():
    repo, notifier = P4Repo(), FakeNotifier()
    rt, _ = runtime_with(repo, notifier)
    _, out = start(rt)
    code = out["approval"]["approval_code"]
    d = {"decision": "approved", "via": "whatsapp", "decided_by": MANAGER, "decision_ref": "twilio:SM9"}
    rt.submit_decision(code, d)
    n_email = sum(c[0] == "N8N_PO_EMAIL_WEBHOOK_URL" for c in notifier.calls)
    res = rt.submit_decision(code, d)  # Twilio retry dengan MessageSid sama
    assert res["status"] == "approved"
    assert sum(c[0] == "N8N_PO_EMAIL_WEBHOOK_URL" for c in notifier.calls) == n_email


def test_no_risk_ends_without_purchaser():
    repo = P4Repo()
    rt, calls = runtime_with(repo, risk=0)
    job, out = start(rt)
    assert calls == ["analyst"] and out["status"] == "no_action" and out["waiting"] is False
    assert repo.jobs[job["run_id"]]["status"] == "no_action"


def test_budget_recheck_blocks_approval_request():
    repo = P4Repo(budget_used=50_000_000)  # tersedia 0, reservasi plan 1.33 jt -> masih cukup pas
    repo.reservation["amount"] = 1_000_000  # batas 1 jt < total PO 1.33 jt
    rt, _ = runtime_with(repo)
    _, out = start(rt)
    assert out["status"] == "failed" and "approval" not in out
    assert all(p["status"] == "draft" for p in repo.pos.values())


def test_wa_failure_falls_back_to_dashboard_approval():
    repo = P4Repo()
    rt, _ = runtime_with(repo, FakeNotifier(status="failed"))
    _, out = start(rt)
    assert out["waiting"] and out["approval"]["notification"] == "failed"
    assert any("dashboard" in w for w in out["warnings"])


def test_duplicate_daily_job_is_rejected():
    rt, _ = runtime_with(P4Repo())
    job, _ = start(rt)
    dup, created = rt.create_job("cron", "test", day="2026-11-09")
    assert created is False and dup["run_id"] == job["run_id"]


# ---------------------------------------------------------------- API
def api_client(repo, notifier=None):
    rt, _ = runtime_with(repo, notifier)
    api.set_runtime(rt)
    return TestClient(api.app), rt


def test_api_requires_token_and_runs_cron():
    client, _ = api_client(P4Repo())
    assert client.post("/workflows/daily-run", json={}).status_code == 401
    h = {"Authorization": "Bearer svc-token"}
    r = client.post("/workflows/daily-run", json={"day": "2026-11-09", "wait": True}, headers=h)
    assert r.status_code == 202 and r.json()["approval_code"] == "APR-20261109-01"
    dup = client.post("/workflows/daily-run", json={"day": "2026-11-09", "wait": True}, headers=h)
    assert dup.json()["duplicate"] is True


def test_api_whatsapp_reply_checks_signature_and_whitelist():
    repo = P4Repo()
    client, _rt = api_client(repo)
    h = {"Authorization": "Bearer svc-token"}
    client.post("/workflows/daily-run", json={"day": "2026-11-09", "wait": True}, headers=h)
    url = "https://n8n.test/webhook/wa"

    def body(sender, text, sid, sign=True):
        params = {"From": f"whatsapp:{sender}", "Body": text, "MessageSid": sid}
        return {"webhook_url": url, "params": params,
                "twilio_signature": twilio_signature("twilio-secret", url, params) if sign else "bad"}

    forged = client.post("/webhooks/whatsapp-reply", json=body(MANAGER, "OK APR-20261109-01", "SM1", sign=False), headers=h)
    assert forged.json()["accepted"] is False and "Signature" in forged.json()["reply_text"]
    stranger = client.post("/webhooks/whatsapp-reply", json=body("+6289999999999", "OK APR-20261109-01", "SM2"), headers=h)
    assert stranger.json()["accepted"] is False and "tidak terdaftar" in stranger.json()["reply_text"]
    assert all(p["status"] == "waiting_approval" for p in repo.pos.values())

    ok = client.post("/webhooks/whatsapp-reply", json=body(MANAGER, "OK APR-20261109-01", "SM3"), headers=h)
    assert ok.json()["accepted"] is True and ok.json()["status"] == "approved"
    late = client.post("/webhooks/whatsapp-reply", json=body(MANAGER, "TOLAK APR-20261109-01", "SM4"), headers=h)
    assert late.json()["accepted"] is False and "sudah diputuskan" in late.json()["reply_text"]

    sent = client.post("/purchase-orders/PO-1/sent", json={"email_message_id": "gmail-1"}, headers=h)
    assert sent.status_code == 200 and sent.json()["status"] == "sent"


def test_api_sent_callback_blocked_before_approval():
    repo = P4Repo()
    client, _ = api_client(repo)
    h = {"Authorization": "Bearer svc-token"}
    client.post("/workflows/daily-run", json={"day": "2026-11-09", "wait": True}, headers=h)
    r = client.post("/purchase-orders/PO-1/sent", json={"email_message_id": "x"}, headers=h)
    assert r.status_code == 409 and repo.incoming == {}


# ---------------------------------------------------------------- migration
def test_p4_migration_enforces_state_machine_in_database():
    sql = (ROOT / "supabase/migrations/20261109200000_p4_orchestration_approval.sql").read_text().lower()
    assert " to anon" not in sql and "auth.jwt() -> 'user_metadata'" not in sql
    assert "purchase order is not approved; email and stock update are blocked" in sql
    assert "decision_ref text unique" in sql and "approval already decided" in sql
    assert "status = 'waiting_approval'" in sql and "uq_workflow_jobs_one_running" in sql
    assert "unique (po_id, product_id, txn_type)" in sql
    assert "incoming_qty" in sql and "stock_qty = public.inventory.stock_qty" not in sql
    assert "revoke all on schema langgraph from public, anon, authenticated" in sql
    for fn in ("request_po_approval(jsonb)", "decide_approval(jsonb)", "mark_po_sent(uuid, text, text)"):
        assert f"grant execute on function public.{fn} to service_role" in sql


def test_incoming_sheet_upsert_is_idempotent():
    from src.services.incoming_sync import INCOMING_HEADER, upsert_incoming_rows

    class Sheet:
        def __init__(self):
            self.cells = {}

        def ensure_tab(self, tab):
            return {"title": tab}

        def get_range(self, rng):
            rows = [self.cells[r] for r in sorted(self.cells)]
            return rows

        def update_range(self, rng, values):
            row = int(rng.split("!A")[1].split(":")[0])
            self.cells[row] = [str(v) for v in values[0]]
            return 1

    sheet = Sheet()
    row = {"po_number": "PO-1", "sku": "SKU-A", "product_name": "Item A", "branch": "Branch 1",
           "qty_incoming": 81, "supplier": "Supplier A", "expected_arrival": "2026-11-12", "status": "in_transit"}
    first = upsert_incoming_rows(sheet, [row])
    second = upsert_incoming_rows(sheet, [row])
    assert first["appended"] == 1 and second == {"tab": "Incoming", "appended": 0, "updated": 1}
    assert sheet.cells[1] == INCOMING_HEADER and len(sheet.cells) == 2 and sheet.cells[2][4] == "81"


def test_concurrent_incoming_callbacks_do_not_overwrite_rows():
    import time
    from concurrent.futures import ThreadPoolExecutor

    from src.services.incoming_sync import INCOMING_HEADER, upsert_incoming_rows

    class SlowSheet:
        def __init__(self):
            self.cells = {}

        def ensure_tab(self, tab):
            return {"title": tab}

        def get_range(self, rng):
            values = [list(self.cells[r]) for r in sorted(self.cells)]
            time.sleep(0.02)
            return values

        def update_range(self, rng, values):
            row = int(rng.split("!A")[1].split(":")[0])
            self.cells[row] = [str(v) for v in values[0]]
            return 1

    sheet = SlowSheet()
    base = {"product_name": "Item A", "branch": "Branch 1", "qty_incoming": 1,
            "supplier": "Supplier A", "expected_arrival": "2026-11-12", "status": "in_transit"}
    rows = [
        [{**base, "po_number": "PO-1", "sku": "SKU-A"}],
        [{**base, "po_number": "PO-2", "sku": "SKU-B"}],
    ]

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda value: upsert_incoming_rows(sheet, value), rows))

    assert sheet.cells[1] == INCOMING_HEADER
    assert {(row[0], row[1]) for row in sheet.cells.values() if row != INCOMING_HEADER} == {
        ("PO-1", "SKU-A"), ("PO-2", "SKU-B")
    }
