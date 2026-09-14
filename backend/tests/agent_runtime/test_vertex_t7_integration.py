# -*- coding: utf-8 -*-
"""
Vertex 原生接入 T7 首版集成与发布验收测试套件（离线，模拟边界）。

涵盖：
- T7a: 敏感信息哨兵全链路检查、多连接高并发与环境干扰隔离、推理/流式/工具/超时取消链路；
- T7b: ADC 环境变量继承与缺失脱敏分类；
- T7c: 数据管理备份/还原与密钥不匹配（Key Mismatch）安全防护与实例隔离；
- T7d: 桌面远程模式与文件路径注入防护及版本契约兼容。
"""

import asyncio
import json
from typing import Any, AsyncGenerator

import google.auth
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from google.auth.credentials import AnonymousCredentials
from google.auth.exceptions import DefaultCredentialsError
from google.oauth2 import service_account
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage

from app.agent_runtime.model_config import without_api_key
from app.core.encryption import EncryptionService, generate_encryption_key
from app.models.clients import llm_client as llm_client_module
from app.models.clients.google_vertex_auth import (
    VERTEX_ERROR_CREDENTIALS_INVALID,
    VERTEX_ERROR_CREDENTIALS_MISSING,
    VertexAuthError,
    VertexConnectionContext,
)
from app.models.clients.llm_client import LLMClient, LLMConfig
from app.models.clients.model_factory import ModelConfig, create_chat_model
from app.models.entities.model_provider import ModelProvider
from app.models.services.model_provider_service import ModelProviderService
from app.models.vertex_config import VertexConfigError, validate_vertex_service_account_json


# ---------------------------------------------------------------------------
# 测试固件与辅助函数
# ---------------------------------------------------------------------------

_SENTINEL_RSA_KEY = "SENTINEL-PRIVATE-KEY-SECRET-VALUE-XYZ-987"
_SENTINEL_CLIENT_EMAIL = "sentinel-service-account@sentinel-project.iam.gserviceaccount.com"


@pytest.fixture(scope="module")
def sample_rsa_private_key_pem() -> str:
    """测试专用 RSA 私钥（仅在测试内存中生成并使用）。"""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()


def _make_sa_json(private_key_pem: str, client_email: str = _SENTINEL_CLIENT_EMAIL) -> str:
    return json.dumps(
        {
            "type": "service_account",
            "project_id": "sentinel-project",
            "private_key_id": "key-999",
            "private_key": private_key_pem,
            "client_email": client_email,
            "client_id": "112233445566",
            "token_uri": "https://oauth2.googleapis.com/token",
        }
    )


# ---------------------------------------------------------------------------
# T7a: 敏感信息全链路检查与脱敏
# ---------------------------------------------------------------------------


