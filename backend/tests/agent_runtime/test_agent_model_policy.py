# -*- coding: utf-8 -*-
"""Agent 思考强度策略纯函数测试。"""

from typing import Any, cast

import pytest

from app.agent_runtime.agents.model_policy import (
    SYSTEM_DEFAULT_MODEL_REFERENCE,
    SYSTEM_LIGHT_MODEL_REFERENCE,
    AgentReasoningEffort,
    apply_agent_reasoning_policy,
)


BASE_WITHOUT_EFFORT: dict[str, Any] = {
    "provider_type": "openai",
    "model_id": "model-parent",
    "max_context_tokens": 8000,
}
BASE_WITH_HIGH: dict[str, Any] = {**BASE_WITHOUT_EFFORT, "reasoning_effort": "high"}
ALL_POLICIES: tuple[str, ...] = (
    "inherit",
    "off",
    "low",
    "medium",
    "high",
    "xhigh",
    "max",
)


@pytest.mark.parametrize(
    ("base_config", "policy", "expected_effort"),
    [
        (BASE_WITHOUT_EFFORT, "inherit", None),
        (BASE_WITH_HIGH, "inherit", "high"),
        (BASE_WITH_HIGH, "off", None),
        (BASE_WITHOUT_EFFORT, "off", None),
        (BASE_WITH_HIGH, "low", "low"),
        ({"reasoning_effort": "off"}, "max", "max"),
        ({"reasoning_effort": "off"}, "inherit", None),
    ],
)
def test_apply_agent_reasoning_policy_decision_table(
    base_config: dict[str, Any],
    policy: AgentReasoningEffort,
    expected_effort: str | None,
) -> None:
    resolved = apply_agent_reasoning_policy(base_config, policy)

    if expected_effort is None:
        assert "reasoning_effort" not in resolved
    else:
        assert resolved["reasoning_effort"] == expected_effort

    for key, value in base_config.items():
        if key != "reasoning_effort":
            assert resolved[key] == value


def test_inherit_returns_a_copy_of_the_base_config() -> None:
    resolved = apply_agent_reasoning_policy(BASE_WITH_HIGH, "inherit")

    assert resolved == BASE_WITH_HIGH
    assert resolved is not BASE_WITH_HIGH


@pytest.mark.parametrize("policy", ALL_POLICIES)
def test_base_config_is_never_mutated(policy: AgentReasoningEffort) -> None:
    base_config = dict(BASE_WITH_HIGH)
    snapshot = dict(base_config)

    apply_agent_reasoning_policy(base_config, policy)

    assert base_config == snapshot


@pytest.mark.parametrize("policy", ALL_POLICIES)
@pytest.mark.parametrize(
    "base_config",
    [BASE_WITHOUT_EFFORT, BASE_WITH_HIGH, {"reasoning_effort": "off"}],
)
def test_result_never_carries_policy_only_values(
    base_config: dict[str, Any],
    policy: AgentReasoningEffort,
) -> None:
    resolved = apply_agent_reasoning_policy(base_config, policy)

    # `inherit` 是策略层取值，不得泄漏到解析结果。
    assert resolved.get("reasoning_effort") != "inherit"
    # 关闭思考一律表示为“无该键”，不写入字面 "off"。
    assert resolved.get("reasoning_effort") != "off"


@pytest.mark.parametrize("unknown_policy", ["extreme", "", "HIGH", "Inherit"])
def test_unknown_policy_falls_back_to_inherit(unknown_policy: str) -> None:
    base_config = dict(BASE_WITH_HIGH)

    resolved = apply_agent_reasoning_policy(
        base_config,
        cast(AgentReasoningEffort, unknown_policy),
    )

    # 回退等价于 inherit：保留基础值，且不把无法识别的值写进结果。
    assert resolved["reasoning_effort"] == "high"
    assert resolved is not base_config
    assert base_config == BASE_WITH_HIGH


def test_unknown_policy_still_normalises_literal_off_base() -> None:
    resolved = apply_agent_reasoning_policy(
        {"reasoning_effort": "off"},
        cast(AgentReasoningEffort, "extreme"),
    )

    assert "reasoning_effort" not in resolved


def test_special_model_reference_values_are_pinned() -> None:
    """这两个字符串会被原样写入 agent_definitions.model_id，改动会重指所有既有定义。"""
    assert SYSTEM_DEFAULT_MODEL_REFERENCE == "__system_default_model__"
    assert SYSTEM_LIGHT_MODEL_REFERENCE == "__system_light_model__"
