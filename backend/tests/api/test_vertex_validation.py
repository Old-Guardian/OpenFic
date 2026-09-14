# -*- coding: utf-8 -*-
"""
Vertex 连接验证 API 测试（T5，离线，模拟 SDK 边界）。

覆盖：草稿与保存凭据合并、用户指定模型的最小调用、错误码分类
（凭据/区域/模型/限流/超时/拦截）、验证范围、验证不落库、响应脱敏
（实施计划第 4.2、6.2 节）。
"""

import json

import google.auth
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from google.auth.credentials import AnonymousCredentials
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.repos import model_provider_repo

_SA_PRIVATE_KEY_SENTINEL = "SENTINEL-vertex-validation-private-key"


@pytest.fixture(scope="module")
def test_private_key_pem() -> str:
    """测试专用 RSA 私钥（仅存在于测试进程中，非生产凭据）。"""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()


def _vertex_config(auth_mode: str = "adc", **overrides: str) -> str:
    payload = {
        "project_id": "my-gcp-project",
        "location": "us-central1",
        "auth_mode": auth_mode,
    }
    payload.update(overrides)
    return json.dumps(payload)


def _service_account_json(private_key: str = _SA_PRIVATE_KEY_SENTINEL) -> str:
    return json.dumps(
        {
            "type": "service_account",
            "project_id": "sa-project",
            "private_key_id": "key-id",
            "private_key": private_key,
            "client_email": "test@sa-project.iam.gserviceaccount.com",
            "client_id": "123456789",
            "token_uri": "https://oauth2.googleapis.com/token",
        }
    )


class _FakeLLMResponse:
    """模拟 LLMClient.generate 的响应对象。"""

    def __init__(self, content: str) -> None:
        self.content = content
        self.reasoning_content = ""
        self.finish_reason = "STOP"
        self.usage = {}
        self.tool_calls = None
        self.first_token_ms = 1


def _patch_llm_client_import(monkeypatch: pytest.MonkeyPatch, behavior) -> dict:
    """
    拦截 Vertex 验证的模型调用边界，记录调用参数。

    behavior 可以是：
    - str：作为响应内容返回（验证成功路径）；
    - Exception：直接抛出（错误分类路径）。
    返回捕获到的调用记录字典。
    """
    from app.models.clients.llm_client import LLMClient

    captured: dict = {}

    async def fake_generate(self, messages, timeout=None):
        captured["messages"] = messages
        captured["timeout"] = timeout
        captured["config"] = self.config
        if isinstance(behavior, Exception):
            raise behavior
        return _FakeLLMResponse(behavior)

    monkeypatch.setattr(LLMClient, "generate", fake_generate)
    return captured


def _validate_payload(
    *,
    # None 表示默认携带 ADC 草稿配置；"" 表示明确省略（用于缺省路径测试）。
    provider_config: str | None = None,
    credentials_action: str = "keep",
    service_account_json: str | None = None,
    model_id: str = "gemini-3.5-flash",
    provider_id: str | None = None,
) -> dict:
    payload: dict = {
        "provider_type": "google-vertex",
        "credentials_action": credentials_action,
        "model_id": model_id,
        "provider_config": provider_config
        if provider_config is not None
        else _vertex_config("adc"),
    }
    if service_account_json is not None:
        payload["service_account_json"] = service_account_json
    if provider_id is not None:
        payload["provider_id"] = provider_id
    return payload


async def _create_saved_vertex(
    client: AsyncClient,
    *,
    auth_mode: str = "service_account",
    service_account_json: str | None = None,
    credentials_action: str = "replace",
) -> str:
    """创建已保存的 Vertex 连接，返回 provider id。"""
    data = {
        "name": "Vertex Validation",
        "url": "",
        "provider_type": "google-vertex",
        "provider_config": _vertex_config(auth_mode),
        "credentials_action": credentials_action,
    }
    if service_account_json is not None:
        data["service_account_json"] = service_account_json
    response = await client.post("/api/v1/model-providers", data=data)
    assert response.status_code == 201, response.text
    return response.json()["id"]


