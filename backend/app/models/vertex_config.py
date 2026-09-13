# -*- coding: utf-8 -*-
"""
Vertex Provider 配置校验。

为 `google-vertex` 提供强类型非敏感配置（provider_config）与
Service Account 凭据（service_account_json）的结构校验，
供 Provider CRUD 与后续认证模块共同使用。

约定（见 docs/plan/VERTEX_ADAPTER_IMPLEMENTATION_PLAN.md 第 4 节）：
- `provider_config` 只允许 project_id / location / auth_mode 三个字段，未声明字段拒绝保存；
- 凭据、token、HTTP 头不能写入 `provider_config`；
- Service Account JSON 仅接受 type=service_account 的预期结构，限制上传大小。
"""

import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

VERTEX_PROVIDER_TYPE = "google-vertex"

VERTEX_CREDENTIALS_ACTIONS = frozenset({"keep", "replace", "clear"})

VERTEX_AUTH_MODES = ("adc", "service_account")

# 与第 5.1 节一致：限制 Service Account 上传大小（64 KiB）。
SERVICE_ACCOUNT_JSON_MAX_BYTES = 64 * 1024

_SERVICE_ACCOUNT_REQUIRED_FIELDS = (
    "project_id",
    "private_key",
    "client_email",
    "token_uri",
)


class VertexConfigError(ValueError):
    """Vertex 配置或凭据校验失败，消息可直接返回给用户。"""


class VertexProviderConfig(BaseModel):
    """google-vertex 连接的非敏感配置。"""

    model_config = ConfigDict(extra="forbid")

    project_id: str = Field(min_length=1, max_length=100)
    location: str = Field(min_length=1, max_length=100)
    auth_mode: Literal["adc", "service_account"]


def _validate_config_payload(payload: dict) -> VertexProviderConfig:
    """校验并规范化配置字典（去除首尾空格），失败时抛出 VertexConfigError。"""
    normalized = {
        key: (value.strip() if isinstance(value, str) else value)
        for key, value in payload.items()
    }
    try:
        return VertexProviderConfig.model_validate(normalized)
    except ValidationError as exc:
        details: list[str] = []
        for error in exc.errors():
            field = ".".join(str(part) for part in error.get("loc", ()))
            if field == "project_id":
                details.append("project_id 不能为空")
            elif field == "location":
                details.append("location 不能为空")
            elif field == "auth_mode":
                details.append("auth_mode 只允许 adc 或 service_account")
            else:
                details.append(f"字段 {field} 校验失败")
        raise VertexConfigError("provider_config 校验失败：" + "；".join(details)) from exc


def parse_vertex_provider_config(raw: str | None) -> VertexProviderConfig:
    """解析请求中的 provider_config（JSON 字符串），必须提供且合法。"""
    if raw is None or not raw.strip():
        raise VertexConfigError(
            "Vertex 连接必须提供 provider_config（project_id、location、auth_mode）"
        )
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise VertexConfigError("provider_config 必须是合法的 JSON") from exc
    if not isinstance(payload, dict):
        raise VertexConfigError("provider_config 必须是 JSON 对象")
    return _validate_config_payload(payload)


def load_vertex_provider_config(
    stored: dict | str | None,
) -> VertexProviderConfig:
    """校验数据库中已保存的配置（用于更新时合并旧配置后的最终状态校验）。"""
    if isinstance(stored, str):
        if not stored.strip():
            stored = None
        else:
            try:
                stored = json.loads(stored)
            except json.JSONDecodeError as exc:
                raise VertexConfigError("已保存的 Vertex 配置格式无效，请重新配置") from exc
    if not isinstance(stored, dict) or not stored:
        raise VertexConfigError(
            "缺少 Vertex 配置，请补充 project_id、location 和 auth_mode"
        )
    return _validate_config_payload(stored)


def validate_vertex_service_account_json(raw: str | None) -> dict:
    """结构校验 Service Account JSON，返回解析后的字典（不校验密钥有效性）。"""
    if raw is None or not raw.strip():
        raise VertexConfigError("Service Account 凭据不能为空")
    encoded = raw.encode("utf-8")
    if len(encoded) > SERVICE_ACCOUNT_JSON_MAX_BYTES:
        raise VertexConfigError("Service Account JSON 超过 64 KiB 大小限制")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise VertexConfigError("Service Account 凭据必须是合法的 JSON") from exc
    if not isinstance(payload, dict):
        raise VertexConfigError("Service Account 凭据必须是 JSON 对象")
    if payload.get("type") != "service_account":
        raise VertexConfigError("Service Account 凭据的 type 必须是 service_account")
    missing = [
        field
        for field in _SERVICE_ACCOUNT_REQUIRED_FIELDS
        if not isinstance(payload.get(field), str) or not payload[field].strip()
    ]
    if missing:
        raise VertexConfigError(
            "Service Account 凭据缺少必要字段：" + "、".join(missing)
        )
    return payload
