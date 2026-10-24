# Sentinel implementation roadmap

This is the status tracker for the [authoritative implementation plan](../IMPLEMENTATION_PLAN.md).
The [original deterministic MVP plan](archive/DETERMINISTIC_MVP_PLAN.md) is historical.
Ticket numbers are identifiers, not strict execution order. GitHub history keeps the same IDs.

The product combines shared banking chat, attacks, evidence, investigation, a
Defense Agent's proposal, user-triggered isolated code repair, and verified replay.
Each approved ticket has its own tests, learning review, and PR to protected main.

| Ticket | Layer and outcome | Dependencies | Status |
| --- | --- | --- | --- |
| T01 | Resolved target contracts and actual-tool manifest | None | Complete; 47 focused tests |
| T02 | Runtime evidence recorder and versioned persistence | T01 | Complete |
| T03 | Scoped banking queries and isolated snapshots | T01–T02 | Complete |
| T04 | Executable tool profiles and policy gateway | T03 | Complete |
| T05 | Shared assistant subgraph with injectable fake model | T04 | Complete |
| T06 | Session and bounded multi-turn chat API | T05 | Complete |
| T07 | Banking chat beside a security inspector | T06 | Complete |
| T08 | Atomic budget reservations and durable usage ledger | T01–T02 | Complete; phase review |
| T09 | Live Responses adapter and explicit smoke check | T05, T08 | Implemented; live smoke blocked by API quota, PR remains draft |
| T10 | Cross-customer scorer driven by observed events | T02, T05 | Planned |
| T11 | Manifest-based surface analysis and bounded attacker | T01, T08–T10 | Planned |
| T12 | Clean paired execution against a candidate revision | T11, T13, T22 | Planned |
| T13 | Direct control probes and actual benign-agent queries | T03, T05, T10 | Planned |
| T14 | Assessment lifecycle, cancellation, persisted SSE | T12–T13 | Planned |
| T15 | Investigation, diff and evidence proof workspace | T07, T14, T21–T22, T25 | Planned |
| T16 | Repeated trials, regression export, guided demo | T15, T26 | Planned |
| T17 | Gateway-only versus session-bound comparison | T16 | Planned |
| T18 | Action provenance and enforceable exact-host URL policy | T05, T08 | Planned |
| T19 | Tailored phishing attacks and observed scoring | T11–T13, T18 | Planned |
| T20 | Phishing proof UI and both-family regression suite | T16, T19 | Planned |
| T21 | Investigator Agent and evidence-linked root cause | T03, T10–T11 | Planned |
| T22 | Defense Agent and structured RemediationPlan | T08, T21 | Planned |
| T23 | Isolated Git workspace and restricted execution | T14, T22, supported sandbox | Planned |
| T24 | Coding Agent and validated patch application | T08, T23 | Planned |
| T25 | Candidate security, benign and replay verification | T12–T13, T24 | Planned |
| T26 | Accept/reject/export and explicit PR actions | T15, T25 | Planned |

## Product phases

1. **Trustworthy foundation:** T01–T04.
2. **Working banking chatbot:** T05–T09.
3. **Attack and detection:** T10–T11.
4. **Investigation and autonomous repair:** T21–T26.
5. **Verification and polished product:** T12–T16.
6. **Phishing expansion:** T17–T20.

Phases group capabilities, not chronological work. Repair and verification interleave.

## Recommended execution order

```text
T01 → T02 → T03 → T04 → T05 → T06 → T07 → T08 → T09 → T10 → T11
→ T13 → T21 → T22 → T12 → T14 → T23 → T24 → T25 → T15 → T26 → T16
→ T17 → T18 → T19 → T20
```

## T02 evidence and review

