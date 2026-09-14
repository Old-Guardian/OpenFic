# -*- coding: utf-8 -*-
"""
Vertex 认证解析模块测试。

使用测试专用 RSA 私钥与伪凭据对象，不使用真实 Google 凭据、
不发起网络请求（对应实施计划第 8.1 节凭据与并发隔离场景）。
"""

import asyncio
import json
import os
import tempfile
import threading
import time
from typing import Literal

import google.auth
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from google.auth.credentials import AnonymousCredentials
from google.auth.exceptions import (
    DefaultCredentialsError,
    MalformedError,
    RefreshError,
)
from google.oauth2 import service_account

from app.models.clients.google_vertex_auth import (
    VERTEX_CLOUD_PLATFORM_SCOPE,
    VERTEX_ERROR_AUTH_UNKNOWN,
    VERTEX_ERROR_CONFIG_INVALID,
    VERTEX_ERROR_CREDENTIALS_INVALID,
    VERTEX_ERROR_CREDENTIALS_MISSING,
    VertexAuthError,
    VertexConnectionContext,
    build_vertex_connection_context,
    build_vertex_connection_context_async,
    classify_vertex_auth_error,
)
from app.models.vertex_config import VertexProviderConfig


def _config(
    auth_mode: Literal["adc", "service_account"] = "service_account",
    project_id: str = "my-gcp-project",
    location: str = "us-central1",
) -> VertexProviderConfig:
    return VertexProviderConfig(project_id=project_id, location=location, auth_mode=auth_mode)


@pytest.fixture(scope="module")
def test_private_key_pem() -> str:
    """测试专用 RSA 私钥（仅存在于测试进程中，非生产凭据）。"""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()


def _service_account_json(
    private_key: str,
    client_email: str = "test@sa-project.iam.gserviceaccount.com",
    project_id: str = "sa-project",
    **overrides: object,
) -> str:
    payload = {
        "type": "service_account",
        "project_id": project_id,
        "private_key_id": "test-key-id",
        "private_key": private_key,
        "client_email": client_email,
        "client_id": "123456789",
        "token_uri": "https://oauth2.googleapis.com/token",
    }
    payload.update(overrides)
    return json.dumps(payload)


class TestServiceAccountContext:
    def test_builds_context_from_service_account(self, test_private_key_pem: str) -> None:
        raw = _service_account_json(test_private_key_pem)
        context = build_vertex_connection_context(_config(), raw)

        assert isinstance(context, VertexConnectionContext)
        assert context.project_id == "my-gcp-project"
        assert context.location == "us-central1"
        assert context.auth_mode == "service_account"
        assert isinstance(context.credentials, service_account.Credentials)
        assert context.credentials.service_account_email == (
            "test@sa-project.iam.gserviceaccount.com"
        )

    def test_missing_credentials_raises_missing(self) -> None:
        with pytest.raises(VertexAuthError) as exc_info:
            build_vertex_connection_context(_config(), None)
        assert exc_info.value.error_code == VERTEX_ERROR_CREDENTIALS_MISSING

    def test_blank_credentials_raises_missing(self) -> None:
        with pytest.raises(VertexAuthError) as exc_info:
            build_vertex_connection_context(_config(), "   ")
        assert exc_info.value.error_code == VERTEX_ERROR_CREDENTIALS_MISSING

    def test_malformed_json_raises_invalid(self) -> None:
        with pytest.raises(VertexAuthError) as exc_info:
            build_vertex_connection_context(_config(), "{not json")
        assert exc_info.value.error_code == VERTEX_ERROR_CREDENTIALS_INVALID

    def test_wrong_type_raises_invalid(self, test_private_key_pem: str) -> None:
        raw = _service_account_json(
            test_private_key_pem, type="authorized_user"
        )
        with pytest.raises(VertexAuthError) as exc_info:
            build_vertex_connection_context(_config(), raw)
        assert exc_info.value.error_code == VERTEX_ERROR_CREDENTIALS_INVALID

    def test_invalid_private_key_raises_invalid_sanitized(
        self,
    ) -> None:
        raw = _service_account_json(
            "-----BEGIN PRIVATE KEY-----\nSENTINEL_PRIVATE_KEY_MARKER\n"
            "-----END PRIVATE KEY-----\n"
        )
        with pytest.raises(VertexAuthError) as exc_info:
            build_vertex_connection_context(_config(), raw)

        assert exc_info.value.error_code == VERTEX_ERROR_CREDENTIALS_INVALID
        # 错误消息脱敏：不包含私钥哨兵内容；原始异常保留在 __cause__ 供日志。
        assert "SENTINEL_PRIVATE_KEY_MARKER" not in str(exc_info.value)
        assert "BEGIN PRIVATE KEY" not in str(exc_info.value)
        assert exc_info.value.__cause__ is not None

    def test_each_call_builds_fresh_credentials(self, test_private_key_pem: str) -> None:
        raw = _service_account_json(test_private_key_pem)
        first = build_vertex_connection_context(_config(), raw)
        second = build_vertex_connection_context(_config(), raw)
        # 不做全局凭据缓存：两次解析产生独立凭据对象。
        assert first.credentials is not second.credentials

    def test_scope_is_cloud_platform(self, test_private_key_pem: str, monkeypatch: pytest.MonkeyPatch) -> None:
        captured: dict = {}

        def fake_from_info(payload: dict, **kwargs: object):
            captured["payload"] = payload
            captured["kwargs"] = kwargs
            return object()

        monkeypatch.setattr(
            service_account.Credentials, "from_service_account_info", fake_from_info
        )
        build_vertex_connection_context(_config(), _service_account_json(test_private_key_pem))

        assert captured["kwargs"] == {"scopes": [VERTEX_CLOUD_PLATFORM_SCOPE]}


