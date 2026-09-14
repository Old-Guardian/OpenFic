# -*- coding: utf-8 -*-
"""
Google Vertex 认证解析模块。

将经校验的 Vertex 配置与已解密的 Service Account JSON 解析为仅限内存的
强类型连接上下文（VertexConnectionContext），供连接验证、普通 LLM 调用
和 Agent 共同使用（见 docs/plan/VERTEX_ADAPTER_IMPLEMENTATION_PLAN.md
第 5.1 节）。

安全约束：
- 不读取、不修改进程级环境变量（GOOGLE_GENAI_USE_VERTEXAI、
  GOOGLE_CLOUD_PROJECT 等），连接切换不依赖全局状态；
- Service Account 使用内存 JSON 构造凭据，不生成临时私钥文件；
- 按认证模式解析，授权失败不静默回退到另一认证模式；
- 错误消息脱敏，不包含私钥、完整 JSON 或 token（原始异常保留在
  __cause__ 中供日志诊断）。
"""

import asyncio
from dataclasses import dataclass
from typing import Literal

import google.auth
import google.auth.credentials
from google.auth.exceptions import DefaultCredentialsError, MalformedError, RefreshError
from google.oauth2 import service_account

from app.models.vertex_config import (
    VertexConfigError,
    VertexProviderConfig,
    validate_vertex_service_account_json,
)

# 标准作用域，token 刷新由 google-auth 库管理，不持久化 access token。
VERTEX_CLOUD_PLATFORM_SCOPE = "https://www.googleapis.com/auth/cloud-platform"

# 第 6.2 节错误码（供验证接口、日志和前端状态映射使用）。
VERTEX_ERROR_CONFIG_INVALID = "vertex_config_invalid"
VERTEX_ERROR_CREDENTIALS_MISSING = "vertex_credentials_missing"
VERTEX_ERROR_CREDENTIALS_INVALID = "vertex_credentials_invalid"
# 未归类到 6.2 节明确错误码的认证域未知异常，便于与已知凭据问题区分。
VERTEX_ERROR_AUTH_UNKNOWN = "vertex_auth_unknown"


class VertexAuthError(Exception):
    """Vertex 认证解析失败，message 已脱敏，可直接返回给用户。"""

    def __init__(self, message: str, *, error_code: str):
        super().__init__(message)
        self.message = message
        self.error_code = error_code


@dataclass(frozen=True, repr=False)
class VertexConnectionContext:
    """仅限内存的 Vertex 连接上下文。"""

    project_id: str
    location: str
    auth_mode: Literal["adc", "service_account"]
    credentials: google.auth.credentials.Credentials

    def __repr__(self) -> str:
        # Credentials 对象的 repr 可能包含私钥，只输出非敏感字段。
        return (
            f"VertexConnectionContext(project_id={self.project_id!r}, "
            f"location={self.location!r}, auth_mode={self.auth_mode!r})"
        )


def _build_service_account_context(
    config: VertexProviderConfig,
    service_account_json: str | None,
) -> VertexConnectionContext:
    if service_account_json is None or not service_account_json.strip():
        raise VertexAuthError(
            "连接未提供服务账号凭据，请先保存或提供 Service Account JSON",
            error_code=VERTEX_ERROR_CREDENTIALS_MISSING,
        )
    try:
        payload = validate_vertex_service_account_json(service_account_json)
    except VertexConfigError as exc:
        raise VertexAuthError(
            f"Service Account 凭据无效：{exc}",
            error_code=VERTEX_ERROR_CREDENTIALS_INVALID,
        ) from exc
    try:
        credentials = service_account.Credentials.from_service_account_info(
            payload, scopes=[VERTEX_CLOUD_PLATFORM_SCOPE]
        )
    except Exception as exc:
        raise VertexAuthError(
            "Service Account 凭据解析失败，请确认 JSON 内容完整且包含有效私钥",
            error_code=VERTEX_ERROR_CREDENTIALS_INVALID,
        ) from exc
    return VertexConnectionContext(
        project_id=config.project_id,
        location=config.location,
        auth_mode=config.auth_mode,
        credentials=credentials,
    )


