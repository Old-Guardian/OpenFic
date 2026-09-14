# -*- coding: utf-8 -*-
"""
google-vertex Provider CRUD API 测试。

覆盖：创建（ADC / Service Account）、凭据操作语义（keep/replace/clear）、
认证模式切换、类型切换清理、响应脱敏、设置锁。全程离线。
"""

import json

import pytest
from httpx import AsyncClient, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.repos import model_provider_repo

_SA_PRIVATE_KEY_SENTINEL = "SENTINEL-vertex-private-key-material"


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


async def _create_vertex(
    client: AsyncClient,
    *,
    auth_mode: str = "adc",
    credentials_action: str = "keep",
    service_account_json: str | None = None,
    provider_config: str | None = None,
    name: str = "Vertex Test",
) -> Response:
    data = {
        "name": name,
        "url": "",
        "provider_type": "google-vertex",
        "provider_config": (
            provider_config if provider_config is not None else _vertex_config(auth_mode)
        ),
        "credentials_action": credentials_action,
    }
    if service_account_json is not None:
        data["service_account_json"] = service_account_json
    return await client.post("/api/v1/model-providers", data=data)


# ======================== 创建 ========================


@pytest.mark.asyncio
async def test_create_vertex_adc_provider(client: AsyncClient, session: AsyncSession):
    response = await _create_vertex(client, auth_mode="adc")

    assert response.status_code == 201
    data = response.json()
    assert data["provider_type"] == "google-vertex"
    assert data["url"] == ""
    assert data["provider_config"] == {
        "project_id": "my-gcp-project",
        "location": "us-central1",
        "auth_mode": "adc",
    }
    assert data["has_credentials"] is False

    provider = await model_provider_repo.get_by_id(session, data["id"])
    assert provider is not None
    assert provider.url == ""
    assert provider.api_key_encrypted == ""
    assert provider.credentials_encrypted == ""
    assert provider.provider_config == {
        "project_id": "my-gcp-project",
        "location": "us-central1",
        "auth_mode": "adc",
    }


@pytest.mark.asyncio
async def test_create_vertex_service_account_encrypts_credentials(
    client: AsyncClient, session: AsyncSession
):
    from app.core.encryption import EncryptionService
    from app.settings import settings

    response = await _create_vertex(
        client,
        auth_mode="service_account",
        credentials_action="replace",
        service_account_json=_service_account_json(),
    )

    assert response.status_code == 201
    data = response.json()
    assert data["has_credentials"] is True
    # 响应不回显凭据内容。
    assert _SA_PRIVATE_KEY_SENTINEL not in response.text

    provider = await model_provider_repo.get_by_id(session, data["id"])
    assert provider is not None
    assert provider.credentials_encrypted
    # 明文哨兵不落库（所有存储列均检查）。
    assert _SA_PRIVATE_KEY_SENTINEL not in provider.credentials_encrypted
    assert _SA_PRIVATE_KEY_SENTINEL not in provider.api_key_encrypted
    assert _SA_PRIVATE_KEY_SENTINEL not in provider.url
    assert _SA_PRIVATE_KEY_SENTINEL not in provider.name
    assert _SA_PRIVATE_KEY_SENTINEL not in json.dumps(provider.provider_config)
    # 密文可由应用加密服务正确解密。
    decrypted = EncryptionService(settings.encryption_key).decrypt(
        provider.credentials_encrypted
    )
    assert json.loads(decrypted)["client_email"] == "test@sa-project.iam.gserviceaccount.com"


@pytest.mark.asyncio
async def test_create_vertex_service_account_without_credentials_rejected(
    client: AsyncClient,
):
    response = await _create_vertex(client, auth_mode="service_account")

    assert response.status_code == 400
    assert "Service Account" in response.json()["detail"]


@pytest.mark.asyncio
async def test_create_vertex_requires_provider_config(client: AsyncClient):
    response = await _create_vertex(client, provider_config="")

    assert response.status_code == 400
    assert "provider_config" in response.json()["detail"]


@pytest.mark.asyncio
async def test_create_vertex_rejects_unknown_config_fields(client: AsyncClient):
    response = await _create_vertex(
        client, provider_config=_vertex_config(credentials="nope")
    )

    assert response.status_code == 400


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("extra_field", "extra_value"),
    [("url", "https://vertex.example"), ("api_key", "some-key")],
)
async def test_create_vertex_rejects_url_and_api_key(
    client: AsyncClient, extra_field: str, extra_value: str
):
    data = {
        "name": "Vertex Test",
        "url": "",
        "provider_type": "google-vertex",
        "provider_config": _vertex_config("adc"),
        extra_field: extra_value,
    }
    response = await client.post("/api/v1/model-providers", data=data)

    assert response.status_code == 400


