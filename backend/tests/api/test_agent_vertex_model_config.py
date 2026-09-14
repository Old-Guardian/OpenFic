# -*- coding: utf-8 -*-
"""
Vertex 模型配置解析入口测试（离线）。

覆盖：主 Agent（_resolve_model_config）、子 Agent
（_build_model_config_from_record）、后台任务（resolve_background_llm）
三个调用入口的 Vertex 连接解析与错误路径；持久化产物不含机密。
"""

import json

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.model_config import without_api_key
from app.api.routers.agent_runtime import _resolve_model_config
from app.background.llm.resolver import resolve_background_llm
from app.core.errors import NotFoundError
from app.core.encryption import EncryptionService
from app.models.repos import model_provider_repo, model_repo
from app.models.vertex_config import VERTEX_PROVIDER_TYPE
from app.settings import settings


@pytest.fixture(scope="module")
def test_private_key_pem() -> str:
    """测试专用 RSA 私钥（仅存在于测试进程中，非生产凭据）。"""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()


def _service_account_json(private_key: str) -> str:
    return json.dumps(
        {
            "type": "service_account",
            "project_id": "sa-project",
            "private_key": private_key,
            "client_email": "test@sa-project.iam.gserviceaccount.com",
            "token_uri": "https://oauth2.googleapis.com/token",
        }
    )


async def _create_vertex_records(
    session: AsyncSession,
    *,
    private_key: str | None = None,
    provider_config: dict | None = None,
    credentials_encrypted: str | None = None,
) -> tuple:
    """创建 Vertex 提供商与模型记录，返回 (model, provider)。"""
    if credentials_encrypted is None and private_key is not None:
        encryption_service = EncryptionService(settings.encryption_key)
        credentials_encrypted = encryption_service.encrypt(
            _service_account_json(private_key)
        )
    provider = await model_provider_repo.create(
        session=session,
        name="Vertex Test",
        url="",
        api_key_encrypted="",
        provider_type=VERTEX_PROVIDER_TYPE,
        custom_headers_encrypted="",
        provider_config=provider_config
        if provider_config is not None
        else {
            "project_id": "my-gcp-project",
            "location": "us-central1",
            "auth_mode": "service_account",
        },
        credentials_encrypted=credentials_encrypted or "",
    )
    model = await model_repo.create(
        session=session,
        name="Gemini 3.5 Flash",
        provider_id=provider.id,
        model_id="gemini-3.5-flash",
        task_type="llm",
        context_length=1048576,
    )
    await session.commit()
    return model, provider


class TestResolveModelConfig:
    async def test_vertex_resolves_connection_context(
        self, session: AsyncSession, test_private_key_pem: str
    ) -> None:
        model, _provider = await _create_vertex_records(
            session, private_key=test_private_key_pem
        )

        config = await _resolve_model_config(session, model.id)

        assert config["provider_type"] == VERTEX_PROVIDER_TYPE
        assert config["api_key"] == ""
        context = config["vertex_connection"]
        assert context.project_id == "my-gcp-project"
        assert context.location == "us-central1"
        # 持久化形态不含连接上下文，且可序列化。
        persisted = without_api_key(config)
        assert "vertex_connection" not in persisted
        assert json.dumps(persisted)

    async def test_vertex_without_config_raises_clear_error(
        self, session: AsyncSession
    ) -> None:
        # 旧目录遗留连接：无 provider_config，恢复/启动时明确报错。
        model, _provider = await _create_vertex_records(session, provider_config={})

        with pytest.raises(ValueError, match="缺少 Vertex 配置"):
            await _resolve_model_config(session, model.id)

    async def test_vertex_corrupt_credentials_raises_clear_error(
        self, session: AsyncSession
    ) -> None:
        model, _provider = await _create_vertex_records(
            session,
            provider_config=None,
            credentials_encrypted="corrupt-cipher-text",
        )

        with pytest.raises(ValueError, match="解密失败"):
            await _resolve_model_config(session, model.id)

    async def test_deleted_provider_raises_not_found(
        self, session: AsyncSession, test_private_key_pem: str
    ) -> None:
        model, provider = await _create_vertex_records(
            session, private_key=test_private_key_pem
        )
        await model_provider_repo.delete_by_id(session, provider.id)
        await session.commit()

        # 恢复运行时依据记录重新加载：连接删除后明确报错，不读取其他连接替代。
        with pytest.raises(NotFoundError, match="模型提供商不存在"):
            await _resolve_model_config(session, model.id)


class TestSubagentModelConfig:
    async def test_vertex_record_resolves_connection_context(
        self, session: AsyncSession, test_private_key_pem: str
    ) -> None:
        from app.agent_runtime.runner.subagent_runner import (
            _build_model_config_from_record,
        )

        model, _provider = await _create_vertex_records(
            session, private_key=test_private_key_pem
        )

        config = await _build_model_config_from_record(session, model.id)

        assert config is not None
        assert config["provider_type"] == VERTEX_PROVIDER_TYPE
        assert config["api_key"] == ""
        assert config["vertex_connection"].project_id == "my-gcp-project"
        assert "vertex_connection" not in without_api_key(config)


class TestBackgroundResolver:
    async def test_vertex_background_llm_resolves_connection(
        self, session: AsyncSession, test_private_key_pem: str
    ) -> None:
        model, _provider = await _create_vertex_records(
            session, private_key=test_private_key_pem
        )

        resolved = await resolve_background_llm(
            session, model_policy="default_model", model_id=model.id
        )

        assert resolved.provider.provider_type == VERTEX_PROVIDER_TYPE
        assert resolved.client.config.vertex_connection is not None
        assert resolved.client.config.vertex_connection.project_id == "my-gcp-project"
        assert resolved.client.config.api_key == ""

    async def test_non_vertex_background_llm_unchanged(
        self, session: AsyncSession
    ) -> None:
        provider = await model_provider_repo.create(
            session=session,
            name="OpenAI Test",
            url="https://api.openai.com/v1",
            api_key_encrypted=EncryptionService(settings.encryption_key).encrypt(
                "sk-test"
            ),
            provider_type="openai",
            custom_headers_encrypted="",
        )
        model = await model_repo.create(
            session=session,
            name="GPT Test",
            provider_id=provider.id,
            model_id="gpt-4o",
            task_type="llm",
        )
        await session.commit()

        resolved = await resolve_background_llm(
            session, model_policy="default_model", model_id=model.id
        )

        assert resolved.client.config.api_key == "sk-test"
        assert resolved.client.config.vertex_connection is None
