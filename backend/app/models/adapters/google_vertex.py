# -*- coding: utf-8 -*-
"""
Google Vertex Adapter - Vertex 原生适配器。

第一阶段仅支持 LLM 任务；模型列表来自 catalog 注入的数据，读取列表
不要求 API Key 或 ADC，不发起网络请求。实际模型调用由模型工厂负责，
不与列表职责混合（见 docs/plan/VERTEX_ADAPTER_IMPLEMENTATION_PLAN.md
第 5.2 节）。
"""

from collections.abc import Mapping, Sequence

import httpx

from app.models.adapters.base import BaseAdapter

# 第一阶段只展示 Vertex 上的 Gemini 对话模型；Claude 等其他模型走各自
# 的调用链，不进入 Gemini 后端（第 6.1 节）。
_VERTEX_GEMINI_MODEL_PREFIX = "gemini"


class GoogleVertexAdapter(BaseAdapter):
    """google-vertex 原生适配器：任务能力声明 + 目录模型列表。"""

    def __init__(self, catalog_models: Sequence[Mapping[str, str]] | None = None):
        """
        Args:
            catalog_models: catalog 注入的候选模型列表（{"id", "name"}）。
                缺省时不读取任何数据源，列表方法返回空（任务能力声明
                场景由 Registry 无参构造）。
        """
        self._catalog_models = catalog_models

    @property
    def provider_type(self) -> str:
        return "google-vertex"

    async def get_llm_models(
        self,
        client: httpx.AsyncClient,
        base_url: str,
        api_key: str,
        *,
        headers: Mapping[str, str] | None = None,
    ) -> list[dict[str, str]]:
        """返回注入的目录模型，过滤为第一阶段支持的 Gemini 对话模型。"""
        if self._catalog_models is None:
            return []
        return [
            {"id": model["id"], "name": model["name"]}
            for model in self._catalog_models
            if str(model.get("id", "")).startswith(_VERTEX_GEMINI_MODEL_PREFIX)
        ]

    async def get_embedding_models(
        self,
        client: httpx.AsyncClient,
        base_url: str,
        api_key: str,
        *,
        headers: Mapping[str, str] | None = None,
    ) -> list[dict[str, str]]:
        """第一阶段不支持 Embedding（第二阶段 T8 接入）。"""
        return []

    async def get_rerank_models(
        self,
        client: httpx.AsyncClient,
        base_url: str,
        api_key: str,
        *,
        headers: Mapping[str, str] | None = None,
    ) -> list[dict[str, str]]:
        """不支持 Rerank（不在计划范围内）。"""
        return []

    def supports_llm(self) -> bool:
        """第一阶段支持 LLM。"""
        return True

    def supports_embedding(self) -> bool:
        return False

    def supports_rerank(self) -> bool:
        return False