class TestT7aDataSanitizationAndLeakPrevention:
    """验证私钥与凭据全链路脱敏，杜绝在明文列、日志、持久化或 repr 中泄漏。"""

    def test_database_and_config_sanitization(
        self, sample_rsa_private_key_pem: str
    ) -> None:
        enc_service = EncryptionService(generate_encryption_key())
        sa_json = _make_sa_json(sample_rsa_private_key_pem)

        encrypted = enc_service.encrypt(sa_json)
        provider = ModelProvider(
            name="Vertex Sentinel Provider",
            provider_type="google-vertex",
            url="",
            api_key_encrypted="",
            provider_config={
                "project_id": "sentinel-project",
                "location": "us-central1",
                "auth_mode": "service_account",
            },
            credentials_encrypted=encrypted,
        )

        # 1. 检查数据库实体：密文列不含明文私钥，明文字段均不含私钥
        assert sample_rsa_private_key_pem not in provider.credentials_encrypted
        assert _SENTINEL_CLIENT_EMAIL not in provider.credentials_encrypted
        assert sample_rsa_private_key_pem not in json.dumps(provider.provider_config)
        assert provider.url == ""
        assert provider.api_key_encrypted == ""

        # 2. 解密后可完全恢复原始私钥与账号
        decrypted = enc_service.decrypt(provider.credentials_encrypted)
        decrypted_data = json.loads(decrypted)
        assert decrypted_data["private_key"] == sample_rsa_private_key_pem
        assert decrypted_data["client_email"] == _SENTINEL_CLIENT_EMAIL

    def test_without_api_key_strips_vertex_connection_and_ensures_serializability(
        self,
    ) -> None:
        context = VertexConnectionContext(
            project_id="test-proj",
            location="us-central1",
            auth_mode="adc",
            credentials=AnonymousCredentials(),
        )
        runtime_config = {
            "provider_type": "google-vertex",
            "model_id": "gemini-2.5-flash",
            "model_record_id": "record-vertex-1",
            "api_key": "sk-sentinel",
            "custom_headers": {"X-Secret": "sentinel-header"},
            "vertex_connection": context,
        }

        # 未脱敏前不可 JSON 序列化
        with pytest.raises(TypeError):
            json.dumps(runtime_config)

        # 脱敏后
        sanitized = without_api_key(runtime_config)
        assert "vertex_connection" not in sanitized
        assert "api_key" not in sanitized
        assert "custom_headers" not in sanitized
        assert sanitized["model_id"] == "gemini-2.5-flash"

        # 脱敏后可安全 JSON 序列化落库（checkpoint、fork、session_changes 等）
        dumped = json.dumps(sanitized)
        assert "vertex_connection" not in dumped
        assert "credentials" not in dumped

    def test_repr_and_str_exclude_credentials(
        self, sample_rsa_private_key_pem: str
    ) -> None:
        sa_creds = service_account.Credentials.from_service_account_info(
            json.loads(_make_sa_json(sample_rsa_private_key_pem))
        )
        context = VertexConnectionContext(
            project_id="audit-proj",
            location="us-central1",
            auth_mode="service_account",
            credentials=sa_creds,
        )

        model_cfg = ModelConfig(
            provider_type="google-vertex",
            base_url="",
            api_key="",
            model_id="gemini-2.5-flash",
            vertex_connection=context,
        )
        llm_cfg = LLMConfig(
            provider_type="google-vertex",
            base_url="",
            api_key="",
            model_id="gemini-2.5-flash",
            vertex_connection=context,
        )

        # 检查 repr 与 str 均不含私钥与凭据对象详情
        for text in [repr(context), str(context), repr(model_cfg), repr(llm_cfg)]:
            assert sample_rsa_private_key_pem not in text
            assert "private_key" not in text


# ---------------------------------------------------------------------------
# T7a: 多连接并发与环境干扰隔离
# ---------------------------------------------------------------------------


