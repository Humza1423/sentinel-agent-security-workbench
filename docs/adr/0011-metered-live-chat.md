# ADR 0011 — Metered Responses in the existing assistant loop

Keep the explicit LangGraph model/tool loop and introduce one admitted live adapter.
Replacing it with a separate provider agent would duplicate tools, history and
evidence. A plain ChatOpenAI injection would bypass the mandatory usage broker.
The adapter uses LangChain's Responses serialization, existing typed tools, and
strict JSON output with local Pydantic validation. Tool schemas retain optional
arguments and use strict=False; trusted Python validates each tool invocation.

Each call reserves its own maximum before dispatch; retries are disabled. Unknown
billing retains liability. The transport checks the actual UTF-8 wire envelope
against the conservative 12,000 input allowance before network access. This is
deliberately stricter than using the full context window. Output is capped at
2,000 tokens. No built-in paid tools, previous response IDs or provider storage
are used. Encrypted reasoning content round-trips in bounded, verified history.

The recorded synthetic reference date is part of the actual system instructions
and target manifest digest. Session mode persists and cannot silently switch.
Live calls require the runtime and adapter to share one live evidence recorder.
Context preparation and actual HTTP dispatch have separate events; denied budget
admission must not create false context-disclosure evidence.

Pricing was rechecked against the official [model page](https://developers.openai.com/api/docs/models/gpt-6-luna)
and [pricing](https://developers.openai.com/api/docs/pricing) on October 24, 2026.
The T08 price card remains applicable. The six-call maximum is US$0.024 per turn.
Key loading uses the existing explicit private OPENAI_ENV_FILE mechanism.

This ticket assesses chat usefulness. It does not generate attacks or claim a
security repair. The selected synthetic lab profile remains deliberately unsafe;
protected reference tools remain a separate control.