GitHub tracking: [T02 — runtime evidence recorder](https://github.com/Humza1423/sentinel-agent-security-workbench/issues/1).

T02 adds observed tables alongside legacy stories, actual tool/repository hooks,
and a read-only paginated API. See [the T02 learning note](T02_EVIDENCE.md) and
[ADR 0004](adr/0004-observed-evidence-recorder.md). It implements no agents, chat,
patch execution, or generated repair UI. T03 followed it.

Acceptance gates:

- Events persist in order, and IDs remain unique across runs.
- Canonical payload digests verify.
- Credentials and sensitive provider errors are redacted before persistence.
- Partial failed runs retain recorded evidence.
- An Alembic migration preserves existing campaign reports.
- Focused tests cover successful, denied, failed, and partially completed traces.
- Default tests make no provider calls.

## T03 — scoped synthetic banking snapshots

T03 adds a fixed-date, 120-record CAD snapshot with exact contents and a canonical
digest, trusted-session queries, integer-cent calculations and per-run untrusted
memo overlays. Legacy demo rows and reports remain unchanged. See
[the T03 learning note](T03_EVIDENCE.md) and [ADR 0005](adr/0005-versioned-bank-snapshots.md).

Acceptance gates:

- Snapshot ID/version/reference date/digest and all transaction contents persist.
- Query scope comes from `RequestContext`; date/category/amount filters cannot
  broaden access to another customer.
- Independent totals and inclusive date-boundary tests pass using integer cents.
- Overlays retain `external_untrusted` provenance and cannot change the base
  snapshot or another run.
- The additive migration preserves existing reports, events and bank rows.
- Snapshot contents and overlays reject ordinary database update/delete.
- Backend/frontend checks make zero provider calls.

## T04 — executable tool boundary

GitHub tracking: [T04 — executable banking tool boundary](https://github.com/Humza1423/sentinel-agent-security-workbench/issues/7).

T04 exposes protected snapshot read tools and an explicitly vulnerable synthetic
lab lookup. The Python gateway records requested versus effective identity, while
ordinary repositories have no general vulnerability bypass. The generated
manifest describes each tool's real schema and context binding. See the
[T04 learning note](T04_EVIDENCE.md) and [ADR 0006](adr/0006-reviewed-tool-profiles-and-gateway.md).

The later assessment demo must chat with and attack the same selected lab target.
A protected profile is a separate reference/control test, never a generated fix.
The Coding Agent must patch an isolated lab copy, pass focused checks and clean
replay, and never label an unverified patch fixed. T04 creates no chat, agents,
live model calls or generated repair. T05 follows this boundary.

T05 compared an explicit assistant graph with LangChain `create_agent` using
a fake-model trace (ADR 0007). Before T09, reverify model pricing and finish
the cost broker. Start live evidence with one end-to-end trial; the normal
assessment may then use up to three attempts.

## T05 — observed assistant runtime

GitHub tracking: [T05 — shared assistant runtime](https://github.com/Humza1423/Sentinel--For-Ai-Assistant-Security-/issues/10).

T05 adds one bounded LangGraph model/tool loop using T04's real tools and gateway.
It records proposed calls, repository results, model-context dispatch and final
validated output in the T02 evidence ledger. The same entry point can later
serve chat, assessments and replay. See [the T05 learning note](T05_EVIDENCE.md)
and [ADR 0007](adr/0007-observed-assistant-graph.md). The comparison with
`create_agent` used fake tool traces; no provider calls were made. T06 adds the
session API; the current dashboard/campaign remain legacy scripted meanwhile.

## Non-negotiable proof rules

T08 adds durable usage admission and the workspace budget summary. See
[T08 learning note](T08_EVIDENCE.md) and [ADR 0010](adr/0010-durable-usage-broker.md).
Live integration remains T09; the legacy unmetered adapter is disabled.

T07 links chat answers to their persisted events. See [T07 learning note](T07_EVIDENCE.md)
and [ADR 0009](adr/0009-chat-evidence-workspace.md).

T06 adds persisted server-owned conversations and verified complete-turn history.
See [T06 learning note](T06_EVIDENCE.md) and [ADR 0008](adr/0008-persisted-chat-turns.md).
The HTTP chat remains an explicitly limited offline fixture until T09.

- Target definitions come from executable tools and enforced configuration.
- Trusted code controls authentication, authorization, and verdicts.
- Clean replay compares independently recorded consumed inputs, allowing only
  the registered defense's declared configuration/schema differences.
- Model resistance, direct-control success, and demonstrated repair remain
  separate conclusions. Timeouts and missing evidence cannot count as blocked.
- Approved URL configuration does not claim an active renderer before T18.
- Default CI and the recruiter demo are free; live runs are opt-in and budgeted.
- One approved ticket at a time, one documented PR, green checks, then main.
