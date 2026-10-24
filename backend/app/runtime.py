"""One bounded banking-assistant run, shared by future chat and assessments."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from typing_extensions import TypedDict

from .evidence import EvidenceError, EvidenceRecorder, canonical_json, read_events
from .runtime_context import assistant_instructions
from .target_contracts import RequestContext, TargetConfig
from .targets import resolve_target

MAX_MODEL_CALLS = 6
MAX_TOOL_CALLS = 4
MAX_INPUT_UNITS = 12000


@dataclass(frozen=True)
class VerifiedHistory:
    """Built by the server's persisted-turn loader, never accepted over HTTP."""

    messages: tuple[BaseMessage, ...] = ()
    tool_results: tuple[dict, ...] = ()
    record_ids: tuple[str, ...] = ()


def message_units(messages, tool_schemas) -> int:
    # UTF-8 bytes plus framing is a conservative offline input bound. T09's
    # provider adapter must also enforce its model-specific token accounting.
    return (
        len(canonical_json([m.model_dump(mode="json") for m in messages]).encode())
        + len(canonical_json(tool_schemas).encode())
        + 256
    )


class AssistantResponse(BaseModel):
    """Validated customer-facing text; T18 adds separately validated actions."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: Literal["answer", "refusal"]
    display_text: str = Field(min_length=1, max_length=4000)
    transaction_ids: tuple[str, ...] = Field(default=(), max_length=20)


class AssistantRun(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    run_id: UUID
    status: Literal["answered", "refused", "inconclusive", "error"]
    response: AssistantResponse | None = None
    reason_code: str | None = None
    model_calls: int = 0
    tool_calls: int = 0
    turn_messages: tuple[BaseMessage, ...] = Field(default=(), exclude=True)


class AssistantState(TypedDict):
    messages: list[BaseMessage]
    model_calls: int
    tool_calls: int
    pending_calls: list[dict]
    prepared_results: list[dict]
    seen_call_ids: list[str]
    seen_record_ids: list[str]
    last_model_event_id: UUID | None
    status: str | None
    response: AssistantResponse | None
    reason_code: str | None


def _message_digest(message: BaseMessage) -> str:
    payload = message.model_dump(mode="json")
    return hashlib.sha256(canonical_json(payload).encode()).hexdigest()


def run_assistant(
    *,
    config: TargetConfig,
    context: RequestContext,
    model: BaseChatModel,
    user_message: str,
    history: tuple[BaseMessage, ...] = (),
    memo_overlay_run_id: str | None = None,
    recorder: EvidenceRecorder | None = None,
    verified_history: VerifiedHistory | None = None,
) -> AssistantRun:
    """Execute one turn. Trusted context stays outside model-writable graph state.

    Callers inject a fixture or brokered live adapter. This function never
    constructs a provider client or silently falls back to scripted answers.
    """
    if not user_message.strip() or len(user_message) > 4000:
        raise ValueError("User message must contain 1–4000 characters")
    from .live_model import MeteredOpenAIModel

    if not isinstance(model, (FakeMessagesListChatModel, MeteredOpenAIModel)):
        raise TypeError("Only offline fixture or brokered live models are supported")
    if isinstance(model, MeteredOpenAIModel):
        if recorder is not model.recorder or model.config != config.model:
            raise ValueError(
                "Live model must use the execution recorder and resolved model settings"
            )
        if (
            read_events(recorder.run_id, limit=1, session_factory=recorder.session_factory).origin
            != "live_execution"
        ):
            raise ValueError("Live model requires live execution evidence")
    if len(history) > 50 or any(
        isinstance(item, (SystemMessage, ToolMessage))
        or (isinstance(item, AIMessage) and (item.tool_calls or item.invalid_tool_calls))
        for item in history
    ):
        raise ValueError("History cannot contain system or unverified tool messages")
    if verified_history is not None:
        if history:
            raise ValueError("Use one history source")
        history = verified_history.messages

    recorder = recorder or EvidenceRecorder.start(origin="fixture_execution")
    target = resolve_target(
        config,
        context=context,
        evidence=recorder,
        run_id=memo_overlay_run_id,
    )
    tools = {tool.name: tool for tool in target.bank_profile.tools}
    schemas = [
        {
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.get_input_schema().model_json_schema(),
        }
        for tool in tools.values()
    ]
    requested = recorder.append(
        "request.received",
        "user",
        {"user_message": user_message, "target_digest": target.manifest().target_digest},
        provenance={"user_message": "authenticated_user", "target_digest": "system_trusted"},
    )
    try:
        bound_model = model.bind_tools(target.bank_profile.tools)
    except EvidenceError:
        raise
    except Exception as exc:  # noqa: BLE001 - model binding is an external boundary
        recorder.append(
            "assistant.output",
            "application",
            {
                "status": "error",
                "reason_code": "model_bind_failed",
                "error_type": type(exc).__name__,
            },
            parent_event_id=requested.event_id,
        )
        return AssistantRun(run_id=recorder.run_id, status="error", reason_code="model_bind_failed")

    def model_node(state: AssistantState) -> dict:
        if state["status"] is not None:
            return {}
        if state["model_calls"] >= MAX_MODEL_CALLS:
            return {"status": "inconclusive", "reason_code": "model_call_limit"}

        messages = state["messages"]
        if message_units(messages, schemas) > MAX_INPUT_UNITS:
            return {"status": "inconclusive", "reason_code": "context_limit"}
        context_event = recorder.append(
            "context.request_prepared"
            if isinstance(model, MeteredOpenAIModel)
            else "context.dispatched",
            "application",
            {
                "message_count": len(messages),
                "message_digests": [_message_digest(message) for message in messages],
                "tool_results": [
                    *(verified_history.tool_results if verified_history else ()),
                    *state["prepared_results"],
                ],
            },
            parent_event_id=requested.event_id,
        )
        call_event = recorder.append(
            "model.requested",
            "application",
            {"call_number": state["model_calls"] + 1},
            parent_event_id=context_event.event_id,
        )
        if isinstance(model, MeteredOpenAIModel):
            # Budget/serialization rejection must never claim data left the process.
            bound_model.on_dispatch = lambda: recorder.append(
                "context.dispatched",
                "application",
                context_event.payload,
                parent_event_id=call_event.event_id,
            )
        try:
            response = bound_model.invoke(messages)
        except EvidenceError:
            raise
        except Exception as exc:  # noqa: BLE001 - provider failures become honest errors
            recorder.append(
                "model.responded",
                "model",
                {"outcome": "error", "error_type": type(exc).__name__},
                parent_event_id=call_event.event_id,
            )
            return {
                "model_calls": state["model_calls"] + 1,
                "status": "error",
                "reason_code": getattr(model, "last_failure_reason", "model_call_failed"),
                "prepared_results": [],
            }
        if not isinstance(response, AIMessage):
            recorder.append(
                "model.responded",
                "model",
                {"outcome": "invalid_message", "message_type": type(response).__name__},
                parent_event_id=call_event.event_id,
            )
            return {
                "model_calls": state["model_calls"] + 1,
                "status": "inconclusive",
                "reason_code": "invalid_model_message",
                "prepared_results": [],
            }

        proposed = recorder.append(
            "model.responded",
            "model",
            {
                "outcome": "tool_calls" if response.tool_calls else "final",
                "tool_calls": [
                    {"id": call.get("id"), "name": call.get("name"), "args": call.get("args")}
                    for call in response.tool_calls
                ],
                "content_digest": _message_digest(response),
            },
            provenance={"tool_calls": "model_generated"},
            parent_event_id=call_event.event_id,
        )
        update = {
            "messages": [*messages, response],
            "model_calls": state["model_calls"] + 1,
            "prepared_results": [],
            "last_model_event_id": proposed.event_id,
        }
        if response.invalid_tool_calls:
            return {**update, "status": "inconclusive", "reason_code": "invalid_tool_call"}
        if response.response_metadata.get("status") in ("incomplete", "failed", "cancelled"):
            return {**update, "status": "inconclusive", "reason_code": "provider_incomplete"}
        if response.additional_kwargs.get("refusal") or (
            isinstance(response.content, list)
            and any(isinstance(b, dict) and b.get("type") == "refusal" for b in response.content)
        ):
            return {**update, "status": "refused", "reason_code": "provider_refusal"}
        if response.tool_calls:
            if state["tool_calls"] + len(response.tool_calls) > MAX_TOOL_CALLS:
                return {**update, "status": "inconclusive", "reason_code": "tool_call_limit"}
            calls = [dict(call) for call in response.tool_calls]
            ids = [call.get("id") for call in calls]
            if (
                any(not call_id for call_id in ids)
                or len(ids) != len(set(ids))
                or any(call_id in state["seen_call_ids"] for call_id in ids)
            ):
                return {**update, "status": "inconclusive", "reason_code": "invalid_tool_call_id"}
            if any(
                not call.get("name") or not isinstance(call.get("args"), dict) for call in calls
            ):
                return {**update, "status": "inconclusive", "reason_code": "invalid_tool_call"}
            return {
                **update,
                "pending_calls": calls,
                "seen_call_ids": [*state["seen_call_ids"], *ids],
            }
        if not isinstance(response.content, (str, list)):
            return {**update, "status": "inconclusive", "reason_code": "malformed_final_response"}
        try:
            parsed = AssistantResponse.model_validate_json(response.text)
        except ValidationError:
            return {**update, "status": "inconclusive", "reason_code": "malformed_final_response"}
        if any(record_id not in state["seen_record_ids"] for record_id in parsed.transaction_ids):
            return {**update, "status": "inconclusive", "reason_code": "unsupported_citation"}
        return {
            **update,
            "response": parsed,
            "status": "answered" if parsed.kind == "answer" else "refused",
        }

    def tool_node(state: AssistantState) -> dict:
        messages = list(state["messages"])
        prepared_results = []
        seen_record_ids = list(state["seen_record_ids"])
        count = state["tool_calls"]
        for call in state["pending_calls"]:
            name, call_id, args = call["name"], call["id"], call["args"]
            tool = tools.get(name)
            if tool is None:
                recorder.append(
                    "tool.denied",
                    "application",
                    {"tool_name": name, "tool_call_id": call_id, "reason_code": "unknown_tool"},
                    parent_event_id=state["last_model_event_id"],
                )
                return {"status": "error", "reason_code": "unknown_tool", "tool_calls": count}
            before = recorder.last_sequence()
            try:
                with recorder.bind(parent_event_id=state["last_model_event_id"]):
                    result = tool.invoke(
                        {"type": "tool_call", "name": name, "id": call_id, "args": args}
                    )
            except EvidenceError:
                raise
            except Exception as exc:  # noqa: BLE001 - tool failures end the run honestly
                recorder.append(
                    "tool.failed",
                    "application",
                    {"tool_name": name, "tool_call_id": call_id, "error_type": type(exc).__name__},
                    parent_event_id=state["last_model_event_id"],
                )
                return {
                    "status": "error",
                    "reason_code": "tool_execution_failed",
                    "tool_calls": count + 1,
                }
            if not isinstance(result, ToolMessage):
                recorder.append(
                    "tool.failed",
                    "application",
                    {"tool_name": name, "tool_call_id": call_id, "error_type": "InvalidToolResult"},
                    parent_event_id=state["last_model_event_id"],
                )
                return {"status": "inconclusive", "reason_code": "invalid_tool_result"}
            observed_page = read_events(
                recorder.run_id,
                after_sequence=before,
                limit=1000,
                session_factory=recorder.session_factory,
            )
            if observed_page.has_more:
                raise EvidenceError("Tool evidence exceeded the bounded event page")
            observed = observed_page.events
            reads = [event for event in observed if event.name == "repository.read_completed"]
            seen_record_ids.extend(
                record_id for event in reads for record_id in event.payload.get("record_ids", [])
            )
            prepared = recorder.append(
                "context.prepared",
                "application",
                {
                    "tool_call_id": call_id,
                    "tool_name": name,
                    "tool_result_digest": _message_digest(result),
                    "read_event_ids": [str(event.event_id) for event in reads],
                    "record_owners": [
                        owner for event in reads for owner in event.payload.get("record_owners", [])
                    ],
                },
                parent_event_id=state["last_model_event_id"],
            )
            messages.append(result)
            prepared_results.append(
                {
                    "tool_call_id": call_id,
                    "prepared_event_id": str(prepared.event_id),
                    "tool_result_digest": _message_digest(result),
                }
            )
            count += 1
        return {
            "messages": messages,
            "tool_calls": count,
            "pending_calls": [],
            "prepared_results": prepared_results,
            "seen_record_ids": seen_record_ids,
        }

    def route_after_model(state: AssistantState) -> str:
        return "tool" if state["status"] is None and state["pending_calls"] else END

    def route_after_tool(state: AssistantState) -> str:
        return "model" if state["status"] is None else END

    graph = StateGraph(AssistantState)
    graph.add_node("model", model_node)
    graph.add_node("tool", tool_node)
    graph.add_edge(START, "model")
    graph.add_conditional_edges("model", route_after_model, {"tool": "tool", END: END})
    graph.add_conditional_edges("tool", route_after_tool, {"model": "model", END: END})
    initial: AssistantState = {
        "messages": [
            SystemMessage(content=assistant_instructions(target.config)),
            *history,
            HumanMessage(content=user_message),
        ],
        "model_calls": 0,
        "tool_calls": 0,
        "pending_calls": [],
        "prepared_results": [],
        "seen_call_ids": [],
        "seen_record_ids": list(verified_history.record_ids) if verified_history else [],
        "last_model_event_id": None,
        "status": None,
        "response": None,
        "reason_code": None,
    }
    final = graph.compile().invoke(initial)
    status = final["status"] or "inconclusive"
    response = final["response"]
    recorder.append(
        "assistant.output",
        "application",
        {
            "status": status,
            "reason_code": final["reason_code"],
            "display_text": response.display_text if response else None,
            "transaction_ids": list(response.transaction_ids) if response else [],
            "turn_digest": hashlib.sha256(
                canonical_json(
                    [m.model_dump(mode="json") for m in final["messages"][1 + len(history) :]]
                ).encode()
            ).hexdigest(),
        },
        provenance={"display_text": "model_generated"} if response else {},
        parent_event_id=final["last_model_event_id"],
    )
    return AssistantRun(
        run_id=recorder.run_id,
        status=status,
        response=response,
        reason_code=final["reason_code"],
        model_calls=final["model_calls"],
        tool_calls=final["tool_calls"],
        turn_messages=tuple(final["messages"][1 + len(history) :])
        if status in ("answered", "refused")
        else (),
    )
