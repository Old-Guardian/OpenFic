# -*- coding: utf-8 -*-
"""
Vertex 原生模型调用运行时测试（离线，模拟 SDK 边界）。

覆盖：模型工厂 Vertex 分支构造参数、既有 Google 分支后端固定、
连接上下文在配置链路中的传递与持久化脱敏（实施计划第 5.1、5.3 节
及 3.1 节已知边界的回归项）。
"""

import json

import google.auth
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from google.auth.credentials import AnonymousCredentials
from google.oauth2 import service_account

from app.agent_runtime.model_config import to_client_model_config, without_api_key
from app.core.encryption import EncryptionService, generate_encryption_key
from app.models.clients import llm_client as llm_client_module
from app.models.clients.google_vertex_auth import (
    VERTEX_ERROR_CREDENTIALS_INVALID,
    VertexAuthError,
    VertexConnectionContext,
)
from app.models.clients.model_factory import ModelConfig, create_chat_model
from app.models.entities.model_provider import ModelProvider
from app.models.services.model_provider_service import ModelProviderService
from app.models.vertex_config import VertexConfigError


def _context(
    project_id: str = "my-gcp-project",
    location: str = "us-central1",
) -> VertexConnectionContext:
    return VertexConnectionContext(
        project_id=project_id,
        location=location,
        auth_mode="adc",
        credentials=AnonymousCredentials(),
    )


@pytest.fixture(scope="module")
def test_private_key_pem() -> str:
    """测试专用 RSA 私钥（仅存在于测试进程中，非生产凭据）。"""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()


