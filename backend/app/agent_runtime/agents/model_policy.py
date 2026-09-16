"""Agent model policy vocabulary and resolution.

``AgentReasoningEffort`` extends the model-client ``ReasoningEffort`` with the
agent-only ``inherit`` policy. Policy values describe what an agent *wants*;
they are resolved against a base model config before a ``ModelConfig`` is
built, so neither ``inherit`` nor ``off`` ever reaches a provider SDK.

The special model references below are stored verbatim in
``agent_definitions.model_id``, so their exact strings are a persisted contract:
changing one would silently repoint every existing agent definition.
"""

from collections.abc import Mapping
from typing import Any, Final, Literal


AgentReasoningEffort = Literal[
    "inherit",
    "off",
    "low",
    "medium",
    "high",
    "xhigh",
    "max",
]

DEFAULT_AGENT_REASONING_EFFORT: Final[AgentReasoningEffort] = "inherit"

AGENT_REASONING_EFFORT_TIERS: Final[frozenset[str]] = frozenset(
    {"low", "medium", "high", "xhigh", "max"}
)

SYSTEM_DEFAULT_MODEL_REFERENCE: Final[str] = "__system_default_model__"
SYSTEM_LIGHT_MODEL_REFERENCE: Final[str] = "__system_light_model__"


def apply_agent_reasoning_policy(
    base_config: Mapping[str, Any],
    policy: AgentReasoningEffort,
) -> dict[str, Any]:
    """Resolve an agent reasoning-effort policy against a base model config.

    Returns a new mapping; the caller's ``base_config`` is never modified.

    - ``inherit`` keeps the base value as-is, which is how a subagent inherits
      its parent's effective effort.
    - ``off`` removes the key. Absence of ``reasoning_effort`` is the single
      representation of "thinking disabled" downstream, so it must delete an
      inherited ``high`` rather than leave it in place.
    - A fixed tier replaces whatever the base config carried.

    A base config may carry the literal ``"off"`` (the model client and
    ``model_factory`` both accept it as "disabled"). Every result is normalised
    so that case collapses to an absent key, keeping ``"off"`` out of the
    resolved config.

    An unrecognised policy is treated as ``inherit`` rather than rejected: a
    bad value can only come from corrupted data or a version mismatch, and
    degrading to the base config beats failing a whole session over one field.
    """
    resolved = dict(base_config)
    if resolved.get("reasoning_effort") == "off":
        resolved.pop("reasoning_effort", None)

    if policy == "off":
        resolved.pop("reasoning_effort", None)
    elif policy in AGENT_REASONING_EFFORT_TIERS:
        resolved["reasoning_effort"] = policy
    return resolved
