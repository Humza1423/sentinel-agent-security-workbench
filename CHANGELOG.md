# Changelog

## T09 — Metered live banking conversation (live validation pending)

- Added the brokered LangChain Responses adapter and explicit live/fixture sessions.
- Included fixed-date banking context in executable target manifests.
- Kept dispatch evidence accurate on budget and wire-limit rejection.
- Added a repeatable opt-in chat evaluation suite; prior-spend initialization gates paid runs.
- Live smoke exposed exhausted API quota; added safe failure classification, clear UI guidance,
  and nonzero evaluation exits. Live language-quality acceptance remains pending.

## T08 — Durable API budget controls

- Added atomic project/assessment admission, versioned pricing and usage settlement.
- Retained uncertain reservations across restart and halted admission on adapter overruns.
- Exposed accounting/estimates in the workspace and disabled the former unmetered smoke path.

## T07 — Banking chat and security inspector

- Made chat the default workspace, with actual run events beside each answer.
- Preserved the visual direction and explicitly labeled the legacy scripted demo.
- Added same-origin API routing, retry handling and stale-response guards.

## T06 — Persistent banking conversations

- Added server-owned synthetic sessions, strict chat endpoints and private event access.
- Preserved verified tool/result history with bounded input, durable request claims
  and idempotent results. Restart recovery does not rerun interrupted work.
- Added an explicitly limited offline banking model over the shared runtime.

## T05 — Shared banking-assistant runtime

- Added a bounded two-node LangGraph assistant loop using LangChain messages,
  injected models and T04 tools, without a new paid-model path.
- Recorded model proposals, actual tool reads, prepared results, model-context
  dispatch and validated final output as separate observed events.
- Preserved trusted session authority, rejected unsupported citations and
  terminated failures/limits without scripted fallback. Unverified tool history
  and unmetered live models are rejected. Existing campaign and dashboard
  behavior remains labeled legacy.

## T04 — Executable banking tool boundary

- Added reviewed session-bound and vulnerable synthetic lab tool profiles with a
  small Python authorization gateway and correlated request, decision, scope and
  repository-result evidence.
- Exposed snapshot-backed read tools for transaction listing/detail, spending
  totals and period comparison; validated categories and preserved untrusted
  memo overlays.
- Removed the general repository vulnerability switch. Versioned the synthetic
  fixture as v2 for a unique second-customer marker, without changing legacy
  demo rows or existing stored snapshots.
- Clarified that the selected lab assistant is the later assessment target and
  a protected reference profile is never an agent-generated repair.

## T03 — Versioned synthetic bank snapshots

- Added a fixed-date, 120-transaction CAD snapshot with exact contents, a
  canonical digest, integer-cent amounts, and database immutability guards.
- Added customer-scoped transaction queries, spending totals, period comparisons,
  and run-specific memo overlays that retain `external_untrusted` provenance.
- Preserved the existing demo transaction table and campaign behavior. No model
  calls or external bank integrations were added.

## T02 — Observed evidence recorder

- Added typed observed events, canonical sanitized-payload digests, causal parents,
  atomic per-run ordering, separate persistence and read-only paginated retrieval.
- Instrumented actual tool requests/validation, permission decisions and repository
  reads, including denied, no-data, failure and async execution paths.
- Added secret redaction and normal SQL append-only protections; preserved legacy
  reports and all existing banking data through additive migration 0003.
- Added a temporary-bank, tool-only evidence demo with no model calls.
- Reorganized the authoritative plan into six product phases, kept ticket IDs,
  added Investigator/Defense/Coding Agent repair tickets and explicit execution
  order, and archived the original deterministic-MVP design.
- Chat, model runtime integration, agents and generated patch execution are not
  implemented in this ticket. Provider usage remains $0.

## Initial GitHub publication

- Published the existing deterministic banking security MVP and T01 together as
  the initial repository state; earlier development history is not reconstructed.
- Added strict resolved-target contracts, actual-tool manifests, and the local
  configuration-inspection endpoint in T01.
- Added a strict bank tool wrapper after tests exposed LangChain's zero-field
  input-validation fast path. Invalid identity arguments cause no repository read.

## GitHub workflow setup

- Added ticket branches, PR and issue templates, ownership, and contribution guidance.
- Added locked backend lint/format/tests and frontend test/type-check/build jobs.
- Added isolated, migrated test databases and blocked provider HTTP transports.
- Added the current roadmap and per-ticket learning/review instructions.
- Kept credentials, local data, caches, and generated artifacts out of version control.
