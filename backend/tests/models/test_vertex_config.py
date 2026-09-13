# -*- coding: utf-8 -*-
"""
VertexProviderConfig 配置与凭据结构校验测试。
"""

import json

import pytest

from app.models.vertex_config import (
    VertexConfigError,
    load_vertex_provider_config,
    parse_vertex_provider_config,
    validate_vertex_service_account_json,
)


def _config_payload(**overrides: str) -> str:
    payload = {
        "project_id": "my-gcp-project",
        "location": "us-central1",
        "auth_mode": "service_account",
    }
    payload.update(overrides)
    return json.dumps(payload)


def _service_account_payload(**overrides: object) -> dict:
    payload = {
        "type": "service_account",
        "project_id": "sa-project",
        "private_key_id": "key-id",
        "private_key": "-----BEGIN PRIVATE KEY-----\nfake\n-----END PRIVATE KEY-----\n",
        "client_email": "test@sa-project.iam.gserviceaccount.com",
        "client_id": "123456789",
        "token_uri": "https://oauth2.googleapis.com/token",
    }
    payload.update(overrides)
    return payload


class TestParseVertexProviderConfig:
    def test_parses_and_strips_values(self) -> None:
        config = parse_vertex_provider_config(
            _config_payload(project_id="  my-gcp-project  ", location=" global ")
        )
        assert config.project_id == "my-gcp-project"
        assert config.location == "global"
        assert config.auth_mode == "service_account"

    @pytest.mark.parametrize("raw", [None, "", "   "])
    def test_missing_config_rejected(self, raw: str | None) -> None:
        with pytest.raises(VertexConfigError, match="必须提供 provider_config"):
            parse_vertex_provider_config(raw)

    def test_invalid_json_rejected(self) -> None:
        with pytest.raises(VertexConfigError, match="合法的 JSON"):
            parse_vertex_provider_config("{not json")

    def test_non_object_rejected(self) -> None:
        with pytest.raises(VertexConfigError, match="JSON 对象"):
            parse_vertex_provider_config('["project_id"]')

    def test_unknown_field_rejected(self) -> None:
        raw = _config_payload(api_key="should-not-be-here")
        with pytest.raises(VertexConfigError, match="校验失败"):
            parse_vertex_provider_config(raw)

    def test_missing_required_fields_rejected(self) -> None:
        with pytest.raises(VertexConfigError, match="project_id"):
            parse_vertex_provider_config('{"location": "global", "auth_mode": "adc"}')

    def test_invalid_auth_mode_rejected(self) -> None:
        with pytest.raises(VertexConfigError, match="auth_mode"):
            parse_vertex_provider_config(_config_payload(auth_mode="api_key"))


class TestLoadVertexProviderConfig:
    def test_loads_stored_dict(self) -> None:
        config = load_vertex_provider_config(
            {"project_id": "p", "location": "us-central1", "auth_mode": "adc"}
        )
        assert config.auth_mode == "adc"

    def test_loads_stored_json_string(self) -> None:
        config = load_vertex_provider_config(
            '{"project_id": "p", "location": "global", "auth_mode": "adc"}'
        )
        assert config.location == "global"

    @pytest.mark.parametrize("stored", [None, {}, "", "{}"])
    def test_missing_stored_config_rejected(self, stored) -> None:
        with pytest.raises(VertexConfigError, match="缺少 Vertex 配置"):
            load_vertex_provider_config(stored)

    def test_invalid_stored_json_rejected(self) -> None:
        with pytest.raises(VertexConfigError, match="格式无效"):
            load_vertex_provider_config("invalid-json")


class TestValidateVertexServiceAccountJson:
    def test_accepts_expected_structure(self) -> None:
        payload = _service_account_payload()
        assert validate_vertex_service_account_json(json.dumps(payload)) == payload

    @pytest.mark.parametrize("raw", [None, "", "   "])
    def test_empty_rejected(self, raw: str | None) -> None:
        with pytest.raises(VertexConfigError, match="不能为空"):
            validate_vertex_service_account_json(raw)

    def test_size_limit_enforced(self) -> None:
        payload = _service_account_payload(private_key="x" * (64 * 1024))
        with pytest.raises(VertexConfigError, match="64 KiB"):
            validate_vertex_service_account_json(json.dumps(payload))

    def test_non_service_account_type_rejected(self) -> None:
        payload = _service_account_payload(type="external_account")
        with pytest.raises(VertexConfigError, match="service_account"):
            validate_vertex_service_account_json(json.dumps(payload))

    def test_missing_required_fields_rejected(self) -> None:
        payload = _service_account_payload()
        del payload["private_key"]
        del payload["token_uri"]
        with pytest.raises(VertexConfigError, match="private_key、token_uri"):
            validate_vertex_service_account_json(json.dumps(payload))

    def test_non_object_rejected(self) -> None:
        with pytest.raises(VertexConfigError, match="JSON 对象"):
            validate_vertex_service_account_json('["type"]')
