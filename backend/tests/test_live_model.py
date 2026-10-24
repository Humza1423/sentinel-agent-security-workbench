"""Real LangChain Responses serialization over a mock wire, never a paid client."""

import json
from uuid import uuid4

import httpx
import pytest
from langchain_core.messages import AIMessage
from sqlalchemy import select

from app.budget import UsageBroker
from app.evidence import EvidenceRecorder
from app.live_model import MeteredOpenAIModel, normalize_usage
from app.runtime import run_assistant
from app.store import UsageChargeRow
from app.target_contracts import RequestContext, TargetConfig


def response(output, *, status="completed", usage=True):
    data = {
        "id": f"resp_{uuid4().hex}",
        "object": "response",
        "created_at": 1792800000,
        "model": "gpt-6-luna",
        "status": status,
        "output": output,
        "parallel_tool_calls": False,
        "tool_choice": "auto",
        "tools": [],
        "usage": {
            "input_tokens": 100,
            "output_tokens": 20,
            "total_tokens": 120,
            "input_tokens_details": {"cached_tokens": 10},
            "output_tokens_details": {"reasoning_tokens": 5},
        }
        if usage
        else None,
    }
    return httpx.Response(200, json=data, headers={"x-request-id": "test-request"})


def final(text="Latest transfer is October 17, CAD 125.00."):
    return [
        {
            "id": "msg_1",
            "type": "message",
            "status": "completed",
            "role": "assistant",
            "content": [
                {
                    "type": "output_text",
                    "annotations": [],
                    "text": json.dumps(
                        {"kind": "answer", "display_text": text, "transaction_ids": ["S-0007"]}
                    ),
                }
            ],
        }
    ]


def setup_model(monkeypatch, broker, handler):
    monkeypatch.setenv("OPENAI_API_KEY", "test-placeholder")
    broker.initialize(0)
    config = TargetConfig(tool_profile="vulnerable_lookup_v1")
    recorder = EvidenceRecorder.start(origin="live_execution")
    model = MeteredOpenAIModel(
        config.model, recorder, broker=broker, transport=httpx.MockTransport(handler)
    )
    return config, recorder, model


@pytest.fixture
def broker(tmp_path):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.store import Base, BudgetAccountRow

    engine = create_engine(f"sqlite:///{tmp_path / 'ledger.db'}")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(engine, expire_on_commit=False)
    with sessions.begin() as db:
        db.add(BudgetAccountRow(account_id="sentinel", halted=0))
    yield UsageBroker(sessions)
    engine.dispose()


def execute(config, recorder, model, message="shwo latest trasnfer"):
    try:
        return run_assistant(
            config=config,
            recorder=recorder,
            model=model,
            context=RequestContext(
                session_id="test", authenticated_customer_id="C-100", request_id="test"
            ),
            user_message=message,
        )
    finally:
        model.close()


def test_responses_wire_real_tool_and_usage(monkeypatch, broker):
    requests = []

    def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        assert body["store"] is False
        assert body["max_output_tokens"] == 2000
        assert body["model"] == "gpt-6-luna"
        assert "previous_response_id" not in body
        assert "2026-10-23" in json.dumps(body)
        if len(requests) == 1:
            return response(
                [
                    {
                        "id": "fc_1",
                        "type": "function_call",
                        "call_id": "call_1",
                        "name": "list_my_transactions",
                        "arguments": '{"category":"transfer","limit":1}',
                    }
                ]
            )
        assert "S-0007" in json.dumps(body)
        return response(final())

    config, recorder, model = setup_model(monkeypatch, broker, handler)
    result = execute(config, recorder, model)
    from app.chat import run_events

    assert result.status == "answered", (
        result,
        requests,
        [e.model_dump() for e in run_events(str(recorder.run_id))],
    )
    assert result.response.transaction_ids == ("S-0007",)
    assert len(requests) == 2
    from app.chat import run_events

    assert len([e for e in run_events(str(recorder.run_id)) if e.name == "context.dispatched"]) == 2
    assert broker.summary()["reserved_micro_usd"] == 0
    assert broker.summary()["recorded_micro_usd"] > 0
    with broker.sessions() as db:
        charges = db.scalars(select(UsageChargeRow)).all()
        assert len(charges) == 2
        assert all(c.provider_request_id == "test-request" for c in charges)