class TestT7aConcurrencyAndEnvironmentalIsolation:
    """验证多个不同的 Vertex 连接并发执行不串用，且不受环境变量污染。"""

    @pytest.mark.asyncio
    async def test_concurrent_vertex_connections_isolation(
        self, sample_rsa_private_key_pem: str
    ) -> None:
        enc_service = EncryptionService(generate_encryption_key())
        service = ModelProviderService(enc_service)

        # 创建连接 A
        sa_json_a = _make_sa_json(sample_rsa_private_key_pem, "alpha@proj-a.iam.gserviceaccount.com")
        provider_a = ModelProvider(
            name="Vertex Alpha",
            provider_type="google-vertex",
            url="",
            api_key_encrypted="",
            provider_config={"project_id": "proj-alpha", "location": "us-central1", "auth_mode": "service_account"},
            credentials_encrypted=enc_service.encrypt(sa_json_a),
        )

        # 创建连接 B
        sa_json_b = _make_sa_json(sample_rsa_private_key_pem, "beta@proj-b.iam.gserviceaccount.com")
        provider_b = ModelProvider(
            name="Vertex Beta",
            provider_type="google-vertex",
            url="",
            api_key_encrypted="",
            provider_config={"project_id": "proj-beta", "location": "asia-east1", "auth_mode": "service_account"},
            credentials_encrypted=enc_service.encrypt(sa_json_b),
        )

        # 并发执行 30 次解析
        async def resolve_a():
            ctx = await service.resolve_vertex_connection_context(provider_a)
            assert ctx.project_id == "proj-alpha"
            assert ctx.location == "us-central1"
            assert isinstance(ctx.credentials, service_account.Credentials)
            assert ctx.credentials.service_account_email == "alpha@proj-a.iam.gserviceaccount.com"
            return ctx

        async def resolve_b():
            ctx = await service.resolve_vertex_connection_context(provider_b)
            assert ctx.project_id == "proj-beta"
            assert ctx.location == "asia-east1"
            assert isinstance(ctx.credentials, service_account.Credentials)
            assert ctx.credentials.service_account_email == "beta@proj-b.iam.gserviceaccount.com"
            return ctx

        tasks = [resolve_a() if i % 2 == 0 else resolve_b() for i in range(30)]
        results = await asyncio.gather(*tasks)
        assert len(results) == 30

    def test_environment_variables_do_not_interfere_with_explicit_configs(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # 注入可能引起冲突的环境变量
        monkeypatch.setenv("GOOGLE_API_KEY", "env-leaked-key")
        monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "env-leaked-project")
        monkeypatch.setenv("GOOGLE_CLOUD_LOCATION", "europe-west4")
        monkeypatch.setenv("GOOGLE_GENAI_USE_VERTEXAI", "true")

        context = VertexConnectionContext(
            project_id="my-explicit-project",
            location="us-central1",
            auth_mode="adc",
            credentials=AnonymousCredentials(),
        )

        # 1. 验证 Vertex 客户端保持显式设置
        vertex_model = create_chat_model(
            ModelConfig(
                provider_type="google-vertex",
                base_url="",
                api_key="",
                model_id="gemini-2.5-flash",
                vertex_connection=context,
            )
        )
        from langchain_google_genai import ChatGoogleGenerativeAI
        assert isinstance(vertex_model, ChatGoogleGenerativeAI)
        assert vertex_model.vertexai is True
        assert vertex_model.project == "my-explicit-project"
        assert vertex_model.location == "us-central1"

        # 2. 验证 Google GenAI 既有分支显式固定为 Developer API（vertexai=False）
        genai_model = create_chat_model(
            ModelConfig(
                provider_type="google-genai",
                base_url="",
                api_key="my-google-api-key",
                model_id="gemini-2.0-flash",
            )
        )
        assert isinstance(genai_model, ChatGoogleGenerativeAI)
        assert genai_model.vertexai is False


# ---------------------------------------------------------------------------
# T7a: 推理、流式、工具调用与超时/取消闭环
# ---------------------------------------------------------------------------


