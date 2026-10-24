"""Persisted synthetic sessions; only this service assembles trusted history."""

import hashlib
import json
import os
import secrets
from uuid import UUID, uuid4

from langchain_core.messages import (
    AIMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
    messages_from_dict,
    messages_to_dict,
)
from sqlalchemy import select, update

from .bank import ALICE_ID
from .evidence import EvidenceError, EvidenceRecorder, canonical_json, read_events
from .fixture_model import BankingFixtureModel
from .runtime import MAX_INPUT_UNITS, VerifiedHistory, message_units, run_assistant
from .runtime_context import assistant_instructions
from .store import ChatSessionRow, ChatTurnRow, Session
from .target_contracts import RequestContext, TargetConfig
from .targets import resolve_target


class ChatError(ValueError):
    pass


def owner_digest(owner):
    return hashlib.sha256(owner.encode()).hexdigest()


def require_session(db, session_id, owner):
    row = db.get(ChatSessionRow, session_id)
    if (
        row is None
        or not owner
        or not secrets.compare_digest(row.owner_digest, owner_digest(owner))
    ):
        raise LookupError("Session not found")
    return row


def create_session(config, owner, mode="fixture_execution"):
    if mode not in ("fixture_execution", "live_execution"):
        raise ValueError("Unknown execution mode")
    if mode == "live_execution":
        from .live_model import live_status

        status = live_status()
        if not status["available"]:
            raise ValueError(status["reason"])
    session_id = str(uuid4())
    context = RequestContext(
        session_id=session_id, authenticated_customer_id=ALICE_ID, request_id=str(uuid4())
    )
    manifest = resolve_target(config, context=context).manifest()
    with Session.begin() as db:
        db.add(
            ChatSessionRow(
                session_id=session_id,
                owner_digest=owner_digest(owner),
                customer_id=ALICE_ID,
                config_json=config.model_dump_json(),
                target_digest=manifest.target_digest,
                mode=mode,
            )
        )
    return get_session(session_id, owner)


def get_session(session_id, owner):
    with Session() as db:
        row = require_session(db, session_id, owner)
        turns = db.scalars(
            select(ChatTurnRow)
            .where(ChatTurnRow.session_id == session_id)
            .order_by(ChatTurnRow.turn_id)
        ).all()
        return {
            "session_id": session_id,
            "mode": row.mode,
            "customer_id": row.customer_id,
            "target": resolve_target(
                TargetConfig.model_validate_json(row.config_json),
                context=RequestContext(
                    session_id=session_id,
                    authenticated_customer_id=row.customer_id,
                    request_id="inspect",
                ),
            )
            .manifest()
            .model_dump(mode="json"),
            "turns": [
                {
                    "request_id": t.request_id,
                    "user_message": t.user_message,
                    "run_id": t.run_id,
                    "status": t.status,
                    "result": json.loads(t.result_json) if t.result_json else None,
                }
                for t in turns
            ],
        }


def run_events(run_id):
    events, after = [], 0
    while True:
        page = read_events(UUID(run_id), after_sequence=after, limit=1000)
        events.extend(page.events)
        if not page.has_more:
            return events
        after = page.next_after_sequence


def load_history(turns, config, context, user_message):
    groups = []
    for turn in turns[-5:]:
        messages = messages_from_dict(json.loads(turn.messages_json))
        events = run_events(turn.run_id)
        output = next((e for e in reversed(events) if e.name == "assistant.output"), None)
        digest = hashlib.sha256(
            canonical_json([m.model_dump(mode="json") for m in messages]).encode()
        ).hexdigest()
        if output is None or output.payload.get("turn_digest") != digest:
            raise EvidenceError("Stored chat history does not match execution evidence")
        if (
            not messages
            or not isinstance(messages[0], HumanMessage)
            or not isinstance(messages[-1], AIMessage)
        ):
            raise EvidenceError("Incomplete stored turn")
        pending = set()
        for message in messages:
            if isinstance(message, AIMessage):
                pending.update(c["id"] for c in message.tool_calls)
            elif isinstance(message, ToolMessage):
                if message.tool_call_id not in pending:
                    raise EvidenceError("Unpaired stored tool result")
                pending.remove(message.tool_call_id)
        if pending:
            raise EvidenceError("Incomplete stored tool calls")
        prepared = [
            {
                "source_run_id": turn.run_id,
                "prepared_event_id": str(e.event_id),
                "tool_call_id": e.payload["tool_call_id"],
                "tool_result_digest": e.payload["tool_result_digest"],
            }
            for e in events
            if e.name == "context.prepared"
        ]
        records = [
            record
            for e in events
            if e.name == "repository.read_completed"
            for record in e.payload.get("record_ids", [])
        ]
        groups.append((messages, prepared, records))
    target = resolve_target(config, context=context)
    schemas = [
        {
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.get_input_schema().model_json_schema(),
        }
        for tool in target.bank_profile.tools
    ]
    mandatory = [
        SystemMessage(content=assistant_instructions(config)),
        HumanMessage(content=user_message),
    ]
    while (
        groups
        and message_units([mandatory[0], *[m for g in groups for m in g[0]], mandatory[1]], schemas)
        > MAX_INPUT_UNITS
    ):
        groups.pop(0)
    return VerifiedHistory(
        tuple(m for g in groups for m in g[0]),
        tuple(r for g in groups for r in g[1]),
        tuple(r for g in groups for r in g[2]),
    ), len(groups)