@pytest.mark.parametrize("failure", ["missing_usage", "timeout", "malformed", "incomplete"])
def test_failures_never_fall_back_or_lose_billing(monkeypatch, broker, failure):
    calls = []

    def handler(request):
        calls.append(request)
        if failure == "timeout":
            raise httpx.ReadTimeout("private provider detail")
        output = final()
        if failure == "malformed":
            output[0]["content"][0]["text"] = "not json"
        return response(
            output,
            usage=failure != "missing_usage",
            status="incomplete" if failure == "incomplete" else "completed",
        )

    config, recorder, model = setup_model(monkeypatch, broker, handler)
    result = execute(config, recorder, model)
    assert result.status in ("error", "inconclusive")
    assert len(calls) == 1
    if failure in ("timeout", "missing_usage"):
        assert broker.summary()["reserved_micro_usd"] > 0
    else:
        assert broker.summary()["recorded_micro_usd"] > 0


def test_budget_denial_and_wire_limit_make_zero_requests(monkeypatch, broker):
    calls = []
    _config, _recorder, model = setup_model(monkeypatch, broker, lambda r: calls.append(r))
    model.bind_tools([])
    from langchain_core.messages import HumanMessage

    from app.budget import NotDispatched

    with pytest.raises(NotDispatched):
        model.invoke([HumanMessage(content="x" * 12000)])
    assert calls == []
    assert broker.summary()["reserved_micro_usd"] == 0
    model.close()


def test_cache_and_reasoning_normalization():
    message = AIMessage(
        content="",
        usage_metadata={
            "input_tokens": 100,
            "output_tokens": 20,
            "total_tokens": 120,
            "input_token_details": {"cache_read": 10},
            "output_token_details": {"reasoning": 5},
        },
    )
    usage = normalize_usage(message)
    assert usage.cached_input_tokens == 10
    assert usage.reasoning_tokens == 5


def test_live_recorder_and_config_cannot_be_mislabeled(monkeypatch, broker):
    config, _recorder, model = setup_model(monkeypatch, broker, lambda r: None)
    with pytest.raises(ValueError, match="execution recorder"):
        run_assistant(
            config=config,
            context=RequestContext(
                session_id="t", authenticated_customer_id="C-100", request_id="t"
            ),
            model=model,
            user_message="Hi",
        )
    model.close()


def test_budget_rejection_has_no_dispatched_context(monkeypatch, broker):
    config, recorder, model = setup_model(
        monkeypatch, broker, lambda r: pytest.fail("Must not dispatch")
    )
    from app.budget import BudgetError

    def rejected(plan):
        raise BudgetError("budget exhausted")

    monkeypatch.setattr(broker, "_reserve", rejected)
    result = execute(config, recorder, model)
    from app.chat import run_events

    events = run_events(str(recorder.run_id))
    assert result.status == "error"
    assert not any(e.name == "context.dispatched" for e in events)
    assert any(e.name == "context.request_prepared" for e in events)


@pytest.mark.parametrize(
    "message,code,expected",
    [
        (
            "You exceeded your current quota. private-provider-detail",
            None,
            "provider_quota_exhausted",
        ),
        ("Billing unavailable", "insufficient_quota", "provider_quota_exhausted"),
        ("Requests per minute limit reached", "rate_limit_exceeded", "provider_rate_limited"),
    ],
)
def test_quota_and_transient_limits_are_distinct_and_sanitized(
    monkeypatch, broker, message, code, expected
):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(
            429, json={"error": {"message": message, "type": "rate_limit_error", "code": code}}
        )

    config, recorder, model = setup_model(monkeypatch, broker, handler)
    result = execute(config, recorder, model)
    assert result.status == "error"
    assert result.reason_code == expected
    assert len(requests) == 1
    assert broker.summary()["reserved_micro_usd"] == 4000
    from app.chat import run_events

    serialized = json.dumps([e.model_dump(mode="json") for e in run_events(str(recorder.run_id))])
    assert "private-provider-detail" not in serialized
    assert "test-placeholder" not in serialized
