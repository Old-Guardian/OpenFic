"""Agent model policy vocabulary.

``AgentReasoningEffort`` extends the model-client ``ReasoningEffort`` with the
agent-only ``inherit`` policy. Policy values describe what an agent *wants*;
they are resolved before a ``ModelConfig`` is built, so neither ``inherit`` nor
``off`` ever reaches a provider SDK.
"""

from typing import Final, Literal


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