def submit_message(session_id, owner, request_id, user_message, model_factory=None):
    recorder = None
    with Session.begin() as db:
        row = require_session(db, session_id, owner)
        prior = db.scalar(
            select(ChatTurnRow).where(
                ChatTurnRow.session_id == session_id, ChatTurnRow.request_id == request_id
            )
        )
        if prior:
            if prior.user_message != user_message:
                raise ChatError("Request ID already used for another message")
            if prior.result_json:
                return json.loads(prior.result_json)
            raise ChatError("Request is running or interrupted; start a new request explicitly")
        claimed = db.execute(
            update(ChatSessionRow)
            .where(ChatSessionRow.session_id == session_id, ChatSessionRow.active_request.is_(None))
            .values(active_request=request_id, worker_pid=os.getpid())
        ).rowcount
        if not claimed:
            raise ChatError("Another message is already running")
        config = TargetConfig.model_validate_json(row.config_json)
        mode = row.mode
        if mode == "live_execution" and model_factory is not None:
            raise ChatError("Live sessions require the brokered model")
        context = RequestContext(
            session_id=session_id, authenticated_customer_id=row.customer_id, request_id=request_id
        )
        if resolve_target(config, context=context).manifest().target_digest != row.target_digest:
            raise ChatError("Target runtime changed; create a new session")
        turns = db.scalars(
            select(ChatTurnRow)
            .where(
                ChatTurnRow.session_id == session_id,
                ChatTurnRow.status.in_(("answered", "refused")),
            )
            .order_by(ChatTurnRow.turn_id)
        ).all()
        history, retained = load_history(turns, config, context, user_message)
    # No write transaction spans model/tool execution.
    model = None
    try:
        recorder = EvidenceRecorder.start(origin=mode)
        with Session.begin() as db:
            db.add(
                ChatTurnRow(
                    session_id=session_id,
                    request_id=request_id,
                    run_id=str(recorder.run_id),
                    user_message=user_message,
                    status="running",
                )
            )
        if mode == "live_execution":
            from .live_model import MeteredOpenAIModel

            model = MeteredOpenAIModel(config.model, recorder)
        else:
            model = (model_factory or BankingFixtureModel)()
        result = run_assistant(
            config=config,
            context=context,
            model=model,
            user_message=user_message,
            verified_history=history,
            recorder=recorder,
        )
        payload = {
            **result.model_dump(mode="json"),
            "retained_turns": retained,
            "dropped_turns": len(turns) - retained,
            "mode": mode,
        }
        messages = json.dumps(messages_to_dict(list(result.turn_messages)))
        with Session.begin() as db:
            db.execute(
                update(ChatTurnRow)
                .where(ChatTurnRow.run_id == str(recorder.run_id))
                .values(
                    status=result.status, result_json=json.dumps(payload), messages_json=messages
                )
            )
    except Exception:
        with Session.begin() as db:
            if recorder is not None:
                db.execute(
                    update(ChatTurnRow)
                    .where(ChatTurnRow.run_id == str(recorder.run_id))
                    .values(status="error")
                )
        raise
    finally:
        if mode == "live_execution" and model is not None:
            model.close()
        with Session.begin() as db:
            db.execute(
                update(ChatSessionRow)
                .where(
                    ChatSessionRow.session_id == session_id,
                    ChatSessionRow.active_request == request_id,
                )
                .values(active_request=None, worker_pid=None)
            )
    return payload


def recover_interrupted():
    with Session.begin() as db:
        rows = db.scalars(
            select(ChatSessionRow).where(ChatSessionRow.active_request.is_not(None))
        ).all()
        for row in rows:
            if row.worker_pid is not None:
                try:
                    os.kill(row.worker_pid, 0)
                except ProcessLookupError:
                    pass
                except PermissionError:
                    continue
                else:
                    continue
            released = db.execute(
                update(ChatSessionRow)
                .where(
                    ChatSessionRow.session_id == row.session_id,
                    ChatSessionRow.active_request == row.active_request,
                    ChatSessionRow.worker_pid == row.worker_pid,
                )
                .values(active_request=None, worker_pid=None)
                .execution_options(synchronize_session=False)
            ).rowcount
            if not released:
                continue
            db.execute(
                update(ChatTurnRow)
                .where(
                    ChatTurnRow.session_id == row.session_id,
                    ChatTurnRow.request_id == row.active_request,
                    ChatTurnRow.status == "running",
                )
                .values(status="interrupted")
            )