def _build_adc_context(config: VertexProviderConfig) -> VertexConnectionContext:
    try:
        credentials, _ = google.auth.default(scopes=[VERTEX_CLOUD_PLATFORM_SCOPE])
    except DefaultCredentialsError as exc:
        raise VertexAuthError(
            "当前连接的后端运行环境未找到应用默认凭据（ADC），"
            "请在该后端可访问的环境中配置 ADC 或改用 Service Account",
            error_code=VERTEX_ERROR_CREDENTIALS_MISSING,
        ) from exc
    except Exception as exc:
        raise VertexAuthError(
            "解析 ADC 失败，请检查后端运行环境中的凭据配置",
            error_code=VERTEX_ERROR_CREDENTIALS_INVALID,
        ) from exc
    if credentials is None:
        raise VertexAuthError(
            "当前连接的后端运行环境未返回有效的 ADC 凭据",
            error_code=VERTEX_ERROR_CREDENTIALS_MISSING,
        )
    return VertexConnectionContext(
        project_id=config.project_id,
        location=config.location,
        auth_mode=config.auth_mode,
        credentials=credentials,
    )


def build_vertex_connection_context(
    config: VertexProviderConfig,
    service_account_json: str | None = None,
) -> VertexConnectionContext:
    """
    同步解析 Vertex 连接上下文。

    Args:
        config: 经校验的 Vertex 非敏感配置。
        service_account_json: 已解密的 Service Account JSON（仅
            auth_mode=service_account 时允许提供）。

    Returns:
        仅限内存的 VertexConnectionContext，包含项目、区域和 Google
        Credentials 对象；每次调用构造新的凭据对象，不做全局缓存。

    Raises:
        VertexAuthError: 认证模式与凭据矛盾、凭据缺失、解析或刷新失败。
    """
    if config.auth_mode == "service_account":
        return _build_service_account_context(config, service_account_json)
    if service_account_json is not None and service_account_json.strip():
        # 矛盾输入直接拒绝，不静默忽略或回退到 Service Account。
        raise VertexAuthError(
            "ADC 认证不应提供服务账号凭据，请检查认证模式设置",
            error_code=VERTEX_ERROR_CONFIG_INVALID,
        )
    return _build_adc_context(config)


async def build_vertex_connection_context_async(
    config: VertexProviderConfig,
    service_account_json: str | None = None,
) -> VertexConnectionContext:
    """异步入口：阻塞部分（ADC 文件/元数据读取、RSA 私钥解析）在线程中执行。"""
    return await asyncio.to_thread(
        build_vertex_connection_context, config, service_account_json
    )


def classify_vertex_auth_error(exc: Exception) -> str:
    """
    将认证相关异常映射为第 6.2 节错误码。

    覆盖凭据解析与刷新阶段的异常；请求层的超时、限流、权限等错误由
    调用方（验证接口、模型工厂）按响应分类，不属于本函数职责。
    """
    if isinstance(exc, VertexAuthError):
        return exc.error_code
    # MalformedError 是 DefaultCredentialsError 的子类：显式提供的凭据文件
    # 格式错误属于凭据无效，须先于“ADC 缺失”判断。
    if isinstance(exc, MalformedError):
        return VERTEX_ERROR_CREDENTIALS_INVALID
    if isinstance(exc, DefaultCredentialsError):
        return VERTEX_ERROR_CREDENTIALS_MISSING
    # 刷新失败（RefreshError）视为凭据无效。
    if isinstance(exc, RefreshError):
        return VERTEX_ERROR_CREDENTIALS_INVALID
    # 未知异常不冒充已知凭据问题，由调用方决定展示与日志策略。
    return VERTEX_ERROR_AUTH_UNKNOWN