@pytest.mark.asyncio
async def test_create_vertex_rejects_custom_headers(client: AsyncClient):
    response = await client.post(
        "/api/v1/model-providers",
        data={
            "name": "Vertex Test",
            "url": "",
            "provider_type": "google-vertex",
            "provider_config": _vertex_config("adc"),
            "custom_headers": json.dumps([{"key": "X-Header", "value": "v"}]),
        },
    )

    assert response.status_code == 400


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("extra_field", "extra_value"),
    [
        ("provider_config", _vertex_config("adc")),
        ("service_account_json", _service_account_json()),
    ],
)
async def test_create_non_vertex_rejects_vertex_inputs(
    client: AsyncClient, extra_field: str, extra_value: str
):
    response = await client.post(
        "/api/v1/model-providers",
        data={
            "name": "OpenAI",
            "url": "https://api.openai.com",
            "api_key": "sk-test",
            "provider_type": "openai",
            extra_field: extra_value,
        },
    )

    assert response.status_code == 400


@pytest.mark.asyncio
async def test_create_vertex_rejects_invalid_credentials_action(client: AsyncClient):
    response = await _create_vertex(
        client, auth_mode="service_account", credentials_action="rotate"
    )

    assert response.status_code == 400


@pytest.mark.asyncio
async def test_create_vertex_rejects_oversized_service_account(client: AsyncClient):
    response = await _create_vertex(
        client,
        auth_mode="service_account",
        credentials_action="replace",
        service_account_json=_service_account_json(
            private_key="x" * (64 * 1024)
        ),
    )

    assert response.status_code == 400
    assert "64 KiB" in response.json()["detail"]


# ======================== 更新 ========================