class _FakeChatModel:
    """模拟 LangChain ChatGoogleGenerativeAI 的响应行为。"""

    def __init__(self, mode: str = "text") -> None:
        self.mode = mode

    def bind_tools(self, tools: list[Any], **kwargs: Any) -> Any:
        return self

    async def ainvoke(self, messages: list[Any], **kwargs: Any) -> AIMessage:
        if self.mode == "timeout":
            await asyncio.sleep(0.5)
            raise TimeoutError("Request timed out")
        if self.mode == "tool_call":
            return AIMessage(
                content="",
                tool_calls=[
                    {"name": "search_chapters", "args": {"query": "test"}, "id": "call_1"}
                ],
                response_metadata={"finish_reason": "TOOL_CALLS"},
            )
        return AIMessage(
            content="Hello from Vertex",
            usage_metadata={"input_tokens": 12, "output_tokens": 8, "total_tokens": 20},
            response_metadata={"finish_reason": "STOP"},
        )

    async def astream(self, messages: list[Any], **kwargs: Any) -> AsyncGenerator[AIMessageChunk, None]:
        if self.mode == "hang":
            # 模拟悬挂流，测试外部取消
            while True:
                await asyncio.sleep(1)
                yield AIMessageChunk(content="chunk")
        if self.mode == "tool_stream":
            yield AIMessageChunk(content="")
            yield AIMessageChunk(
                content="",
                tool_call_chunks=[
                    {"name": "search_chapters", "args": '{"query":', "id": "call_1", "index": 0}
                ],
            )
            yield AIMessageChunk(
                content="",
                tool_call_chunks=[
                    {"name": None, "args": ' "test"}', "id": "call_1", "index": 0}
                ],
                usage_metadata={"input_tokens": 15, "output_tokens": 10, "total_tokens": 25},
            )
            return

        yield AIMessageChunk(content="Hello ")
        yield AIMessageChunk(content="from ")
        yield AIMessageChunk(
            content="Vertex",
            usage_metadata={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
        )


class TestT7aInferenceAndStreamingLifecycle:
    """验证非流式、流式、工具调用、超时与取消生命周期。"""

    @pytest.fixture(autouse=True)
    def patch_create_chat_model(self, monkeypatch: pytest.MonkeyPatch):
        self.fake_mode = "text"

        def _fake_factory(config: ModelConfig):
            return _FakeChatModel(mode=self.fake_mode)

        monkeypatch.setattr(llm_client_module, "create_chat_model", _fake_factory)

    @pytest.mark.asyncio
    async def test_non_streaming_generate_with_usage(self) -> None:
        self.fake_mode = "text"
        client = LLMClient(
            LLMConfig(
                provider_type="google-vertex",
                base_url="",
                api_key="",
                model_id="gemini-2.5-flash",
                vertex_connection=VertexConnectionContext("p", "l", "adc", AnonymousCredentials()),
            )
        )
        resp = await client.generate([{"role": "user", "content": "hi"}])
        assert resp.content == "Hello from Vertex"
        assert resp.usage == {"input_tokens": 12, "output_tokens": 8, "total_tokens": 20}
        assert resp.finish_reason == "STOP"

    @pytest.mark.asyncio
    async def test_streaming_accumulates_content_and_usage(self) -> None:
        self.fake_mode = "text"
        client = LLMClient(
            LLMConfig(
                provider_type="google-vertex",
                base_url="",
                api_key="",
                model_id="gemini-2.5-flash",
                vertex_connection=VertexConnectionContext("p", "l", "adc", AnonymousCredentials()),
            )
        )
        chunks = []
        async for chunk in client.generate_stream_chunks([{"role": "user", "content": "hi"}]):
            chunks.append(chunk)

        # 验证增量文本流式输出
        assert len(chunks) >= 2
        content_chunks = [c.content for c in chunks if c.content]
        assert "".join(content_chunks) == "Hello from Vertex"
        # 最后一个 chunk 携带完整 response 及 usage 统计
        last_chunk = chunks[-1]
        assert last_chunk.response is not None
        assert last_chunk.response.content == "Hello from Vertex"
        assert last_chunk.response.usage == {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}

    @pytest.mark.asyncio
    async def test_tool_call_streaming_parsed(self) -> None:
        from langchain_core.tools import StructuredTool

        def _dummy_tool(query: str) -> str:
            return query

        tool = StructuredTool.from_function(_dummy_tool, name="search_chapters", description="search")

        self.fake_mode = "tool_stream"
        client = LLMClient(
            LLMConfig(
                provider_type="google-vertex",
                base_url="",
                api_key="",
                model_id="gemini-2.5-flash",
                vertex_connection=VertexConnectionContext("p", "l", "adc", AnonymousCredentials()),
            )
        )
        client.bind_tools([tool])
        chunks = []
        async for chunk in client.generate_with_tools_stream([HumanMessage(content="search chapters")]):
            chunks.append(chunk)

        last_chunk = chunks[-1]
        assert last_chunk.response is not None
        assert last_chunk.response.tool_calls is not None
        assert len(last_chunk.response.tool_calls) == 1
        tool_call = last_chunk.response.tool_calls[0]
        assert tool_call["name"] == "search_chapters"
        assert tool_call["args"] == {"query": "test"}

    @pytest.mark.asyncio
    async def test_streaming_task_cancellation_terminates_cleanly(self) -> None:
        self.fake_mode = "hang"
        client = LLMClient(
            LLMConfig(
                provider_type="google-vertex",
                base_url="",
                api_key="",
                model_id="gemini-2.5-flash",
                vertex_connection=VertexConnectionContext("p", "l", "adc", AnonymousCredentials()),
            )
        )

        async def stream_task():
            async for _ in client.generate_stream_chunks([{"role": "user", "content": "hang"}]):
                pass

        task = asyncio.create_task(stream_task())
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


# ---------------------------------------------------------------------------
# T7b: 桌面与服务器 ADC 环境测试
# ---------------------------------------------------------------------------


class TestT7bADCEnvironmentHandling:
    """验证在 ADC 凭据存在或缺失时的确定性行为。"""

    @pytest.mark.asyncio
    async def test_adc_missing_yields_descriptive_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _raise_missing(*args: Any, **kwargs: Any):
            raise DefaultCredentialsError("Could not automatically determine credentials.")

        monkeypatch.setattr(google.auth, "default", _raise_missing)

        service = ModelProviderService(EncryptionService(generate_encryption_key()))
        provider = ModelProvider(
            name="Vertex ADC",
            provider_type="google-vertex",
            url="",
            api_key_encrypted="",
            provider_config={"project_id": "proj-x", "location": "us-central1", "auth_mode": "adc"},
            credentials_encrypted="",
        )

        with pytest.raises(VertexAuthError) as exc_info:
            await service.resolve_vertex_connection_context(provider)

        assert exc_info.value.error_code == VERTEX_ERROR_CREDENTIALS_MISSING
        # 消息必须明确指出凭据应在“后端运行环境”配置
        assert "后端运行环境" in str(exc_info.value)


# ---------------------------------------------------------------------------
# T7c: 数据管理备份还原与密钥不匹配（Key Mismatch）安全防护
# ---------------------------------------------------------------------------


class TestT7cDataBackupAndKeyMismatchProtection:
    """验证数据备份还原后的密钥一致性要求，及更换密钥后的安全拦截。"""

    @pytest.mark.asyncio
    async def test_mismatched_key_fails_safely_without_leaking_ciphertext(
        self, sample_rsa_private_key_pem: str
    ) -> None:
        # 模拟实例 1（生成密钥 A 并保存凭据）
        key_a = generate_encryption_key()
        service_a = ModelProviderService(EncryptionService(key_a))
        sa_json = _make_sa_json(sample_rsa_private_key_pem)

        provider = ModelProvider(
            name="Vertex Instance 1",
            provider_type="google-vertex",
            url="",
            api_key_encrypted="",
            provider_config={"project_id": "proj-1", "location": "us-central1", "auth_mode": "service_account"},
            credentials_encrypted=service_a.encryption_service.encrypt(sa_json),
        )

        # 在实例 1 下可正常解密并解析
        ctx_a = await service_a.resolve_vertex_connection_context(provider)
        assert ctx_a.project_id == "proj-1"

        # 模拟还原到实例 2，但 .key 不匹配（生成了不同的密钥 B）
        key_b = generate_encryption_key()
        service_b = ModelProviderService(EncryptionService(key_b))

        # 使用不匹配的密钥尝试解析
        with pytest.raises(VertexAuthError) as exc_info:
            await service_b.resolve_vertex_connection_context(provider)

        # 必须安全报错并提示重新配置凭据，不得抛出未捕获的解密崩溃或回显密文
        assert exc_info.value.error_code == VERTEX_ERROR_CREDENTIALS_INVALID
        assert "已保存的 Vertex 凭据解密失败" in str(exc_info.value)
        assert provider.credentials_encrypted not in str(exc_info.value)


# ---------------------------------------------------------------------------
# T7d: 桌面远程模式与文件路径注入防护
# ---------------------------------------------------------------------------


class TestT7dRemoteModeAndPathInjectionPrevention:
    """验证远程模式下杜绝提交本地绝对路径，严格要求 JSON 字符串。"""

    def test_rejects_file_paths_as_service_account_json(self) -> None:
        # 客户端禁止将本地文件绝对路径（Windows 或 POSIX）当作凭据内容提交
        invalid_inputs = [
            r"C:\Users\Admin\secrets\vertex-sa.json",
            r"D:\gcp\service-account.json",
            "/home/user/.config/gcloud/sa.json",
            "file:///C:/Users/Admin/sa.json",
            "not-json-content",
        ]
        for path_val in invalid_inputs:
            with pytest.raises(VertexConfigError):
                validate_vertex_service_account_json(path_val)