class TestAdcContext:
    def test_builds_context_from_adc(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake_credentials = AnonymousCredentials()

        def fake_default(scopes=None):
            assert scopes == [VERTEX_CLOUD_PLATFORM_SCOPE]
            return fake_credentials, "adc-project"

        monkeypatch.setattr(google.auth, "default", fake_default)

        context = build_vertex_connection_context(_config(auth_mode="adc"))

        assert context.auth_mode == "adc"
        assert context.project_id == "my-gcp-project"
        assert context.location == "us-central1"
        assert context.credentials is fake_credentials

    def test_adc_missing_raises_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def fake_default(scopes=None):
            raise DefaultCredentialsError("no ADC found")

        monkeypatch.setattr(google.auth, "default", fake_default)

        with pytest.raises(VertexAuthError) as exc_info:
            build_vertex_connection_context(_config(auth_mode="adc"))
        assert exc_info.value.error_code == VERTEX_ERROR_CREDENTIALS_MISSING
        assert "后端" in str(exc_info.value)

    def test_adc_failure_raises_invalid(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def fake_default(scopes=None):
            raise OSError("credentials file unreadable")

        monkeypatch.setattr(google.auth, "default", fake_default)

        with pytest.raises(VertexAuthError) as exc_info:
            build_vertex_connection_context(_config(auth_mode="adc"))
        assert exc_info.value.error_code == VERTEX_ERROR_CREDENTIALS_INVALID

    def test_adc_with_credentials_json_rejected_without_fallback(
        self, monkeypatch: pytest.MonkeyPatch, test_private_key_pem: str
    ) -> None:
        def fake_default(scopes=None):
            raise AssertionError("ADC 模式不应触发凭据解析回退")

        monkeypatch.setattr(google.auth, "default", fake_default)

        with pytest.raises(VertexAuthError) as exc_info:
            build_vertex_connection_context(
                _config(auth_mode="adc"),
                _service_account_json(test_private_key_pem),
            )
        assert exc_info.value.error_code == VERTEX_ERROR_CONFIG_INVALID


class TestAsyncEntry:
    async def test_resolves_blocking_parts_in_worker_thread(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        loop_thread = threading.get_ident()

        def fake_default(scopes=None):
            assert threading.get_ident() != loop_thread
            return AnonymousCredentials(), "adc-project"

        monkeypatch.setattr(google.auth, "default", fake_default)

        context = await build_vertex_connection_context_async(_config(auth_mode="adc"))

        assert isinstance(context.credentials, AnonymousCredentials)

    async def test_does_not_block_event_loop(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        ticks = 0

        async def heartbeat() -> None:
            nonlocal ticks
            while True:
                ticks += 1
                await asyncio.sleep(0.01)

        def slow_default(scopes=None):
            time.sleep(0.2)
            return AnonymousCredentials(), "adc-project"

        monkeypatch.setattr(google.auth, "default", slow_default)

        task = asyncio.create_task(heartbeat())
        try:
            context = await build_vertex_connection_context_async(
                _config(auth_mode="adc")
            )
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

        assert context.auth_mode == "adc"
        # 阻塞解析期间事件循环仍在调度心跳任务。
        assert ticks >= 2

    async def test_service_account_path(self, test_private_key_pem: str) -> None:
        context = await build_vertex_connection_context_async(
            _config(), _service_account_json(test_private_key_pem)
        )
        assert isinstance(context.credentials, service_account.Credentials)


class TestEnvironmentAndFileSafety:
    def test_does_not_mutate_environment(
        self, monkeypatch: pytest.MonkeyPatch, test_private_key_pem: str
    ) -> None:
        monkeypatch.setenv("GOOGLE_API_KEY", "env-key-sentinel")
        monkeypatch.setenv("GOOGLE_GENAI_USE_VERTEXAI", "false")
        snapshot = dict(os.environ)

        monkeypatch.setattr(google.auth, "default", lambda scopes=None: (AnonymousCredentials(), None))
        build_vertex_connection_context(_config(auth_mode="adc"))
        build_vertex_connection_context(
            _config(), _service_account_json(test_private_key_pem)
        )

        assert os.environ == snapshot

    def test_does_not_create_private_key_files(
        self, monkeypatch: pytest.MonkeyPatch, test_private_key_pem: str
    ) -> None:
        def _fail(*args: object, **kwargs: object):
            raise AssertionError("不应生成临时私钥文件")

        monkeypatch.setattr(tempfile, "NamedTemporaryFile", _fail)
        monkeypatch.setattr(tempfile, "mkstemp", _fail)

        context = build_vertex_connection_context(
            _config(), _service_account_json(test_private_key_pem)
        )
        assert isinstance(context.credentials, service_account.Credentials)

    def test_context_repr_excludes_credentials(self, test_private_key_pem: str) -> None:
        context = build_vertex_connection_context(
            _config(), _service_account_json(test_private_key_pem)
        )
        rendered = repr(context)
        assert "credentials" not in rendered
        assert "private" not in rendered.lower()
        assert rendered == (
            "VertexConnectionContext(project_id='my-gcp-project', "
            "location='us-central1', auth_mode='service_account')"
        )


class TestConcurrencyIsolation:
    async def test_two_service_accounts_do_not_mix(
        self, test_private_key_pem: str
    ) -> None:
        raw_a = _service_account_json(
            test_private_key_pem,
            client_email="alpha@project-a.iam.gserviceaccount.com",
            project_id="project-a",
        )
        raw_b = _service_account_json(
            test_private_key_pem,
            client_email="beta@project-b.iam.gserviceaccount.com",
            project_id="project-b",
        )
        config_a = _config(project_id="target-project-a", location="us-central1")
        config_b = _config(project_id="target-project-b", location="europe-west1")

        context_a, context_b = await asyncio.gather(
            build_vertex_connection_context_async(config_a, raw_a),
            build_vertex_connection_context_async(config_b, raw_b),
        )

        assert context_a.project_id == "target-project-a"
        assert context_a.location == "us-central1"
        assert isinstance(context_a.credentials, service_account.Credentials)
        assert context_a.credentials.service_account_email == (
            "alpha@project-a.iam.gserviceaccount.com"
        )
        assert context_b.project_id == "target-project-b"
        assert context_b.location == "europe-west1"
        assert isinstance(context_b.credentials, service_account.Credentials)
        assert context_b.credentials.service_account_email == (
            "beta@project-b.iam.gserviceaccount.com"
        )

    async def test_adc_and_service_account_do_not_mix(
        self, monkeypatch: pytest.MonkeyPatch, test_private_key_pem: str
    ) -> None:
        adc_credentials = AnonymousCredentials()
        monkeypatch.setattr(
            google.auth, "default", lambda scopes=None: (adc_credentials, None)
        )

        adc_context, sa_context = await asyncio.gather(
            build_vertex_connection_context_async(_config(auth_mode="adc")),
            build_vertex_connection_context_async(
                _config(), _service_account_json(test_private_key_pem)
            ),
        )

        assert adc_context.credentials is adc_credentials
        assert isinstance(sa_context.credentials, service_account.Credentials)
        assert adc_context.credentials is not sa_context.credentials


class TestClassifyVertexAuthError:
    def test_vertex_auth_error_passthrough(self) -> None:
        error = VertexAuthError(
            "message", error_code=VERTEX_ERROR_CREDENTIALS_MISSING
        )
        assert classify_vertex_auth_error(error) == VERTEX_ERROR_CREDENTIALS_MISSING

    def test_default_credentials_error_is_missing(self) -> None:
        assert (
            classify_vertex_auth_error(DefaultCredentialsError("no adc"))
            == VERTEX_ERROR_CREDENTIALS_MISSING
        )

    def test_refresh_error_is_invalid(self) -> None:
        assert (
            classify_vertex_auth_error(RefreshError("token refresh failed"))
            == VERTEX_ERROR_CREDENTIALS_INVALID
        )

    def test_malformed_error_is_invalid(self) -> None:
        assert (
            classify_vertex_auth_error(MalformedError("malformed credentials"))
            == VERTEX_ERROR_CREDENTIALS_INVALID
        )

    def test_unknown_error_returns_unknown_code(self) -> None:
        assert (
            classify_vertex_auth_error(ValueError("unexpected"))
            == VERTEX_ERROR_AUTH_UNKNOWN
        )
