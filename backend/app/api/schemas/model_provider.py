# -*- coding: utf-8 -*-
"""
ModelProvider API Schemas - 模型服务提供商请求/响应模型。
"""

from typing import Any

from pydantic import BaseModel, Field


class CatalogMatchResponse(BaseModel):
    """Matched catalog provider metadata for a saved provider."""

    catalog_provider_type: str = Field(description="匹配到的 catalog provider_type")
    display_name: str = Field(description="Catalog 提供商显示名")
    default_url: str | None = Field(default=None, description="Catalog 默认 URL")
    api: str | None = Field(default=None, description="Models.dev api 字段")
    icon_path: str | None = Field(default=None, description="内置图标路径")
    models_dev_provider_id: str | None = Field(
        default=None, description="Models.dev provider id"
    )
    matched_via: str = Field(description="provider_type 或 api")


class ModelProviderResponse(BaseModel):
    """提供商响应。"""

    id: str = Field(description="提供商 ID")
    name: str = Field(description="提供商名称/备注")
    url: str = Field(description="服务 URL")
    provider_type: str = Field(description="提供商类型")
    provider_config: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "非敏感配置（Vertex 为 project_id/location/auth_mode，其他提供商为空对象）"
        ),
    )
    has_credentials: bool = Field(
        default=False,
        description=(
            "是否存在已保存的 Vertex Service Account 凭据密文；"
            "不代表 ADC 可用，也不代表已通过认证"
        ),
    )
    custom_header_names: list[str] = Field(
        default_factory=list,
        description="已配置的自定义请求头名称（不返回请求头值）",
    )
    supported_task_types: list[str] = Field(
        description="支持的任务类型列表 (llm, embedding, rerank)"
    )
    icon_path: str | None = Field(description="Catalog 图标路径")
    is_builtin: bool = Field(default=False, description="是否为内置提供商")
    catalog_match: CatalogMatchResponse | None = Field(
        default=None, description="匹配到的 catalog 提供商元数据"
    )
    created_at: str = Field(description="创建时间")
    updated_at: str = Field(description="更新时间")


class CustomHeaderEntry(BaseModel):
    """单条自定义请求头。"""

    key: str = Field(description="请求头名称")
    value: str = Field(description="请求头值")


class ModelProviderValidateRequest(BaseModel):
    """验证提供商连接请求。"""

    provider_type: str = Field(description="提供商类型")
    # Vertex 连接不提供 URL 与 API Key（端点由 SDK 按项目与区域构造）。
    url: str = Field(default="", description="服务 URL")
    api_key: str = Field(default="", description="API Key")
    custom_headers: list["CustomHeaderEntry"] = Field(
        default_factory=list,
        description="自定义请求头",
    )
    provider_config: str | None = Field(
        default=None,
        description=(
            "Vertex 非敏感配置（JSON 字符串）；提供时为草稿值，"
            "省略时配合 provider_id 使用已保存配置"
        ),
    )
    credentials_action: str = Field(
        default="keep",
        description="Vertex 凭据操作（keep/replace/clear），仅用于本次验证",
    )
    service_account_json: str | None = Field(
        default=None,
        description="Vertex Service Account JSON（仅 replace 时传入，不保存）",
    )
    model_id: str | None = Field(
        default=None,
        description="Vertex 测试模型 ID（Vertex 验证必填）",
    )
    provider_id: str | None = Field(
        default=None,
        description="可选的已保存连接 ID，用于读取旧配置与旧凭据参与验证",
    )


class AvailableModelMetadata(BaseModel):
    """模型展示元数据。"""

    release_date: str | None = Field(default=None, description="模型发布日期")
    reasoning: bool | None = Field(default=None, description="是否支持 reasoning")
    tool_call: bool | None = Field(default=None, description="是否支持 tool call")
    modalities: dict[str, list[str]] | None = Field(
        default=None, description="输入输出模态"
    )
    limit: dict[str, Any] | str | int | None = Field(
        default=None, description="上下文与输出限制"
    )
    cost: dict[str, Any] | str | int | None = Field(
        default=None, description="价格元数据"
    )


class AvailableModel(BaseModel):
    """可用模型。"""

    id: str = Field(description="模型 ID")
    name: str = Field(description="模型名称")
    task_type: str | None = Field(default=None, description="任务类型")
    metadata: AvailableModelMetadata | None = Field(
        default=None, description="匹配到的 catalog 元数据"
    )


class ModelProviderValidateResponse(BaseModel):
    """验证提供商连接响应。"""

    success: bool = Field(description="是否验证成功")
    message: str = Field(description="消息")
    models: list[AvailableModel] = Field(description="可用模型列表")
    error_code: str | None = Field(
        default=None,
        description="验证失败错误码（Vertex 按第 6.2 节分类；非 Vertex 为空）",
    )
    validation_scope: str | None = Field(
        default=None,
        description=(
            "验证范围；Vertex 成功时为 model_invocation，"
            "仅证明本次项目、区域、凭据和模型组合可调用"
        ),
    )
