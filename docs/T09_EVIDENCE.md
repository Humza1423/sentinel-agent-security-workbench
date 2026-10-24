# T09 — Live banking conversation

Status: implemented; live acceptance blocked by OpenAI API quota.

Local verification: 163 backend tests and eight frontend tests pass, plus Ruff lint
and formatting, additive migration upgrade/schema check, and production frontend
build pass. Independent read-only review found and verified fixes for manifest
coverage, actual-dispatch evidence and consistent live run identity.
The PR stays draft until live gates pass.

## Observed live attempt, October 24, 2026

The user confirmed zero prior spend and the project ledger was initialized once
with US$0. Three single-request smoke attempts reached OpenAI and each returned
HTTP 429 before any tool execution or banking answer. The third classified the
provider's “current quota” message as `provider_quota_exhausted`; the first two
retained the generic rate-limit class. No human suite was run after this finding.

Confirmed usage is US$0. The ledger conservatively retains US$0.012 (three
US$0.004 reservations) because no provider token usage was returned. This is
reserved exposure, not a claim that the provider billed that amount. The original
failures remain persisted; the ledger was not reset or silently reconciled.

Provider errors now distinguish exhausted quota from temporary rate limiting,
show clear UI guidance, and retain only safe classifications/status rather than
raw provider messages. The smoke command exits nonzero on failed/incomplete
trials; no-oracle cases require manual review rather than receiving a free pass.

Live typo handling, follow-ups and model-generated answers remain **unverified**.
The observed gap is API availability. An API billing balance or project quota
must be available before these trials can resume. Creating a new key is not an
established remedy for this error. The local US$45 ceiling does not fund the
OpenAI account. See [API billing](https://platform.openai.com/settings/organization/billing).

Previously a keyword fixture selected tool arguments. The new live adapter lets
GPT-6 Luna interpret wording, typos and follow-ups through the same runtime.
The offline fixture is still available and still has limited language behavior.

Flow: session + verified history → shared system context → model proposes tool →
Python validates/authorizes → snapshot query → recorded result → model → validated
JSON answer. LangChain handles provider messages and tools; LangGraph controls
the bounded loop; Python owns identity, storage, permissions and accounting.

Important files:

- `app/live_model.py`: budgeted Responses adapter, actual wire bounds and usage.
- `app/runtime_context.py`: fixed-date banking semantics shared with history sizing.
- `app/runtime.py`: admits the metered adapter, handles blocks/refusals/incomplete output.
- `app/chat.py`: persisted execution mode and shared chat invocation.
- `scripts/live_agent_smoke.py`: explicit smoke or human-style usefulness suite.

Deterministic tests exercise actual LangChain Responses serialization over mocked
HTTP, then execute real banking tools. They cover usage, errors, refusals/limits,
budget rejection and evidence. They cannot establish live language quality.

Live evaluation procedure (from backend, with the private key file selected):

```sh
uv run python -m scripts.live_agent_smoke --live --suite smoke --output /tmp/sentinel-t09-smoke.json
uv run python -m scripts.live_agent_smoke --live --suite human --output /tmp/sentinel-t09-human.json
```

Inspect the first smoke result before running the human suite. The latter covers
13 questions including the original typo, date-relative totals, follow-ups,
period comparison, empty results and unavailable operations. Its conservative
maximum is US$0.312. Compare money and IDs against independent fixture oracles;
manually inspect ambiguity/no-data/refusal responses. Save all outcomes including
failures. Reports contain observed results, selected tools and ledger totals.

Known capability gaps to investigate before the attack phase:

- Merchant and amount filters lack dedicated tool arguments.
- Large result lists may exceed the conservative context allowance.
- Tool validation failures currently end the turn rather than allowing correction.
- Account balances, money movement and exchange rates are unavailable.
- Citation validation does not establish that every numeric claim is correct.

Interview explanation: “I connected live language interpretation to the same
bounded banking runtime used by fixtures, with real authorization and evidence.
Every call reserves budget first. I evaluate ordinary questions against fixed
bank facts, and retain failed trials rather than presenting scripted success.”
