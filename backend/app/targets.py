"""Resolve reviewed configuration into tools; derive manifests from those exact tools."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from .assistant import BankToolProfile, make_bank_tool_profile
from .bank import BankRepository
from .defenses import REGISTRY
from .evidence import EvidenceRecorder
from .runtime_context import assistant_instructions
from .target_contracts import (
    CustomerAccessManifest,
    DefenseId,
    OutputActionManifest,
    RequestContext,
    TargetConfig,
    TargetManifest,
    ToolManifest,
)


@dataclass(frozen=True)
class ResolvedTarget:
    config: TargetConfig
    context: RequestContext
    bank_profile: BankToolProfile
    applied_defenses: tuple[DefenseId, ...]

    def manifest(self) -> TargetManifest:
        """Rebuild from actual schemas; never accept caller-supplied policy claims."""
        scoped = self.bank_profile.session_bound
        tools = tuple(
            ToolManifest(
                name=tool.name,
                description=tool.description,
                input_schema=tool.get_input_schema().model_json_schema(),
                model_controlled_arguments=tuple(sorted(tool.get_input_schema().model_fields)),
                trusted_context_bindings=("RequestContext.authenticated_customer_id",)
                if tool.name in self.bank_profile.trusted_bound_tool_names
                else (),
            )
            for tool in self.bank_profile.tools
        )
        manifest = TargetManifest(
            target_digest="",
            requested_config=self.config,
            runtime_output_contract=assistant_instructions(self.config),
            effective_tool_profile="session_bound_v1" if scoped else "vulnerable_lookup_v1",
            tools=tools,
            customer_access=CustomerAccessManifest(
                policy_preset="session_bound_customer_v1" if scoped else "vulnerable_lab_v1",
                identity_source="trusted_session" if scoped else "model_argument",
                repository_scope_enforced=scoped,
                enforcement_locations=("tool_closure", "repository_customer_filter")
                if scoped
                else (),
            ),
            applied_defenses=self.applied_defenses,
            output_actions=OutputActionManifest(approved_https_hosts=self.config.approved_domains),
        )
        payload = manifest.model_dump(mode="json", exclude={"target_digest"})
        digest = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
        ).hexdigest()
        return manifest.model_copy(update={"target_digest": digest})


def resolve_target(
    config: TargetConfig,
    *,
    context: RequestContext,
    repository: BankRepository | None = None,
    tool_events: list[dict[str, Any]] | None = None,
    evidence: EvidenceRecorder | None = None,
    run_id: str | None = None,
) -> ResolvedTarget:
    """No provider access, bank access, or tool execution.

    Context is supplied separately by trusted code. Policy selection can only choose
    reviewed factory branches; a defense cannot claim an unimplemented renderer.
    Supplying a recorder explicitly opts into persisting the resolution observation.
    """
    # Revalidate even if trusted Python callers used model_copy/model_construct.
    config = TargetConfig.model_validate_json(config.model_dump_json())
    context = RequestContext.model_validate_json(context.model_dump_json())
    scoped = config.tool_profile == "session_bound_v1"
    required_policy = "session_bound_customer_v1" if scoped else "vulnerable_lab_v1"
    if config.policy_preset not in (None, required_policy):
        raise ValueError("Policy preset is incompatible with the selected tool profile")
    for defense_id in config.defense_presets:
        if defense_id not in REGISTRY:
            raise ValueError("Unknown defense preset")
        if defense_id != "context_bound_customer_tools_v1":
            raise ValueError(
                f"Defense {defense_id} is registered but not executable by this target yet"
            )
        scoped = True
    profile = make_bank_tool_profile(
        defended=scoped,
        authenticated_customer_id=context.authenticated_customer_id,
        repository=repository,
        tool_events=tool_events,
        evidence=evidence,
        context=context,
        run_id=run_id,
    )
    resolved = ResolvedTarget(
        config=config,
        context=context,
        bank_profile=profile,
        applied_defenses=config.defense_presets,
    )
    if evidence is not None:
        evidence.append(
            "target.resolved",
            "target_factory",
            {
                "target_digest": resolved.manifest().target_digest,
                "tool_names": [tool.name for tool in profile.tools],
            },
            provenance={"target_digest": "system_trusted", "tool_names": "system_trusted"},
        )
    return resolved
