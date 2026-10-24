"""The only admitted live model: Responses through durable budget admission."""

import os
from uuid import uuid4

import httpx
import openai
from langchain_openai import ChatOpenAI

from .budget import CallPlan, NotDispatched, Usage, UsageBroker


def failure_reason(exc):
    """Classify safe public reasons; never persist provider messages or bodies."""
    # Some API errors put quota detail in the message but omit a machine code.
    message = str(getattr(exc, "message", "")).lower()
    if getattr(exc, "code", None) == "insufficient_quota" or any(
        phrase in message
        for phrase in (
            "insufficient_quota",
            "current quota",
            "billing quota",
            "run out of credits",
            "no balance left",
        )
    ):
        return "provider_quota_exhausted"
    if isinstance(exc, openai.RateLimitError):
        return "provider_rate_limited"
    if isinstance(exc, openai.AuthenticationError):
        return "provider_authentication_failed"
    if isinstance(exc, openai.PermissionDeniedError):
        return "provider_access_denied"
    if isinstance(exc, openai.NotFoundError):
        return "provider_model_unavailable"
    if isinstance(exc, openai.APITimeoutError):
        return "provider_timeout"
    if isinstance(exc, openai.APIConnectionError):
        return "provider_connection_failed"
    if isinstance(exc, openai.BadRequestError):
        return "provider_request_rejected"
    if isinstance(exc, NotDispatched):
        return "provider_not_dispatched"
    return "model_call_failed"


class BoundedTransport(httpx.BaseTransport):
    """Check the actual serialized wire body before the HTTP transport sees it."""

    def __init__(self, inner=None):
        self.inner = inner or httpx.HTTPTransport(retries=0)
        self.dispatched = False
        self.on_dispatch = lambda: None

    def handle_request(self, request):
        # UTF-8 bytes + protocol framing conservatively bound token count. Include
        # schemas, response format, messages and all envelope fields in this bound.
        if len(request.content) + 512 > 12000:
            raise NotDispatched("Serialized model context exceeds input allowance")
        if request.url.host != "api.openai.com" or request.url.path != "/v1/responses":
            raise NotDispatched("Only the configured Responses endpoint is supported")
        self.on_dispatch()
        self.dispatched = True
        return self.inner.handle_request(request)

    def close(self):
        self.inner.close()


def normalize_usage(message):
    data = message.usage_metadata
    if not data or "input_tokens" not in data or "output_tokens" not in data:
        raise ValueError("Provider usage unavailable; reservation retained")
    detail = data.get("input_token_details", {})
    # Unknown charged cache partitions must not silently be priced as ordinary input.
    known = {"cache_read", "cache_creation", "cache_creation_30m", "cache_creation_12h"}
    if any(value for key, value in detail.items() if key not in known):
        raise ValueError("Unsupported input usage partition")
    return Usage(
        input_tokens=data["input_tokens"],
        output_tokens=data["output_tokens"],
        cached_input_tokens=detail.get("cache_read", 0),
        cache_write_30m_tokens=detail.get("cache_creation_30m", detail.get("cache_creation", 0)),
        cache_write_12h_tokens=detail.get("cache_creation_12h", 0),
        reasoning_tokens=data.get("output_token_details", {}).get("reasoning", 0),
    )


class MeteredOpenAIModel:
    """Per-run adapter; model selection, limits and accounting stay in trusted code."""

    def __init__(self, config, recorder, *, broker=None, transport=None):
        if not os.getenv("OPENAI_API_KEY"):
            raise ValueError("OpenAI credentials unavailable")
        self.config, self.recorder = config, recorder
        self.broker = broker or UsageBroker()
        self.transport = BoundedTransport(transport)
        self.client = httpx.Client(transport=self.transport, timeout=config.timeout_seconds)
        self.model = ChatOpenAI(
            model=config.model,
            base_url="https://api.openai.com/v1",
            use_responses_api=True,
            use_previous_response_id=False,
            output_version="responses/v1",
            reasoning_effort=config.reasoning_effort,
            max_tokens=config.max_output_tokens,
            timeout=config.timeout_seconds,
            max_retries=0,
            store=False,
            service_tier="default",
            http_client=self.client,
            include_response_headers=True,
            include=["reasoning.encrypted_content"],
        )

    def bind_tools(self, tools):
        # Strict tool schemas disallow optional/default arguments in current tools.
        # Existing StrictBankTool remains the authority for argument validation.
        self.bound = self.model.bind_tools(
            tools,
            strict=False,
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": "bank_answer",
                    "strict": True,
                    "schema": {
                        "title": "bank_answer",
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "kind": {"type": "string", "enum": ["answer", "refusal"]},
                            "display_text": {"type": "string"},
                            "transaction_ids": {"type": "array", "items": {"type": "string"}},
                        },
                        "required": ["kind", "display_text", "transaction_ids"],
                    },
                },
            },
            parallel_tool_calls=False,
        )
        return self

    def invoke(self, messages):
        plan = CallPlan(
            charge_id=uuid4(),
            run_id=self.recorder.run_id,
            role="target",
            max_output_tokens=self.config.max_output_tokens,
        )
        self.transport.dispatched = False
        self.transport.on_dispatch = getattr(self, "on_dispatch", lambda: None)

        def operation(_):
            try:
                message = self.bound.invoke(messages)
            except Exception as exc:
                if not self.transport.dispatched:
                    raise NotDispatched("Provider request was not dispatched") from exc
                raise
            usage = normalize_usage(message)
            headers = message.response_metadata.get("headers", {})
            return message, usage, headers.get("x-request-id", message.id)

        try:
            message = self.broker.execute(plan, operation)
        except Exception as exc:
            self.last_failure_reason = failure_reason(exc)
            self.recorder.append(
                "model.usage",
                "application",
                {
                    "charge_id": str(plan.charge_id),
                    "outcome": "error",
                    "error_type": type(exc).__name__,
                    "reason_code": self.last_failure_reason,
                    "http_status": getattr(exc, "status_code", None),
                    "limit_kind": "tokens"
                    if "tokens per" in str(getattr(exc, "message", "")).lower()
                    else "requests"
                    if "requests per" in str(getattr(exc, "message", "")).lower()
                    else "unspecified",
                },
            )
            raise
        self.recorder.append(
            "model.usage",
            "application",
            {
                "charge_id": str(plan.charge_id),
                "outcome": "settled",
                "usage": normalize_usage(message).model_dump(),
                "provider_response_id": message.id,
                "model": message.response_metadata.get("model_name"),
                "status": message.response_metadata.get("status"),
            },
        )
        # Keep Responses blocks in stored history: function/reasoning IDs must
        # round-trip. Runtime extracts the text only at final validation.
        return message

    def close(self):
        self.client.close()


def live_status():
    budget = UsageBroker().summary()
    reason = None
    if not os.getenv("OPENAI_API_KEY"):
        reason = "OpenAI key is not configured on the server"
    elif not budget["initialized"]:
        reason = "Initialize project accounting with prior API spend first"
    elif budget["halted"] or (budget["remaining_micro_usd"] or 0) < 24000:
        reason = "Project budget cannot cover another chat turn"
    return {"available": reason is None, "reason": reason, "max_turn_micro_usd": 24000}