class TestCreateChatModelVertex:
    def test_uses_native_client_with_explicit_connection(self) -> None:
        context = _context()
        model = create_chat_model(
            ModelConfig(
                provider_type="google-vertex",
                base_url="",
                api_key="",
                model_id="gemini-3.5-flash",
                reasoning_effort="max",
                vertex_connection=context,
            )
        )

        from langchain_google_genai import ChatGoogleGenerativeAI

        assert isinstance(model, ChatGoogleGenerativeAI)
        assert model.vertexai is True
        assert model.project == "my-gcp-project"
        assert model.location == "us-central1"
        # 凭据对象按身份注入，不做复制。
        assert model.credentials is context.credentials
        assert model.max_retries == 0
        assert model.thinking_level == "high"

    def test_missing_connection_raises(self) -> None:
        with pytest.raises(ValueError, match="Vertex 连接上下文不可用"):
            create_chat_model(
                ModelConfig(
                    provider_type="google-vertex",
                    base_url="",
                    api_key="",
                    model_id="gemini-3.5-flash",
                )
            )

    def test_never_passes_api_key_parameters(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured: dict = {}

        class FakeChatGoogleGenerativeAI:
            def __init__(self, **kwargs: object) -> None:
                captured.update(kwargs)

        monkeypatch.setattr(
            "langchain_google_genai.ChatGoogleGenerativeAI", FakeChatGoogleGenerativeAI
        )
        create_chat_model(
            ModelConfig(
                provider_type="google-vertex",
                base_url="",
                api_key="",
                model_id="gemini-3.5-flash",
                temperature=0.7,
                top_p=0.9,
                top_k=32,
                max_tokens=4096,
                vertex_connection=_context(),
            )
        )

        # 禁止传入任何 API Key 参数（第 3.1 节 SDK 边界）。
        assert "google_api_key" not in captured
        assert "api_key" not in captured
        assert captured["vertexai"] is True
        assert captured["project"] == "my-gcp-project"
        assert captured["location"] == "us-central1"
        assert captured["model"] == "gemini-3.5-flash"
        assert captured["temperature"] == 0.7
        assert captured["top_p"] == 0.9
        assert captured["top_k"] == 32
        assert captured["max_output_tokens"] == 4096
        assert captured["max_retries"] == 0

    def test_env_interference_does_not_flip_explicit_params(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("GOOGLE_API_KEY", "env-api-key-sentinel")
        monkeypatch.setenv("GOOGLE_GENAI_USE_VERTEXAI", "false")
        monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "other-env-project")
        monkeypatch.setenv("GOOGLE_CLOUD_LOCATION", "europe-west1")

        context = _context()
        model = create_chat_model(
            ModelConfig(
                provider_type="google-vertex",
                base_url="",
                api_key="",
                model_id="gemini-3.5-flash",
                vertex_connection=context,
            )
        )

        from langchain_google_genai import ChatGoogleGenerativeAI

        assert isinstance(model, ChatGoogleGenerativeAI)
        # 显式参数优先于环境变量；不采用环境中的 Gemini API Key。
        assert model.vertexai is True
        assert model.project == "my-gcp-project"
        assert model.location == "us-central1"
        assert model.credentials is context.credentials


class TestExistingGoogleBackendPinned:
    """3.1 节已知边界回归：既有 Google 分支显式固定 Developer API 后端。"""

    def test_google_genai_pinned_to_developer_api(self) -> None:
        model = create_chat_model(
            ModelConfig(
                provider_type="google-genai",
                base_url="",
                api_key="google-api-key",
                model_id="gemini-2.0-flash",
            )
        )

        from langchain_google_genai import ChatGoogleGenerativeAI

        assert isinstance(model, ChatGoogleGenerativeAI)
        assert model.vertexai is False

    def test_gemini_compatible_pinned_to_developer_api(self) -> None:
        model = create_chat_model(
            ModelConfig(
                provider_type="gemini-compatible",
                base_url="https://gateway.example/gemini",
                api_key="gateway-key",
                model_id="gemini-custom",
            )
        )

        from langchain_google_genai import ChatGoogleGenerativeAI

        assert isinstance(model, ChatGoogleGenerativeAI)
        assert model.vertexai is False


class TestConfigSanitization:
    def test_model_config_repr_excludes_connection(self) -> None:
        config = ModelConfig(
            provider_type="google-vertex",
            base_url="",
            api_key="",
            model_id="gemini-3.5-flash",
            vertex_connection=_context(),
        )
        assert "vertex_connection" not in repr(config)
        assert "credentials" not in repr(config)

    def test_without_api_key_strips_vertex_connection(self) -> None:
        full_config = {
            "provider_type": "google-vertex",
            "api_key": "sk-sentinel",
            "custom_headers": {"X-Token": "sentinel"},
            "model_id": "gemini-3.5-flash",
            "vertex_connection": _context(),
        }

        persisted = without_api_key(full_config)

        assert "vertex_connection" not in persisted
        assert "api_key" not in persisted
        assert "custom_headers" not in persisted
        # 脱敏后的配置可安全序列化（checkpoint、fork、事件等写入点）。
        assert json.dumps(persisted)

    def test_full_config_is_not_json_serializable_before_sanitization(self) -> None:
        full_config = {
            "provider_type": "google-vertex",
            "vertex_connection": _context(),
        }
        with pytest.raises(TypeError):
            json.dumps(full_config)

    def test_to_client_model_config_keeps_connection_for_client(self) -> None:
        context = _context()
        client_config = to_client_model_config(
            {
                "provider_type": "google-vertex",
                "model_record_id": "record-1",
                "model_id": "gemini-3.5-flash",
                "vertex_connection": context,
            }
        )
        assert client_config["vertex_connection"] is context
        assert "model_record_id" not in client_config


class TestLLMClientPlumbing:
    def test_llm_client_passes_connection_to_model_factory(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured: dict = {}

        def fake_create_chat_model(config: ModelConfig):
            captured["config"] = config
            return object()

        monkeypatch.setattr(
            llm_client_module, "create_chat_model", fake_create_chat_model
        )
        from app.models.clients.llm_client import LLMClient, LLMConfig

        context = _context()
        client = LLMClient(
            LLMConfig(
                provider_type="google-vertex",
                base_url="",
                api_key="",
                model_id="gemini-3.5-flash",
                vertex_connection=context,
            )
        )
        client._get_llm()

        assert captured["config"].vertex_connection is context


class TestServiceResolveVertexConnectionContext:
    @pytest.fixture
    def provider_service(self) -> ModelProviderService:
        return ModelProviderService(EncryptionService(generate_encryption_key()))

    def _vertex_provider(
        self,
        *,
        provider_config: dict | None = None,
        credentials_encrypted: str = "",
    ) -> ModelProvider:
        return ModelProvider(
            name="Vertex",
            provider_type="google-vertex",
            url="",
            api_key_encrypted="",
            provider_config=provider_config
            if provider_config is not None
            else {
                "project_id": "my-gcp-project",
                "location": "us-central1",
                "auth_mode": "service_account",
            },
            credentials_encrypted=credentials_encrypted,
        )

    async def test_service_account_roundtrip(
        self, provider_service: ModelProviderService, test_private_key_pem: str
    ) -> None:
        raw = json.dumps(
            {
                "type": "service_account",
                "project_id": "sa-project",
                "private_key": test_private_key_pem,
                "client_email": "test@sa-project.iam.gserviceaccount.com",
                "token_uri": "https://oauth2.googleapis.com/token",
            }
        )
        provider = self._vertex_provider(
            credentials_encrypted=provider_service.encryption_service.encrypt(raw)
        )

        context = await provider_service.resolve_vertex_connection_context(provider)

        assert context.project_id == "my-gcp-project"
        assert context.location == "us-central1"
        assert isinstance(context.credentials, service_account.Credentials)

    async def test_adc_mode(
        self, provider_service: ModelProviderService, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            google.auth, "default", lambda scopes=None: (AnonymousCredentials(), None)
        )
        provider = self._vertex_provider(
            provider_config={
                "project_id": "my-gcp-project",
                "location": "global",
                "auth_mode": "adc",
            }
        )

        context = await provider_service.resolve_vertex_connection_context(provider)

        assert isinstance(context.credentials, AnonymousCredentials)
        assert context.location == "global"

    async def test_missing_config_raises(
        self, provider_service: ModelProviderService
    ) -> None:
        provider = self._vertex_provider(provider_config={})
        with pytest.raises(VertexConfigError, match="缺少 Vertex 配置"):
            await provider_service.resolve_vertex_connection_context(provider)

    async def test_decrypt_failure_raises_invalid(
        self, provider_service: ModelProviderService
    ) -> None:
        provider = self._vertex_provider(credentials_encrypted="not-a-valid-token")
        with pytest.raises(VertexAuthError, match="解密失败") as exc_info:
            await provider_service.resolve_vertex_connection_context(provider)
        assert exc_info.value.error_code == VERTEX_ERROR_CREDENTIALS_INVALID
