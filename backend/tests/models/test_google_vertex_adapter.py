# -*- coding: utf-8 -*-
"""
GoogleVertexAdapter 与 Vertex 目录路由测试（离线）。

覆盖：任务能力限制、目录注入与 Gemini 过滤、Registry 原生路由、
服务层路由规则、目录任务类型不受模型数量影响（实施计划第 5.2、6.1 节）。
"""

import httpx
import pytest

from app.core.encryption import EncryptionService, generate_encryption_key
from app.models.adapters import GoogleVertexAdapter, OpenAICompatibleAdapter
from app.models.adapters.base import BaseAdapter
from app.models.catalog.service import ModelProviderCatalogService
from app.models.entities.model_provider import ModelProvider
from app.models.registry import AdapterRegistry
from app.models.services.model_provider_service import ModelProviderService


def _catalog_models() -> list[dict[str, str]]:
    """模拟 catalog 注入的候选列表（含 Gemini 与非 Gemini 模型）。"""
    return [
        {"id": "gemini-3.5-flash", "name": "Gemini 3.5 Flash"},
        {"id": "claude-opus-4-8@default", "name": "Claude Opus 4.8"},
        {"id": "gemini-3.1-pro-preview", "name": "Gemini 3.1 Pro Preview"},
        {"id": "embedder-x", "name": "Embedder X"},
    ]


def _vertex_provider() -> ModelProvider:
    return ModelProvider(
        name="Vertex",
        provider_type="google-vertex",
        url="",
        api_key_encrypted="",
    )


@pytest.fixture
def provider_service() -> ModelProviderService:
    return ModelProviderService(EncryptionService(generate_encryption_key()))


class TestGoogleVertexAdapter:
    def test_task_capabilities_limited_to_llm(self) -> None:
        adapter = GoogleVertexAdapter()
        assert isinstance(adapter, BaseAdapter)
        assert adapter.provider_type == "google-vertex"
        assert adapter.supports_llm() is True
        assert adapter.supports_embedding() is False
        assert adapter.supports_rerank() is False

    async def test_llm_models_filtered_to_gemini(self) -> None:
        adapter = GoogleVertexAdapter(catalog_models=_catalog_models())
        async with httpx.AsyncClient() as client:
            models = await adapter.get_llm_models(client, "", "")
        assert [model["id"] for model in models] == [
            "gemini-3.5-flash",
            "gemini-3.1-pro-preview",
        ]

    async def test_llm_models_without_injection_returns_empty(self) -> None:
        adapter = GoogleVertexAdapter()
        async with httpx.AsyncClient() as client:
            models = await adapter.get_llm_models(client, "", "")
        assert models == []

    async def test_embedding_and_rerank_models_always_empty(self) -> None:
        adapter = GoogleVertexAdapter(catalog_models=_catalog_models())
        async with httpx.AsyncClient() as client:
            assert await adapter.get_embedding_models(client, "", "") == []
            assert await adapter.get_rerank_models(client, "", "") == []


class TestRegistryRouting:
    def test_vertex_routes_to_native_adapter(self) -> None:
        adapter = AdapterRegistry.get_adapter("google-vertex")
        assert isinstance(adapter, GoogleVertexAdapter)

    def test_registry_supports_vertex_llm_only(self) -> None:
        assert AdapterRegistry.is_supported("google-vertex", "llm") is True
        assert AdapterRegistry.is_supported("google-vertex", "embedding") is False
        assert AdapterRegistry.is_supported("google-vertex", "rerank") is False

    def test_registry_passes_catalog_models_to_vertex_adapter(self) -> None:
        adapter = AdapterRegistry.get_adapter(
            "google-vertex", catalog_models=_catalog_models()
        )
        assert isinstance(adapter, GoogleVertexAdapter)
        assert adapter.provider_type == "google-vertex"

    def test_vertex_anthropic_stays_out_of_gemini_backend(self) -> None:
        adapter = AdapterRegistry.get_adapter("google-vertex-anthropic")
        assert isinstance(adapter, OpenAICompatibleAdapter)
        assert not isinstance(adapter, GoogleVertexAdapter)


class TestServiceRouting:
    def test_runtime_provider_type_resolution(
        self, provider_service: ModelProviderService
    ) -> None:
        resolve = provider_service._resolve_runtime_provider_type
        assert resolve("google-vertex") == "google-vertex"
        assert resolve("google-vertex-anthropic") == "openai-compatible"
        assert resolve("anthropic-compatible") == "anthropic-compatible"
        assert resolve("openai-compatible-responses") == "openai-compatible-responses"
        assert resolve("gemini-compatible") == "gemini-compatible"
        assert resolve("openai") == "openai-compatible"

    async def test_get_available_models_returns_gemini_catalog_models(
        self, provider_service: ModelProviderService
    ) -> None:
        models = await provider_service.get_available_models(_vertex_provider(), "llm")

        assert models
        assert all(model["id"].startswith("gemini") for model in models)
        # Claude 等其他模型不进入 Gemini 后端。
        assert not any(model["id"].startswith("claude") for model in models)
        # 候选模型不代表账号权限、区域支持或配额状态，但均带展示名称。
        assert all(model["name"] for model in models)

    async def test_get_available_models_rejects_non_llm_tasks(
        self, provider_service: ModelProviderService
    ) -> None:
        with pytest.raises(ValueError, match="does not support task_type"):
            await provider_service.get_available_models(_vertex_provider(), "embedding")
        with pytest.raises(ValueError, match="does not support task_type"):
            await provider_service.get_available_models(_vertex_provider(), "rerank")

    async def test_validate_and_get_models_routes_vertex_to_catalog(
        self, provider_service: ModelProviderService
    ) -> None:
        models = await provider_service.validate_and_get_models(
            "google-vertex", url="", api_key=""
        )
        assert models
        assert all(model["id"].startswith("gemini") for model in models)

    async def test_supported_task_types_limited_to_llm(
        self, provider_service: ModelProviderService
    ) -> None:
        assert await provider_service.get_supported_task_types(_vertex_provider()) == [
            "llm"
        ]


class TestCatalogTaskTypeLimit:
    def test_counts_do_not_enable_unimplemented_tasks(self) -> None:
        catalog_service = ModelProviderCatalogService()
        assert (
            catalog_service._supported_task_types_for(
                "google-vertex", {"llm": 40, "embedding": 5, "rerank": 2}
            )
            == ["llm"]
        )

    def test_get_supported_task_types_ignores_catalog_match(self) -> None:
        catalog_service = ModelProviderCatalogService()
        assert catalog_service.get_supported_task_types("google-vertex") == ["llm"]

    async def test_snapshot_summary_limits_vertex_task_types(self) -> None:
        catalog_service = ModelProviderCatalogService()
        summary = await catalog_service.get_provider("google-vertex")
        assert summary.supported_task_types == ["llm"]

    async def test_vertex_anthropic_keeps_existing_behavior(self) -> None:
        catalog_service = ModelProviderCatalogService()
        # google-vertex-anthropic 不做原生接入，维持既有目录行为（界面
        # 提示由 T6 处理），但绝不进入 Gemini 后端。
        summary = await catalog_service.get_provider("google-vertex-anthropic")
        assert summary.supported_task_types == ["llm"]
        assert "google-vertex-anthropic" not in AdapterRegistry.list_providers()
