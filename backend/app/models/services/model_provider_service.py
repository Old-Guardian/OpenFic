# -*- coding: utf-8 -*-
"""
Model Provider Service - 模型服务提供商业务逻辑层。

Service作为Executor，是唯一发起调用的地方，负责处理重试、熔断、fallback和观测。
"""

import json
from collections.abc import Mapping
from typing import Any

import httpx
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.encryption import EncryptionService
from app.core.errors import NotFoundError
from app.models.catalog import CatalogMatch, ModelProviderCatalogService
from app.models.entities.model_provider import ModelProvider
from app.models.registry import AdapterRegistry
from app.models.repos import model_provider_repo
from app.models.vertex_config import (
    VERTEX_CREDENTIALS_ACTIONS,
    VERTEX_PROVIDER_TYPE,
    VertexProviderConfig,
    load_vertex_provider_config,
    parse_vertex_provider_config,
    validate_vertex_service_account_json,
)


CUSTOM_PROVIDER_TYPES = frozenset(
    {
        "openai-compatible",
        "openai-compatible-responses",
        "anthropic-compatible",
        "gemini-compatible",
    }
)


class ModelProviderService:
    """模型服务提供商Service（Executor），负责执行调用和观测。"""

    def __init__(
        self,
        encryption_service: EncryptionService,
        catalog_service: ModelProviderCatalogService | None = None,
    ):
        """
        初始化服务。

        Args:
            encryption_service: 加密服务实例。
        """
        self.encryption_service = encryption_service
        self.catalog_service = catalog_service or ModelProviderCatalogService()

    # ========================
    # CRUD 操作
    # ========================

    async def get_all_providers(self, session: AsyncSession) -> list[ModelProvider]:
        """
        获取所有提供商。

        Args:
            session: 数据库 session。

        Returns:
            提供商列表。
        """
        return await model_provider_repo.get_all(session)

    async def get_catalog_match(self, provider: ModelProvider) -> CatalogMatch | None:
        return await self.catalog_service.match_saved_provider(
            provider.provider_type, provider.url
        )

    async def get_supported_task_types(
        self,
        provider: ModelProvider,
        catalog_match: CatalogMatch | None = None,
    ) -> list[str]:
        if provider.is_builtin:
            return ["embedding", "rerank"]
        if provider.provider_type == "gemini-compatible":
            return ["llm"]
        if catalog_match is None:
            catalog_match = await self.get_catalog_match(provider)
        return self.catalog_service.get_supported_task_types(
            provider.provider_type,
            catalog_match,
        )

    async def get_effective_icon_path(
        self,
        provider: ModelProvider,
        catalog_match: CatalogMatch | None = None,
    ) -> str | None:
        if catalog_match is None:
            catalog_match = await self.get_catalog_match(provider)
        return catalog_match.icon_path if catalog_match else None

    def get_decrypted_custom_headers(self, provider: ModelProvider) -> dict[str, str]:
        """获取自定义提供商的请求头，不向 API 响应暴露值。"""
        if provider.provider_type not in CUSTOM_PROVIDER_TYPES:
            return {}

        encrypted_headers = provider.custom_headers_encrypted
        if not encrypted_headers:
            return {}

        try:
            payload = json.loads(self.encryption_service.decrypt(encrypted_headers))
        except Exception:
            logger.warning("Failed to decrypt custom headers for provider {}", provider.id)
            return {}

        if not isinstance(payload, dict):
            return {}
        return {
            key: value
            for key, value in payload.items()
            if isinstance(key, str) and isinstance(value, str)
        }

    def get_custom_header_names(self, provider: ModelProvider) -> list[str]:
        """获取已配置的自定义请求头名称。"""
        return list(self.get_decrypted_custom_headers(provider))

    @staticmethod
    def get_provider_config_payload(provider: ModelProvider) -> dict[str, Any]:
        """获取已保存的非敏感配置（防御性解析，失败返回空字典，不触发校验异常）。"""
        stored = provider.provider_config
        if isinstance(stored, dict):
            return stored
        if isinstance(stored, str) and stored.strip():
            try:
                payload = json.loads(stored)
            except json.JSONDecodeError:
                return {}
            return payload if isinstance(payload, dict) else {}
        return {}

    @staticmethod
    def _normalize_custom_headers(
        provider_type: str,
        entries: list[dict[str, str]] | None,
        existing_headers: Mapping[str, str] | None = None,
    ) -> dict[str, str]:
        """清理请求头输入，并在更新时保留未重新填写的旧值。"""
        existing = dict(existing_headers or {})
        normalized: dict[str, str] = {}

        for entry in entries or []:
            key = entry.get("key", "").strip()
            value = entry.get("value", "")
            if not isinstance(value, str):
                raise ValueError("自定义请求头值必须是字符串")
            if not key and not value:
                continue
            if "\r" in key or "\n" in key or "\r" in value or "\n" in value:
                raise ValueError("自定义请求头不能包含换行符")
            if not key:
                continue

            existing_key = next(
                (name for name in existing if name.casefold() == key.casefold()),
                None,
            )
            if not value and existing_key is not None:
                normalized[key] = existing[existing_key]
            elif value:
                normalized[key] = value

        if provider_type not in CUSTOM_PROVIDER_TYPES and normalized:
            raise ValueError("自定义请求头仅支持自定义类型提供商")
        return normalized

    async def get_provider_by_id(
        self, session: AsyncSession, provider_id: str
    ) -> ModelProvider:
        """
        根据ID获取提供商。

        Args:
            session: 数据库session。
            provider_id: 提供商ID。

        Returns:
            提供商实例。

        Raises:
            NotFoundError: 如果提供商不存在。
        """
        provider = await model_provider_repo.get_by_id(session, provider_id)
        if not provider:
            raise NotFoundError(f"Provider with id {provider_id} not found")
        return provider

    async def create_provider(
        self,
        session: AsyncSession,
        name: str,
        url: str,
        api_key: str,
        provider_type: str,
        custom_headers: list[dict[str, str]] | None = None,
        provider_config: str | None = None,
        credentials_action: str = "keep",
        service_account_json: str | None = None,
    ) -> ModelProvider:
        """
        创建提供商。

        Args:
            session: 数据库 session。
            name: 提供商名称。
            url: 服务 URL。
            api_key: API Key（明文）。
            provider_type: 提供商类型。
            custom_headers: 自定义请求头。
            provider_config: Vertex 非敏感配置（JSON 字符串）。
            credentials_action: Vertex 凭据操作（keep/replace/clear）。
            service_account_json: Vertex Service Account JSON（仅 replace 时传入）。

        Returns:
            创建的提供商实例。
        """
        if provider_type == VERTEX_PROVIDER_TYPE:
            self._normalize_custom_headers(provider_type, custom_headers)
            return await self._create_vertex_provider(
                session=session,
                name=name,
                url=url,
                api_key=api_key,
                provider_config=provider_config,
                credentials_action=credentials_action,
                service_account_json=service_account_json,
            )

        self._reject_vertex_inputs_for_non_vertex(
            provider_config, credentials_action, service_account_json
        )

        # 表单层的 URL 必填已放宽（Vertex 允许为空），非 Vertex 在此保留原语义。
        if not url or not url.strip():
            raise ValueError("服务 URL 不能为空")

        url = await self._resolve_provider_url(provider_type, url)

        # 加密 API Key
        encrypted_key = self.encryption_service.encrypt(api_key) if api_key else ""
        normalized_headers = self._normalize_custom_headers(provider_type, custom_headers)
        encrypted_headers = (
            self.encryption_service.encrypt(json.dumps(normalized_headers, ensure_ascii=False))
            if normalized_headers
            else ""
        )

        provider = await model_provider_repo.create(
            session=session,
            name=name,
            url=url,
            api_key_encrypted=encrypted_key,
            provider_type=provider_type,
            custom_headers_encrypted=encrypted_headers,
        )
        await session.commit()
        return provider

    async def _create_vertex_provider(
        self,
        session: AsyncSession,
        name: str,
        url: str,
        api_key: str,
        provider_config: str | None,
        credentials_action: str,
        service_account_json: str | None,
    ) -> ModelProvider:
        """创建 Vertex 连接：必填 provider_config，URL 与 API Key 恒为空。"""
        if url and url.strip():
            raise ValueError("Vertex 连接不需要配置服务 URL")
        if api_key and api_key.strip():
            raise ValueError("Vertex 连接不支持 API Key，请使用 ADC 或 Service Account")

        config = parse_vertex_provider_config(provider_config)
        credentials_encrypted = self._resolve_vertex_credentials(
            existing_encrypted="",
            credentials_action=credentials_action,
            service_account_json=service_account_json,
        )
        self._validate_vertex_final_state(config, credentials_encrypted)

        provider = await model_provider_repo.create(
            session=session,
            name=name,
            url="",
            api_key_encrypted="",
            provider_type=VERTEX_PROVIDER_TYPE,
            custom_headers_encrypted="",
            provider_config=config.model_dump(),
            credentials_encrypted=credentials_encrypted,
        )
        await session.commit()
        return provider

    def _resolve_vertex_credentials(
        self,
        existing_encrypted: str,
        credentials_action: str,
        service_account_json: str | None,
    ) -> str:
        """按凭据操作语义计算 Vertex 连接的最终凭据密文。"""
        if credentials_action not in VERTEX_CREDENTIALS_ACTIONS:
            raise ValueError("credentials_action 只允许 keep、replace 或 clear")

        if credentials_action == "replace":
            if service_account_json is None or not service_account_json.strip():
                raise ValueError("credentials_action=replace 必须提供 service_account_json")
            payload = validate_vertex_service_account_json(service_account_json)
            return self.encryption_service.encrypt(
                json.dumps(payload, ensure_ascii=False)
            )

        if service_account_json is not None and service_account_json.strip():
            raise ValueError("仅 credentials_action=replace 时可以提供 service_account_json")
        if credentials_action == "clear":
            return ""
        return existing_encrypted

    @staticmethod
    def _validate_vertex_final_state(
        config: VertexProviderConfig, credentials_encrypted: str
    ) -> None:
        """校验认证模式与凭据的最终状态组合。"""
        if config.auth_mode == "service_account":
            if not credentials_encrypted:
                raise ValueError(
                    "Service Account 认证需要提供凭据；没有已保存的凭据时请重新提供"
                )
        elif config.auth_mode == "adc":
            if credentials_encrypted:
                raise ValueError(
                    "ADC 认证不能保留 Service Account 凭据，请先清除（credentials_action=clear）"
                )

    @staticmethod
    def _reject_vertex_inputs_for_non_vertex(
        provider_config: str | None,
        credentials_action: str,
        service_account_json: str | None,
    ) -> None:
        """非 Vertex 提供商不接受非空 Vertex 配置或凭据。"""
        if service_account_json is not None and service_account_json.strip():
            raise ValueError("仅 Vertex 连接支持 Service Account 凭据")
        if credentials_action not in VERTEX_CREDENTIALS_ACTIONS:
            raise ValueError("credentials_action 只允许 keep、replace 或 clear")
        if credentials_action == "replace":
            raise ValueError("仅 Vertex 连接支持 credentials_action=replace")
        if provider_config is not None and provider_config.strip():
            try:
                payload = json.loads(provider_config)
            except json.JSONDecodeError as exc:
                raise ValueError("provider_config 必须是合法的 JSON") from exc
            if not isinstance(payload, dict) or payload:
                raise ValueError("仅 Vertex 连接支持 provider_config")

    async def _resolve_provider_url(self, provider_type: str, url: str) -> str:
        if provider_type in {
            "openai-compatible",
            "openai-compatible-responses",
            "anthropic-compatible",
            "gemini-compatible",
        }:
            return url

        try:
            catalog_provider = await self.catalog_service.get_provider(provider_type)
        except KeyError:
            return url

        return catalog_provider.api or url

    async def update_provider(
        self,
        session: AsyncSession,
        provider_id: str,
        name: str | None = None,
        url: str | None = None,
        api_key: str | None = None,
        provider_type: str | None = None,
        custom_headers: list[dict[str, str]] | None = None,
        provider_config: str | None = None,
        credentials_action: str = "keep",
        service_account_json: str | None = None,
    ) -> ModelProvider:
        """
        更新提供商。

        Args:
            session: 数据库 session。
            provider_id: 提供商 ID。
            name: 提供商名称。
            url: 服务 URL。
            api_key: API Key（明文），如果提供则重新加密。
            provider_type: 提供商类型。
            custom_headers: 自定义请求头。
            provider_config: Vertex 非敏感配置（JSON 字符串），省略则保留原配置。
            credentials_action: Vertex 凭据操作（keep/replace/clear）。
            service_account_json: Vertex Service Account JSON（仅 replace 时传入）。

        Returns:
            更新后的提供商实例。

        Raises:
            NotFoundError: 如果提供商不存在。
        """
        existing = await model_provider_repo.get_by_id(session, provider_id)
        if existing is None:
            raise NotFoundError(f"Provider with id {provider_id} not found")
        if existing.is_builtin:
            raise ValueError("内置提供商不允许编辑")

        effective_provider_type = provider_type or existing.provider_type

        if effective_provider_type == VERTEX_PROVIDER_TYPE:
            self._normalize_custom_headers(
                effective_provider_type,
                custom_headers,
                self.get_decrypted_custom_headers(existing),
            )
            return await self._update_vertex_provider(
                session=session,
                provider_id=provider_id,
                existing=existing,
                name=name,
                url=url,
                api_key=api_key,
                provider_type=provider_type,
                provider_config=provider_config,
                credentials_action=credentials_action,
                service_account_json=service_account_json,
            )

        self._reject_vertex_inputs_for_non_vertex(
            provider_config, credentials_action, service_account_json
        )

        # 加密 API Key（如果提供）
        encrypted_key = None
        if api_key is not None:
            encrypted_key = self.encryption_service.encrypt(api_key) if api_key else ""

        encrypted_headers = None
        if custom_headers is not None:
            normalized_headers = self._normalize_custom_headers(
                effective_provider_type,
                custom_headers,
                self.get_decrypted_custom_headers(existing),
            )
            encrypted_headers = (
                self.encryption_service.encrypt(
                    json.dumps(normalized_headers, ensure_ascii=False)
                )
                if normalized_headers
                else ""
            )
        elif effective_provider_type not in CUSTOM_PROVIDER_TYPES:
            encrypted_headers = ""

        # 非 Vertex 最终状态：清理 Vertex 专属配置与凭据（含从 Vertex 切换过来的场景）。
        provider = await model_provider_repo.update(
            session=session,
            provider_id=provider_id,
            name=name,
            url=url,
            api_key_encrypted=encrypted_key,
            custom_headers_encrypted=encrypted_headers,
            provider_type=provider_type,
            provider_config={},
            credentials_encrypted="",
        )

        if not provider:
            raise NotFoundError(f"Provider with id {provider_id} not found")

        await session.commit()
        return provider

    async def _update_vertex_provider(
        self,
        session: AsyncSession,
        provider_id: str,
        existing: ModelProvider,
        name: str | None,
        url: str | None,
        api_key: str | None,
        provider_type: str | None,
        provider_config: str | None,
        credentials_action: str,
        service_account_json: str | None,
    ) -> ModelProvider:
        """更新 Vertex 连接：合并旧配置后对最终状态校验，一次事务提交。"""
        if url is not None and url.strip():
            raise ValueError("Vertex 连接不需要配置服务 URL")
        if api_key is not None and api_key.strip():
            raise ValueError("Vertex 连接不支持 API Key，请使用 ADC 或 Service Account")

        # 合并配置：省略 provider_config 时保留旧配置，提供时整体替换。
        if provider_config is None:
            config = load_vertex_provider_config(existing.provider_config)
            config_payload = self.get_provider_config_payload(existing)
        else:
            config = parse_vertex_provider_config(provider_config)
            config_payload = config.model_dump()

        credentials_encrypted = self._resolve_vertex_credentials(
            existing_encrypted=existing.credentials_encrypted or "",
            credentials_action=credentials_action,
            service_account_json=service_account_json,
        )
        self._validate_vertex_final_state(config, credentials_encrypted)

        provider = await model_provider_repo.update(
            session=session,
            provider_id=provider_id,
            name=name,
            # Vertex 的 URL 与 API Key 规范为空（含清理旧目录遗留数据）。
            url="",
            api_key_encrypted="",
            custom_headers_encrypted="",
            provider_type=provider_type,
            provider_config=config_payload,
            credentials_encrypted=credentials_encrypted,
        )

        if not provider:
            raise NotFoundError(f"Provider with id {provider_id} not found")

        await session.commit()
        return provider

    async def delete_provider(self, session: AsyncSession, provider_id: str) -> None:
        """
        删除提供商。

        Args:
            session: 数据库 session。
            provider_id: 提供商 ID。

        Raises:
            NotFoundError: 如果提供商不存在。
            ValueError: 如果提供商为内置提供商，不允许删除。
        """
        provider = await model_provider_repo.get_by_id(session, provider_id)
        if provider is None:
            raise NotFoundError(f"Provider with id {provider_id} not found")
        if provider.is_builtin:
            raise ValueError("内置提供商不允许删除")
        success = await model_provider_repo.delete_by_id(session, provider_id)
        if not success:
            raise NotFoundError(f"Provider with id {provider_id} not found")
        await session.commit()

    # ========================
    # API Key 操作
    # ========================

    def get_decrypted_api_key(self, provider: ModelProvider) -> str | None:
        """
        获取解密后的 API Key。

        Args:
            provider: 提供商实例。

        Returns:
            解密后的 API Key，如果加密字段为空或解密失败返回 None。
        """
        if not provider.api_key_encrypted or provider.api_key_encrypted.strip() == "":
            return None

        try:
            return self.encryption_service.decrypt(provider.api_key_encrypted)
        except Exception as e:
            logger.warning(f"Failed to decrypt API key for provider {provider.id}: {e}")
            return None

    # ========================
    # 模型列表获取（Executor执行点）
    # ========================

    @staticmethod
    def _resolve_runtime_provider_type(provider_type: str) -> str:
        """
        解析运行时 Provider 类型（validate_and_get_models 与
        get_available_models 共用的路由规则，见实施计划第 5.2 节）。

        Vertex 显式路由到原生 Adapter；google-vertex-anthropic 保持既有
        兼容回退，不进入 Gemini 后端。
        """
        if provider_type == VERTEX_PROVIDER_TYPE:
            return VERTEX_PROVIDER_TYPE
        if provider_type in (
            "anthropic-compatible",
            "openai-compatible-responses",
            "gemini-compatible",
        ):
            return provider_type
        return "openai-compatible"

    async def _get_vertex_models(self) -> list[dict[str, str]]:
        """经 Vertex Adapter 读取目录模型列表（离线，无凭据，不发起网络请求）。"""
        try:
            response = await self.catalog_service.get_provider_models(
                VERTEX_PROVIDER_TYPE, "llm"
            )
        except KeyError:
            return []
        catalog_models = [
            {"id": model.model_id, "name": model.display_name}
            for model in response.models
        ]
        adapter = AdapterRegistry.get_adapter(
            VERTEX_PROVIDER_TYPE, catalog_models=catalog_models
        )
        async with httpx.AsyncClient(timeout=30.0) as client:
            return await adapter.get_llm_models(client, "", "")

    async def validate_and_get_models(
        self,
        provider_type: str,
        url: str,
        api_key: str,
        custom_headers: list[dict[str, str]] | None = None,
    ) -> list[dict[str, str]]:
        """
        验证提供商连接并获取模型列表。

        Args:
            provider_type: 提供商类型。
            url: 服务 URL。
            api_key: API Key（明文）。
            custom_headers: 自定义请求头。

        Returns:
            模型列表，每个模型为 {"id": "model-id", "name": "Model Name"} 格式。

        Raises:
            Exception: 如果连接验证失败。
        """
        url = await self._resolve_provider_url(provider_type, url)

        # 使用统一的Adapter获取模型
        runtime_provider_type = self._resolve_runtime_provider_type(provider_type)

        if runtime_provider_type == VERTEX_PROVIDER_TYPE:
            # Vertex：返回目录模型列表；success 仅表示列表读取成功，
            # 不代表凭据可用（真实调用验证由 T5 接管）。
            return await self._get_vertex_models()

        adapter = AdapterRegistry.get_adapter(runtime_provider_type)
        request_headers = self._normalize_custom_headers(provider_type, custom_headers)

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                # 默认获取LLM模型列表（用于连接验证）
                if request_headers:
                    return await adapter.get_llm_models(
                        client,
                        url,
                        api_key,
                        headers=request_headers,
                    )
                return await adapter.get_llm_models(client, url, api_key)
        except Exception as e:
            logger.error(f"验证提供商连接失败: {e}")
            raise

    async def get_available_models(
        self, provider: ModelProvider, task_type: str
    ) -> list[dict[str, str]]:
        """
        获取指定provider和task_type的可用模型列表（Executor执行点）。

        Args:
            provider: 提供商实例。
            task_type: 任务类型（llm、embedding 或 rerank）。

        Returns:
            模型列表。

        Raises:
            ValueError: 如果不支持该provider和task_type组合。
            Exception: 如果请求失败。
        """
        if provider.is_builtin:
            return self._builtin_available_models(task_type)

        logger.info(
            f"Fetching available models for provider={provider.provider_type}, task_type={task_type}"
        )

        # 检查是否支持
        runtime_provider_type = self._resolve_runtime_provider_type(provider.provider_type)
        if not AdapterRegistry.is_supported(runtime_provider_type, task_type):
            raise ValueError(
                f"Provider '{provider.provider_type}' does not support task_type '{task_type}'"
            )

        if runtime_provider_type == VERTEX_PROVIDER_TYPE:
            # Vertex：目录注入的静态列表，读取不要求 API Key 或 ADC，
            # 也不解密任何凭据。
            return await self._get_vertex_models()

        # 获取Adapter
        adapter = AdapterRegistry.get_adapter(runtime_provider_type)

        # 解密API Key
        api_key = self.encryption_service.decrypt(provider.api_key_encrypted)
        request_headers = self.get_decrypted_custom_headers(provider)

        # 创建HTTP客户端并执行请求
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                # 根据task_type路由到对应方法
                if task_type == "llm":
                    if request_headers:
                        models = await adapter.get_llm_models(
                            client,
                            provider.url,
                            api_key,
                            headers=request_headers,
                        )
                    else:
                        models = await adapter.get_llm_models(client, provider.url, api_key)
                elif task_type == "rerank":
                    if request_headers:
                        models = await adapter.get_rerank_models(
                            client,
                            provider.url,
                            api_key,
                            headers=request_headers,
                        )
                    else:
                        models = await adapter.get_rerank_models(client, provider.url, api_key)
                else:
                    if request_headers:
                        models = await adapter.get_embedding_models(
                            client,
                            provider.url,
                            api_key,
                            headers=request_headers,
                        )
                    else:
                        models = await adapter.get_embedding_models(client, provider.url, api_key)

                logger.info(
                    f"Successfully fetched {len(models)} models for provider={provider.provider_type}, task_type={task_type}"
                )
                return models

        except Exception as e:
            logger.error(
                "Failed to fetch models for provider={}, task_type={}: {}",
                provider.provider_type,
                task_type,
                str(e),
                exc_info=True,
            )
            raise

    async def enrich_models_with_catalog_metadata(
        self,
        provider: ModelProvider,
        task_type: str,
        models: list[dict[str, str]],
    ) -> list[dict[str, Any]]:
        """按 model id 将远端返回模型与 catalog 元数据对齐。"""

        enriched_models: list[dict[str, Any]] = [
            {
                "id": model["id"],
                "name": model["name"],
                "task_type": task_type,
                "metadata": None,
            }
            for model in models
        ]

        if not models:
            return enriched_models

        catalog_match = await self.get_catalog_match(provider)
        if catalog_match is None:
            return enriched_models

        try:
            catalog_models = await self.catalog_service.get_provider_models(
                catalog_match.catalog_provider_type,
                task_type,
            )
        except KeyError:
            return enriched_models

        catalog_models_by_id = {
            model.model_id: model for model in catalog_models.models
        }

        for model in enriched_models:
            matched_model = catalog_models_by_id.get(model["id"])
            if matched_model is None:
                continue

            model["name"] = matched_model.display_name
            model["task_type"] = matched_model.task_type
            model["metadata"] = matched_model.metadata

        return self.catalog_service._sort_model_dicts_by_release_date(enriched_models)

    @staticmethod
    def _builtin_available_models(task_type: str) -> list[dict[str, str]]:
        """内置提供商的固定模型列表。"""
        from app.models.builtin import BUILTIN_MODELS

        return [
            {"id": spec.model_id, "name": spec.name}
            for spec in BUILTIN_MODELS
            if spec.task_type == task_type
        ]
