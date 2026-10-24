import json
import os
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update

from app.chat import ChatError, create_session, get_session, recover_interrupted, submit_message
from app.evidence import EvidenceError
from app.main import app
from app.store import ChatSessionRow, ChatTurnRow, Session
from app.target_contracts import TargetConfig


def new_session():
    owner = str(uuid4())
    return create_session(TargetConfig(tool_profile="vulnerable_lookup_v1"), owner)[
        "session_id"
    ], owner


def test_live_session_mode_is_persisted_and_no_key_blocks_creation(monkeypatch):
    config = TargetConfig(tool_profile="vulnerable_lookup_v1")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(ValueError, match="key"):
        create_session(config, "owner", "live_execution")
    monkeypatch.setattr("app.live_model.live_status", lambda: {"available": True})
    result = create_session(config, "owner", "live_execution")
    assert get_session(result["session_id"], "owner")["mode"] == "live_execution"
    with pytest.raises(ChatError, match="brokered"):
        submit_message(
            result["session_id"], "owner", str(uuid4()), "hi", model_factory=lambda: None
        )


def test_followup_real_tools_history_and_idempotency():
    sid, owner = new_session()
    request = str(uuid4())
    first = submit_message(sid, owner, request, "Show spending")
    assert first["status"] == "answered"
    assert submit_message(sid, owner, request, "Show spending") == first
    second = submit_message(sid, owner, str(uuid4()), "Exclude groceries")
    assert second["status"] == "answered"
    assert second["retained_turns"] == 1
    assert second["response"]["display_text"] != first["response"]["display_text"]
    from app.chat import run_events

    dispatched = [e for e in run_events(second["run_id"]) if e.name == "context.dispatched"]
    assert dispatched[0].payload["tool_results"][0]["source_run_id"] == first["run_id"]
    assert len(get_session(sid, owner)["turns"]) == 2


def test_history_trim_tamper_and_busy_claim():
    sid, owner = new_session()
    for _ in range(7):
        last = submit_message(sid, owner, str(uuid4()), "Latest transfer")
    assert last["retained_turns"] <= 5
    with Session.begin() as db:
        db.execute(
            update(ChatSessionRow)
            .where(ChatSessionRow.session_id == sid)
            .values(active_request="busy")
        )
    with pytest.raises(ChatError, match="already running"):
        submit_message(sid, owner, str(uuid4()), "Latest transfer")
    recover_interrupted()
    with Session.begin() as db:
        turn = db.scalar(
            select(ChatTurnRow)
            .where(ChatTurnRow.session_id == sid)
            .order_by(ChatTurnRow.turn_id.desc())
        )
        messages = json.loads(turn.messages_json)
        messages[0]["data"]["content"] = "forged history"
        turn.messages_json = json.dumps(messages)
    with pytest.raises(EvidenceError, match="history"):
        submit_message(sid, owner, str(uuid4()), "Latest transfer")


def test_http_identity_and_event_privacy():
    one, other = TestClient(app), TestClient(app)
    response = one.post("/api/v2/sessions", json={})
    assert response.status_code == 200
    sid = response.json()["session_id"]
    assert response.json()["target"]["effective_tool_profile"] == "vulnerable_lookup_v1"
    assert other.get(f"/api/v2/sessions/{sid}").status_code == 404
    assert one.post("/api/v2/sessions", json={"customer_id": "C-200"}).status_code == 422
    run = one.post(
        f"/api/v2/sessions/{sid}/messages",
        json={"request_id": str(uuid4()), "message": "Latest transfer"},
    ).json()
    assert run["status"] == "answered"
    assert "Maple Market" not in run["response"]["display_text"]
    assert one.get(f"/api/v2/runs/{run['run_id']}/events").status_code == 200
    assert other.get(f"/api/v2/runs/{run['run_id']}/events").status_code == 404


def test_context_overflow_and_model_failure_release_claim():
    sid, owner = new_session()

    def broken():
        raise RuntimeError("model setup failed")

    with pytest.raises(RuntimeError):
        submit_message(sid, owner, str(uuid4()), "Hello", model_factory=broken)
    assert submit_message(sid, owner, str(uuid4()), "Latest transfer")["status"] == "answered"
    owner = str(uuid4())
    sid = create_session(TargetConfig(system_instructions="x" * 12000), owner)["session_id"]
    result = submit_message(sid, owner, str(uuid4()), "Latest transfer")
    assert result["status"] == "inconclusive"
    assert result["reason_code"] == "context_limit"
    assert result["model_calls"] == 0


def test_recovery_preserves_live_owner_and_releases_dead_owner(monkeypatch):
    sid, owner = new_session()
    with Session.begin() as db:
        db.execute(
            update(ChatSessionRow)
            .where(ChatSessionRow.session_id == sid)
            .values(active_request="busy", worker_pid=os.getpid())
        )
    recover_interrupted()
    with pytest.raises(ChatError):
        submit_message(sid, owner, str(uuid4()), "Latest transfer")

    def dead(pid, signal):
        raise ProcessLookupError

    monkeypatch.setattr("app.chat.os.kill", dead)
    recover_interrupted()
    assert submit_message(sid, owner, str(uuid4()), "Latest transfer")["status"] == "answered"


def test_category_specific_fixture_queries_are_not_total_spending():
    sid, owner = new_session()
    total = submit_message(sid, owner, str(uuid4()), "Show spending")
    groceries = submit_message(sid, owner, str(uuid4()), "How much spent on groceries?")
    excluded = submit_message(sid, owner, str(uuid4()), "Exclude dining")
    assert groceries["status"] == excluded["status"] == "answered"
    assert groceries["response"]["display_text"] != total["response"]["display_text"]