@pytest.mark.asyncio
async def test_update_vertex_omitted_fields_keep_config_and_credentials(
    client: AsyncClient, session: AsyncSession
):
    created = (
        await _create_vertex(
            client,
            auth_mode="service_account",
            credentials_action="replace",
            service_account_json=_service_account_json(),
        )
    ).json()

    response = await client.put(
        f"/api/v1/model-providers/{created['id']}",
        data={"name": "Renamed Vertex"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "Renamed Vertex"
    assert data["provider_config"] == created["provider_config"]
    assert data["has_credentials"] is True

    provider = await model_provider_repo.get_by_id(session, created["id"])
    assert provider is not None
    assert provider.credentials_encrypted


@pytest.mark.asyncio
async def test_update_vertex_keep_without_existing_credentials_rejected(
    client: AsyncClient,
):
    created = (await _create_vertex(client, auth_mode="adc")).json()

    response = await client.put(
        f"/api/v1/model-providers/{created['id']}",
        data={"provider_config": _vertex_config("service_account")},
    )

    assert response.status_code == 400
    assert "没有已保存的凭据" in response.json()["detail"]


@pytest.mark.asyncio
async def test_update_vertex_switch_to_adc_requires_clear(client: AsyncClient):
    created = (
        await _create_vertex(
            client,
            auth_mode="service_account",
            credentials_action="replace",
            service_account_json=_service_account_json(),
        )
    ).json()

    # keep 旧凭据 + adc → 最终状态矛盾，拒绝。
    rejected = await client.put(
        f"/api/v1/model-providers/{created['id']}",
        data={"provider_config": _vertex_config("adc")},
    )
    assert rejected.status_code == 400
    assert "清除" in rejected.json()["detail"]

    cleared = await client.put(
        f"/api/v1/model-providers/{created['id']}",
        data={
            "provider_config": _vertex_config("adc"),
            "credentials_action": "clear",
        },
    )
    assert cleared.status_code == 200
    assert cleared.json()["has_credentials"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("action", "sa_json"),
    [
        ("replace", ""),
        ("replace", "   "),
        ("keep", _service_account_json()),
        ("clear", _service_account_json()),
    ],
)
async def test_update_vertex_contradictory_credentials_inputs_rejected(
    client: AsyncClient, action: str, sa_json: str
):
    created = (await _create_vertex(client, auth_mode="adc")).json()

    response = await client.put(
        f"/api/v1/model-providers/{created['id']}",
        data={
            "provider_config": _vertex_config("adc"),
            "credentials_action": action,
            "service_account_json": sa_json,
        },
    )

    assert response.status_code == 400


@pytest.mark.asyncio
async def test_update_vertex_replaces_config_and_credentials(
    client: AsyncClient, session: AsyncSession
):
    from app.core.encryption import EncryptionService
    from app.settings import settings

    created = (
        await _create_vertex(
            client,
            auth_mode="service_account",
            credentials_action="replace",
            service_account_json=_service_account_json(),
        )
    ).json()

    new_config = _vertex_config(
        "service_account", project_id="other-project", location="europe-west1"
    )
    response = await client.put(
        f"/api/v1/model-providers/{created['id']}",
        data={
            "provider_config": new_config,
            "credentials_action": "replace",
            "service_account_json": _service_account_json(
                private_key="-----BEGIN PRIVATE KEY-----\nreplacement\n-----END PRIVATE KEY-----\n"
            ),
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["provider_config"] == {
        "project_id": "other-project",
        "location": "europe-west1",
        "auth_mode": "service_account",
    }
    assert data["has_credentials"] is True

    provider = await model_provider_repo.get_by_id(session, created["id"])
    assert provider is not None
    decrypted = EncryptionService(settings.encryption_key).decrypt(
        provider.credentials_encrypted
    )
    assert json.loads(decrypted)["private_key"].startswith(
        "-----BEGIN PRIVATE KEY-----\nreplacement"
    )


@pytest.mark.asyncio
async def test_update_vertex_rejects_url_and_api_key(client: AsyncClient):
    created = (await _create_vertex(client, auth_mode="adc")).json()

    response = await client.put(
        f"/api/v1/model-providers/{created['id']}",
        data={"url": "https://vertex.example"},
    )
    assert response.status_code == 400

    response = await client.put(
        f"/api/v1/model-providers/{created['id']}",
        data={"api_key": "some-key"},
    )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_update_legacy_vertex_without_config_rejected(
    client: AsyncClient, session: AsyncSession
):
    """旧目录遗留的 google-vertex 连接（无配置）在补充配置前拒绝更新。"""
    provider = await model_provider_repo.create(
        session=session,
        name="Legacy Vertex",
        url="https://legacy-vertex.example",
        api_key_encrypted="legacy-key",
        provider_type="google-vertex",
    )
    await session.commit()

    response = await client.put(
        f"/api/v1/model-providers/{provider.id}",
        data={"name": "Still Legacy"},
    )

    assert response.status_code == 400
    assert "缺少 Vertex 配置" in response.json()["detail"]


@pytest.mark.asyncio
async def test_update_vertex_normalizes_legacy_url_and_api_key(
    client: AsyncClient, session: AsyncSession
):
    """补充配置后，遗留的 URL 与 API Key 规范为空。"""
    provider = await model_provider_repo.create(
        session=session,
        name="Legacy Vertex",
        url="https://legacy-vertex.example",
        api_key_encrypted="legacy-key",
        provider_type="google-vertex",
    )
    await session.commit()

    response = await client.put(
        f"/api/v1/model-providers/{provider.id}",
        data={"provider_config": _vertex_config("adc")},
    )

    assert response.status_code == 200
    updated = await model_provider_repo.get_by_id(session, provider.id)
    assert updated is not None
    assert updated.url == ""
    assert updated.api_key_encrypted == ""


@pytest.mark.asyncio
async def test_update_switch_vertex_to_openai_clears_vertex_fields(
    client: AsyncClient, session: AsyncSession
):
    created = (
        await _create_vertex(
            client,
            auth_mode="service_account",
            credentials_action="replace",
            service_account_json=_service_account_json(),
        )
    ).json()

    response = await client.put(
        f"/api/v1/model-providers/{created['id']}",
        data={
            "provider_type": "openai",
            "url": "https://api.openai.com",
            "api_key": "sk-test",
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["provider_type"] == "openai"
    assert data["provider_config"] == {}
    assert data["has_credentials"] is False

    provider = await model_provider_repo.get_by_id(session, created["id"])
    assert provider is not None
    assert provider.credentials_encrypted == ""
    assert provider.provider_config == {}
    assert provider.api_key_encrypted  # 目标提供商的 API Key 正常保存


@pytest.mark.asyncio
async def test_update_non_vertex_rejects_vertex_inputs(client: AsyncClient):
    created = (
        await client.post(
            "/api/v1/model-providers",
            data={
                "name": "OpenAI",
                "url": "https://api.openai.com",
                "api_key": "sk-test",
                "provider_type": "openai",
            },
        )
    ).json()

    response = await client.put(
        f"/api/v1/model-providers/{created['id']}",
        data={
            "name": "OpenAI",
            "provider_config": _vertex_config("adc"),
        },
    )
    assert response.status_code == 400

    response = await client.put(
        f"/api/v1/model-providers/{created['id']}",
        data={
            "name": "OpenAI",
            "credentials_action": "replace",
            "service_account_json": _service_account_json(),
        },
    )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_update_invalid_request_not_partially_saved(
    client: AsyncClient, session: AsyncSession
):
    """无效更新不部分保存：名称与配置都不应改变。"""
    created = (
        await _create_vertex(
            client,
            auth_mode="service_account",
            credentials_action="replace",
            service_account_json=_service_account_json(),
        )
    ).json()

    response = await client.put(
        f"/api/v1/model-providers/{created['id']}",
        data={
            "name": "Should Not Save",
            "provider_config": _vertex_config("adc"),  # keep 旧凭据 + adc → 矛盾
        },
    )

    assert response.status_code == 400
    provider = await model_provider_repo.get_by_id(session, created["id"])
    assert provider is not None
    assert provider.name == "Vertex Test"
    assert provider.provider_config["auth_mode"] == "service_account"
    assert provider.credentials_encrypted


# ======================== 响应与设置锁 ========================


@pytest.mark.asyncio
async def test_provider_list_includes_vertex_fields(client: AsyncClient):
    created = (await _create_vertex(client, auth_mode="adc")).json()

    list_response = await client.get("/api/v1/model-providers")
    assert list_response.status_code == 200
    matched = next(
        item for item in list_response.json() if item["id"] == created["id"]
    )
    assert matched["provider_config"] == created["provider_config"]
    assert matched["has_credentials"] is False

    detail = await client.get(f"/api/v1/model-providers/{created['id']}")
    assert detail.status_code == 200
    assert detail.json()["provider_config"] == created["provider_config"]


@pytest.mark.asyncio
async def test_non_vertex_provider_response_has_empty_vertex_fields(
    client: AsyncClient,
):
    created = (
        await client.post(
            "/api/v1/model-providers",
            data={
                "name": "OpenAI",
                "url": "https://api.openai.com",
                "api_key": "sk-test",
                "provider_type": "openai",
            },
        )
    ).json()

    assert created["provider_config"] == {}
    assert created["has_credentials"] is False


@pytest.mark.asyncio
async def test_vertex_write_blocked_by_agent_settings_lock(
    client: AsyncClient, session: AsyncSession
):
    from tests.api.test_agent_settings_lock import _create_agent_task

    await _create_agent_task(client, session, is_running=True)

    response = await _create_vertex(client, auth_mode="adc")

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "agent_settings_locked"


# ======================== 模型列表（T3） ========================


@pytest.mark.asyncio
async def test_get_vertex_models_without_credentials(client: AsyncClient):
    """ADC 模式、无任何已保存凭据时即可读取目录列表（离线，无网络请求）。"""
    created = await _create_vertex(client, auth_mode="adc")
    assert created.status_code == 201

    response = await client.get(f"/api/v1/model-providers/{created.json()['id']}/models")

    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["models"]
    assert all(model["id"].startswith("gemini") for model in data["models"])
    assert not any(model["id"].startswith("claude") for model in data["models"])


@pytest.mark.asyncio
async def test_get_vertex_models_with_service_account_does_not_leak_credentials(
    client: AsyncClient,
):
    """已保存 Service Account 的连接读取列表时不回显凭据。"""
    created = await _create_vertex(
        client,
        auth_mode="service_account",
        credentials_action="replace",
        service_account_json=_service_account_json(),
    )
    assert created.status_code == 201

    response = await client.get(f"/api/v1/model-providers/{created.json()['id']}/models")

    assert response.status_code == 200
    assert response.json()["success"] is True
    assert _SA_PRIVATE_KEY_SENTINEL not in response.text


@pytest.mark.asyncio
async def test_get_vertex_models_rejects_embedding_task(client: AsyncClient):
    """第一阶段仅支持 LLM 任务类型。"""
    created = await _create_vertex(client, auth_mode="adc")
    assert created.status_code == 201

    response = await client.get(
        f"/api/v1/model-providers/{created.json()['id']}/models",
        params={"task_type": "embedding"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["success"] is False
    assert data["models"] == []
    assert "does not support task_type" in data["message"]
