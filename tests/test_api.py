import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_status(client):
    r = client.get("/status")
    assert r.status_code == 200
    body = r.json()
    assert body["agent"] == "nihon.chatbot"
    assert body["protocol"] == "nihon-loop"
    assert "open_cycles" in body


def test_skill_md(client):
    r = client.get("/skill.md")
    assert r.status_code == 200
    assert "Nihon Loop" in r.text
    assert "POST /cycles" in r.text


def test_agent_json(client):
    r = client.get("/agent.json")
    assert r.status_code == 200
    body = r.json()
    assert body["domain"] == "nihon.chatbot"
    assert "kaizen_coach" in body["capabilities"]
    assert body["endpoints"]["webhook"].endswith("/webhook")
    assert body["payments"]["inbound"] == "pending"


def test_index_page(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "Nihon Loop" in r.text


def test_full_cycle_no_llm_key(client):
    # PLAN: create a system
    r = client.post("/systems", json={"name": "support-queue", "current_standard": "1. Triage daily."})
    assert r.status_code == 200
    system = r.json()
    system_id = system["id"]

    # PLAN: start a cycle
    r = client.post(f"/systems/{system_id}/cycles", json={"job": "Triage Monday backlog", "tool_budget": 8})
    assert r.status_code == 200
    cycle = r.json()
    cycle_id = cycle["id"]
    assert cycle["status"] == "plan"

    # DO: submit artifacts within budget that still show clear waste
    # (long wait, some errors, a context switch) — going over budget is
    # its own separate rule (see test_do_rejects_over_budget_*), so this
    # stays within tool_budget to isolate the waste-detection behavior.
    r = client.post(
        f"/cycles/{cycle_id}/do",
        json={
            "artifacts": {
                "tool_calls": 8,
                "human_wait_seconds": 300,
                "errors": 2,
                "notes": "had to switch tabs to re-read old tickets",
            },
            "tools_used": 8,
        },
    )
    assert r.status_code == 200
    cycle = r.json()
    assert cycle["status"] == "do"

    # CHECK: waste should be computed and exactly one change proposed
    r = client.post(f"/cycles/{cycle_id}/check", json={})
    assert r.status_code == 200
    cycle = r.json()
    assert cycle["status"] == "check"
    assert cycle["proposed_change"]
    assert cycle["before_metric_name"] in (
        "extra_tool_calls",
        "human_wait_seconds",
        "repeated_errors",
        "rework",
        "context_thrash",
        "overproduction",
    )

    # ACT: accept the change, standard should gain a new numbered line
    r = client.post(f"/cycles/{cycle_id}/act", json={"accept": True})
    assert r.status_code == 200
    cycle = r.json()
    assert cycle["status"] == "closed"
    assert cycle["accepted_change"]

    r = client.get(f"/systems/{system_id}")
    system = r.json()
    assert "2." in system["current_standard"]


def test_do_rejects_over_budget_without_explanation(client):
    r = client.post("/systems", json={"name": "over-budget-test"})
    system_id = r.json()["id"]
    r = client.post(f"/systems/{system_id}/cycles", json={"job": "a job", "tool_budget": 3})
    cycle_id = r.json()["id"]

    r = client.post(
        f"/cycles/{cycle_id}/do",
        json={"artifacts": {"tool_calls": 9, "human_wait_seconds": 0, "errors": 0}, "tools_used": 9},
    )
    assert r.status_code == 400


def test_do_allows_over_budget_when_job_failed_explained(client):
    r = client.post("/systems", json={"name": "over-budget-explained"})
    system_id = r.json()["id"]
    r = client.post(f"/systems/{system_id}/cycles", json={"job": "a job", "tool_budget": 3})
    cycle_id = r.json()["id"]

    r = client.post(
        f"/cycles/{cycle_id}/do",
        json={
            "artifacts": {"tool_calls": 9, "human_wait_seconds": 0, "errors": 0},
            "tools_used": 9,
            "job_failed": True,
            "job_failed_reason": "hit an infinite retry loop",
        },
    )
    assert r.status_code == 200


def test_check_with_no_waste_produces_no_proposal(client):
    r = client.post("/systems", json={"name": "clean-run"})
    system_id = r.json()["id"]
    r = client.post(f"/systems/{system_id}/cycles", json={"job": "a clean job", "tool_budget": 8})
    cycle_id = r.json()["id"]

    r = client.post(
        f"/cycles/{cycle_id}/do",
        json={"artifacts": {"tool_calls": 2, "human_wait_seconds": 0, "errors": 0}, "tools_used": 2},
    )
    assert r.status_code == 200

    r = client.post(f"/cycles/{cycle_id}/check", json={})
    cycle = r.json()
    assert cycle["proposed_change"] == ""

    # accept should be rejected — nothing to accept
    r = client.post(f"/cycles/{cycle_id}/act", json={"accept": True})
    assert r.status_code == 400

    # reject should still close the cycle cleanly
    r = client.post(f"/cycles/{cycle_id}/act", json={"accept": False, "note": "no evidence of waste"})
    assert r.status_code == 200
    assert r.json()["status"] == "closed"


def test_phase_order_enforced(client):
    r = client.post("/systems", json={"name": "phase-order-test"})
    system_id = r.json()["id"]
    r = client.post(f"/systems/{system_id}/cycles", json={"job": "a job"})
    cycle_id = r.json()["id"]

    # Can't check before do
    r = client.post(f"/cycles/{cycle_id}/check", json={})
    assert r.status_code == 409

    # Can't act before check
    r = client.post(f"/cycles/{cycle_id}/act", json={"accept": True})
    assert r.status_code == 409


def test_chat_full_flow_via_commands(client):
    r = client.post("/chat", json={"message": "new system: chat-flow-system", "channel": "web"})
    assert r.status_code == 200
    body = r.json()
    system_id = body["system_id"]
    assert system_id

    r = client.post("/chat", json={"system_id": system_id, "message": "new cycle: write the changelog"})
    assert r.status_code == 200
    assert r.json()["phase"] == "plan"

    r = client.post(
        "/chat",
        json={
            "system_id": system_id,
            # tool_calls stays within the default tool_budget (8) so this
            # exercises waste detection, not the budget-overrun rule.
            "message": "done 6 45 1 notes: had to context switch between docs",
        },
    )
    assert r.status_code == 200
    assert r.json()["phase"] == "do"

    r = client.post("/chat", json={"system_id": system_id, "message": "check"})
    assert r.status_code == 200
    assert r.json()["phase"] == "check"

    r = client.post("/chat", json={"system_id": system_id, "message": "accept"})
    assert r.status_code == 200
    assert r.json()["phase"] == "closed"


def test_chat_help_and_unknown(client):
    r = client.post("/chat", json={"message": "help"})
    assert r.status_code == 200
    assert "commands" in r.json()["reply"].lower()

    r = client.post("/chat", json={"message": "asdkjaksjd nonsense"})
    assert r.status_code == 200


def test_webhook_generic(client):
    r = client.post("/webhook", json={"message": "help", "sender": "tester"})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert "commands" in body["reply"].lower()


def test_webhook_powerlobster_shape(client):
    r = client.post(
        "/webhook",
        headers={"X-PowerLobster-Event": "message.created"},
        json={
            "id": "evt_1",
            "sender_handle": "@someone",
            "sender_name": "Someone",
            "content": "new system: powerlobster-system",
            "created_at": "2026-01-01T00:00:00Z",
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True


def test_webhook_powerlobster_bad_secret(monkeypatch, client):
    monkeypatch.setattr("app.webhooks.POWERLOBSTER_SECRET", "supersecret")
    r = client.post(
        "/webhook",
        headers={"X-PowerLobster-Event": "message.created", "X-PowerLobster-Secret": "wrong"},
        json={"content": "help"},
    )
    assert r.status_code == 401