class TestVertexValidateSuccess:
    async def test_unsaved_connection_with_draft_config_and_adc(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """未保存连接：草稿配置 + ADC（模拟凭据）即可验证，不要求先保存。"""
        monkeypatch.setattr(
            google.auth, "default", lambda scopes=None: (AnonymousCredentials(), None)
        )
        captured = _patch_llm_client_import(monkeypatch, "OK")

        response = await client.post(
            "/api/v1/model-providers/validate", json=_validate_payload()
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["validation_scope"] == "model_invocation"
        assert data["error_code"] is None
        assert data["models"][0]["id"] == "gemini-3.5-flash"
        # 固定短提示词、限制输出、不使用用户文稿。
        assert captured["messages"] == [
            {"role": "user", "content": "Reply with exactly: OK"}
        ]
        assert captured["config"].max_tokens == 16
        assert captured["config"].vertex_connection.project_id == "my-gcp-project"

    async def test_saved_service_account_connection_without_draft(
        self,
        client: AsyncClient,
        test_private_key_pem: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """已保存连接 + 省略草稿：使用已保存配置与凭据验证。"""
        provider_id = await _create_saved_vertex(
            client, service_account_json=_service_account_json(test_private_key_pem)
        )
        captured = _patch_llm_client_import(monkeypatch, "OK")

        response = await client.post(
            "/api/v1/model-providers/validate",
            json=_validate_payload(provider_config="", provider_id=provider_id),
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        # keep 沿用已保存凭据。
        assert captured["config"].vertex_connection.auth_mode == "service_account"

    async def test_edit_uses_draft_config_with_saved_credentials(
        self,
        client: AsyncClient,
        test_private_key_pem: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """编辑模式：草稿配置（新区域）+ keep 旧凭据参与验证，不落库。"""
        provider_id = await _create_saved_vertex(
            client, service_account_json=_service_account_json(test_private_key_pem)
        )
        captured = _patch_llm_client_import(monkeypatch, "OK")

        response = await client.post(
            "/api/v1/model-providers/validate",
            json=_validate_payload(
                provider_config=_vertex_config(
                    "service_account", location="europe-west1"
                ),
                provider_id=provider_id,
            ),
        )

        assert response.status_code == 200
        assert response.json()["success"] is True
        # 草稿区域参与验证。
        assert captured["config"].vertex_connection.location == "europe-west1"

        # 验证不保存草稿：数据库中配置与凭据保持原值。
        from tests.conftest import _per_test_session

        assert _per_test_session is not None
        provider = await model_provider_repo.get_by_id(_per_test_session, provider_id)
        assert provider is not None
        assert provider.provider_config["location"] == "us-central1"
        assert provider.credentials_encrypted

    async def test_replace_credentials_used_for_validation_only(
        self,
        client: AsyncClient,
        test_private_key_pem: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """replace：临时凭据参与验证但不落库。"""
        provider_id = await _create_saved_vertex(
            client, service_account_json=_service_account_json(test_private_key_pem)
        )
        _patch_llm_client_import(monkeypatch, "OK")

        # 生成第二把真实测试私钥，确保凭据解析通过。
        replacement_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        new_key = _service_account_json(
            private_key=replacement_key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption(),
            ).decode()
        )
        response = await client.post(
            "/api/v1/model-providers/validate",
            json=_validate_payload(
                provider_config=_vertex_config("service_account"),
                credentials_action="replace",
                service_account_json=new_key,
                provider_id=provider_id,
            ),
        )

        assert response.status_code == 200
        assert response.json()["success"] is True

        from tests.conftest import _per_test_session

        assert _per_test_session is not None
        provider = await model_provider_repo.get_by_id(_per_test_session, provider_id)
        assert provider is not None
        from app.core.encryption import EncryptionService
        from app.settings import settings

        decrypted = EncryptionService(settings.encryption_key).decrypt(
            provider.credentials_encrypted
        )
        # 落库的仍是旧凭据：新私钥不在，旧私钥仍在。
        assert new_key not in decrypted
        assert json.loads(decrypted)["private_key"] == test_private_key_pem


class TestVertexValidateInputErrors:
    async def test_missing_provider_config_and_provider_id(
        self, client: AsyncClient
    ) -> None:
        response = await client.post(
            "/api/v1/model-providers/validate",
            json=_validate_payload(provider_config=""),
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is False
        assert data["error_code"] == "vertex_config_invalid"
        assert "provider_config" in data["message"]

    async def test_missing_model_id_rejected(self, client: AsyncClient) -> None:
        response = await client.post(
            "/api/v1/model-providers/validate",
            json={
                "provider_type": "google-vertex",
                "provider_config": _vertex_config("adc"),
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is False
        assert data["error_code"] == "vertex_config_invalid"
        assert "model_id" in data["message"]

    async def test_unknown_provider_id_returns_404(self, client: AsyncClient) -> None:
        response = await client.post(
            "/api/v1/model-providers/validate",
            json=_validate_payload(
                provider_config=_vertex_config("adc"), provider_id="nonexistent"
            ),
        )

        assert response.status_code == 404

    async def test_non_vertex_provider_id_rejected(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        from app.core.encryption import EncryptionService
        from app.settings import settings

        provider = await model_provider_repo.create(
            session=session,
            name="OpenAI",
            url="https://api.openai.com",
            api_key_encrypted=EncryptionService(settings.encryption_key).encrypt("sk"),
            provider_type="openai",
        )
        await session.commit()

        response = await client.post(
            "/api/v1/model-providers/validate",
            json=_validate_payload(
                provider_config=_vertex_config("adc"), provider_id=provider.id
            ),
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is False
        assert data["error_code"] == "vertex_config_invalid"

    async def test_contradictory_credentials_inputs_rejected(
        self, client: AsyncClient, test_private_key_pem: str
    ) -> None:
        """keep/clear 配新凭据、replace 配空凭据均拒绝（不触发任何解析）。"""
        for action, sa_json in (
            ("keep", _service_account_json()),
            ("clear", "x"),
            ("replace", ""),
        ):
            response = await client.post(
                "/api/v1/model-providers/validate",
                json=_validate_payload(
                    provider_config=_vertex_config("service_account"),
                    credentials_action=action,
                    service_account_json=sa_json,
                ),
            )
            assert response.status_code == 200, (action, sa_json)
            assert response.json()["success"] is False

    async def test_adc_with_credentials_rejected_without_auth_resolution(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """ADC + 凭据属矛盾输入：拒绝且不触发认证解析（无回退）。"""
        resolved = False

        def fail_default(scopes=None):
            nonlocal resolved
            resolved = True
            raise AssertionError("ADC resolution must not run for contradictory input")

        monkeypatch.setattr(google.auth, "default", fail_default)
        response = await client.post(
            "/api/v1/model-providers/validate",
            json=_validate_payload(
                provider_config=_vertex_config("adc"),
                credentials_action="replace",
                service_account_json=_service_account_json(),
            ),
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is False
        assert resolved is False

    async def test_service_account_without_credentials_reports_missing(
        self, client: AsyncClient
    ) -> None:
        """service_account 模式无凭据：credentials_missing，不触发解析。"""
        response = await client.post(
            "/api/v1/model-providers/validate",
            json=_validate_payload(provider_config=_vertex_config("service_account")),
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is False
        assert data["error_code"] == "vertex_credentials_missing"

    async def test_adc_missing_reports_credentials_missing(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from google.auth.exceptions import DefaultCredentialsError

        def no_adc(scopes=None):
            raise DefaultCredentialsError("no adc in test env")

        monkeypatch.setattr(google.auth, "default", no_adc)

        response = await client.post(
            "/api/v1/model-providers/validate",
            json=_validate_payload(),
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is False
        assert data["error_code"] == "vertex_credentials_missing"
        assert "ADC" in data["message"] or "后端" in data["message"]

    async def test_invalid_service_account_reports_credentials_invalid(
        self, client: AsyncClient
    ) -> None:
        """结构非法的 Service Account：credentials_invalid。"""
        bad_json = json.dumps({"type": "service_account", "project_id": "p"})
        response = await client.post(
            "/api/v1/model-providers/validate",
            json=_validate_payload(
                provider_config=_vertex_config("service_account"),
                credentials_action="replace",
                service_account_json=bad_json,
            ),
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is False
        assert data["error_code"] == "vertex_credentials_invalid"

    async def test_unparsable_private_key_reports_credentials_invalid(
        self, client: AsyncClient
    ) -> None:
        """结构完整但私钥无法解析：credentials_invalid（脱敏消息）。"""
        bad_key_json = _service_account_json(private_key="not-a-real-key")
        response = await client.post(
            "/api/v1/model-providers/validate",
            json=_validate_payload(
                provider_config=_vertex_config("service_account"),
                credentials_action="replace",
                service_account_json=bad_key_json,
            ),
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is False
        assert data["error_code"] == "vertex_credentials_invalid"
        # 不回传原始 JSON / 私钥内容。
        assert "not-a-real-key" not in response.text


class TestVertexValidateRequestErrors:
    """请求层错误分类（第 6.2 节）：错误凭据、限流、超时、拦截。"""

    @staticmethod
    def _make_api_error(code: int):
        from google.genai.errors import ClientError, ServerError

        if 500 <= code < 600:
            error = ServerError(code, {"error": {"message": "boom"}}, None)
        else:
            error = ClientError(code, {"error": {"message": "boom"}}, None)
        return error

    @pytest.mark.parametrize(
        ("status_code", "expected_error_code"),
        [
            (401, "vertex_credentials_invalid"),
            (403, "vertex_permission_denied"),
            (404, "vertex_model_unavailable"),
            (429, "vertex_rate_limited"),
            (500, "vertex_request_unknown"),
        ],
    )
    async def test_http_error_classification(
        self,
        client: AsyncClient,
        monkeypatch: pytest.MonkeyPatch,
        status_code: int,
        expected_error_code: str,
    ) -> None:
        monkeypatch.setattr(
            google.auth, "default", lambda scopes=None: (AnonymousCredentials(), None)
        )
        api_error = self._make_api_error(status_code)
        # 模拟 langchain 的包装链：ChatGoogleGenerativeAIError <- ClientError。
        try:
            raise RuntimeError("wrapped") from api_error
        except RuntimeError as wrapped_exc:
            wrapped = wrapped_exc
        _patch_llm_client_import(monkeypatch, wrapped)

        response = await client.post(
            "/api/v1/model-providers/validate", json=_validate_payload()
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is False
        assert data["error_code"] == expected_error_code
        # 原始异常字符串不回传。
        assert "boom" not in response.text

    async def test_timeout_classification(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import asyncio as _asyncio

        monkeypatch.setattr(
            google.auth, "default", lambda scopes=None: (AnonymousCredentials(), None)
        )
        _patch_llm_client_import(monkeypatch, _asyncio.TimeoutError())

        response = await client.post(
            "/api/v1/model-providers/validate", json=_validate_payload()
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is False
        assert data["error_code"] == "vertex_timeout"

    async def test_refresh_error_classified_as_credentials_invalid(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """请求期 token 刷新失败（凭据无效），不误判为请求层未知错误。"""
        from google.auth.exceptions import RefreshError

        monkeypatch.setattr(
            google.auth, "default", lambda scopes=None: (AnonymousCredentials(), None)
        )
        try:
            raise RuntimeError("wrapped") from RefreshError("token refresh failed")
        except RuntimeError as wrapped_exc:
            wrapped = wrapped_exc
        _patch_llm_client_import(monkeypatch, wrapped)

        response = await client.post(
            "/api/v1/model-providers/validate", json=_validate_payload()
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is False
        assert data["error_code"] == "vertex_credentials_invalid"
        # 刷新失败的原始错误不回传。
        assert "refresh failed" not in response.text

    async def test_empty_response_reports_blocked(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """空响应（安全拦截）：vertex_response_blocked，不 fallback 成功。"""
        monkeypatch.setattr(
            google.auth, "default", lambda scopes=None: (AnonymousCredentials(), None)
        )
        _patch_llm_client_import(monkeypatch, "")

        response = await client.post(
            "/api/v1/model-providers/validate", json=_validate_payload()
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is False
        assert data["error_code"] == "vertex_response_blocked"
        assert data["validation_scope"] is None

    async def test_response_contains_no_credentials(
        self,
        client: AsyncClient,
        test_private_key_pem: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """验证响应不回显 Service Account 内容。"""
        provider_id = await _create_saved_vertex(
            client, service_account_json=_service_account_json(test_private_key_pem)
        )
        _patch_llm_client_import(monkeypatch, "OK")

        response = await client.post(
            "/api/v1/model-providers/validate",
            json=_validate_payload(provider_config="", provider_id=provider_id),
        )

        assert response.status_code == 200
        assert response.json()["success"] is True
        assert _SA_PRIVATE_KEY_SENTINEL not in response.text
        assert test_private_key_pem not in response.text


class TestNonVertexValidateRegression:
    async def test_openai_validate_unchanged(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """非 Vertex 提供商的验证路径保持原有行为（无 error_code/scope）。"""
        import respx
        import httpx

        with respx.mock:
            respx.get("https://api.openai.com/v1/models").mock(
                return_value=httpx.Response(401, json={"error": {"message": "bad key"}})
            )
            response = await client.post(
                "/api/v1/model-providers/validate",
                json={
                    "provider_type": "openai",
                    "url": "https://api.openai.com",
                    "api_key": "invalid",
                },
            )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is False
        assert data["error_code"] is None
        assert data["validation_scope"] is None
