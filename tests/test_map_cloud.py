"""Tests for on-demand map fetch (mocked cloud) and secret non-persistence."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from custom_components.roomba_local.map_cloud import (
    MapFetchError,
    fetch_map_image,
    render_map_png,
)
from custom_components.roomba_local.map_store import (
    SAFE_META_KEYS,
    assert_no_secrets_in_entry_data,
)


def _discovery() -> dict[str, Any]:
    return {
        "gigya": {
            "api_key": "fake-api-key",
            "datacenter_domain": "us1.gigya.com",
        },
        "deployments": {
            "current": {
                "httpBase": "https://example-http.irobotapi.test",
                "httpBaseAuth": "https://example-auth.irobotapi.test",
                "awsRegion": "us-east-1",
            }
        },
    }


def _login_payload(blid: str = "ABCDEF123456") -> dict[str, Any]:
    return {
        "credentials": {
            "AccessKeyId": "AKIATEST",
            "SecretKey": "secret-test",
            "SessionToken": "session-test",
        },
        "robots": {
            blid: {"name": "Rosie", "sku": "j9+"},
        },
    }


def test_render_map_png_with_and_without_geometry() -> None:
    empty = render_map_png({}, title="T", subtitle="S")
    assert empty[:8] == b"\x89PNG\r\n\x1a\n"

    umf = {
        "regions": [
            {
                "name": "Kitchen",
                "polygon": [[0, 0], [10, 0], [10, 5], [0, 5]],
            }
        ]
    }
    drawn = render_map_png(umf, title="Rosie", subtitle="pmap=1")
    assert drawn[:8] == b"\x89PNG\r\n\x1a\n"
    assert len(drawn) > 100
    assert len(empty) > 100


def test_assert_no_secrets_rejects_cloud_fields() -> None:
    assert_no_secrets_in_entry_data({"last_map_fetched_at": "2026-01-01T00:00:00Z"})
    with pytest.raises(AssertionError):
        assert_no_secrets_in_entry_data({"email": "a@b.c"})
    with pytest.raises(AssertionError):
        assert_no_secrets_in_entry_data({"SessionToken": "x"})
    with pytest.raises(AssertionError):
        assert_no_secrets_in_entry_data({"UIDSignature": "x"})


@patch("custom_components.roomba_local.map_cloud._http_json")
def test_fetch_map_image_success_scrubs_and_returns_png(mock_http: MagicMock) -> None:
    blid = "ABCDEF123456"
    umf = {
        "regions": [
            {"id": "1", "name": "Kitchen", "type": "rid", "polygon": [[0, 0], [1, 0], [1, 1]]}
        ]
    }

    def side_effect(method: str, url: str, **kwargs: Any) -> Any:
        if "discover/endpoints" in url:
            return _discovery()
        if "accounts.login" in url:
            # Ensure password was sent in form for this call only.
            assert kwargs.get("form", {}).get("password") == "account-secret"
            return {
                "UID": "uid-1",
                "UIDSignature": "sig-1",
                "signatureTimestamp": "123",
            }
        if url.endswith("/v2/login"):
            return _login_payload(blid)
        if "/pmaps?" in url or url.rstrip("/").endswith("/pmaps"):
            return [
                {
                    "pmap_id": "pmap-1",
                    "active_pmapv_details": {
                        "active_pmapv": {"pmapv_id": "v-1"},
                    },
                }
            ]
        if "/umf" in url:
            return umf
        raise AssertionError(f"Unexpected URL {url}")

    mock_http.side_effect = side_effect

    # Patch signed_get_json path: _http_json is used for all; signed URLs include host.
    # For SigV4 GETs, URL is https://example-auth.../v1/...
    def side_effect_full(method: str, url: str, **kwargs: Any) -> Any:
        if "discover/endpoints" in url:
            return _discovery()
        if "accounts.login" in url:
            assert "account-secret" in (kwargs.get("form") or {}).values()
            return {
                "UID": "uid-1",
                "UIDSignature": "sig-1",
                "signatureTimestamp": "123",
            }
        if url.endswith("/v2/login"):
            body = json.loads(kwargs["data"].decode())
            assert "password" not in json.dumps(body)
            return _login_payload(blid)
        if "/pmaps/" in url and "/umf" in url:
            return umf
        if "/pmaps" in url:
            return [
                {
                    "pmap_id": "pmap-1",
                    "active_pmapv_details": {
                        "active_pmapv": {"pmapv_id": "v-1"},
                    },
                }
            ]
        raise AssertionError(f"Unexpected URL {url}")

    mock_http.side_effect = side_effect_full

    result = fetch_map_image(
        email="user@example.com",
        password="account-secret",
        blid=blid,
        country="US",
    )
    assert result.png_bytes.startswith(b"\x89PNG")
    assert result.pmap_id == "pmap-1"
    assert result.pmapv_id == "v-1"
    assert result.rooms == [{"name": "Kitchen", "region_id": "1", "type": "rid"}]
    assert result.robot_name == "Rosie"
    # Result dataclass must not carry secrets.
    dumped = json.dumps(result.__dict__, default=lambda o: None)
    assert "account-secret" not in dumped
    assert "session-test" not in dumped
    assert "secret-test" not in dumped
    assert "user@example.com" not in dumped


@patch("custom_components.roomba_local.map_cloud._http_json")
def test_fetch_map_image_no_maps_errors(mock_http: MagicMock) -> None:
    blid = "ABCDEF123456"

    def side_effect(method: str, url: str, **kwargs: Any) -> Any:
        if "discover/endpoints" in url:
            return _discovery()
        if "accounts.login" in url:
            return {
                "UID": "uid-1",
                "UIDSignature": "sig-1",
                "signatureTimestamp": "123",
            }
        if url.endswith("/v2/login"):
            return _login_payload(blid)
        if "/pmaps" in url:
            return []
        raise AssertionError(url)

    mock_http.side_effect = side_effect
    with pytest.raises(MapFetchError, match="No active persistent maps"):
        fetch_map_image(
            email="user@example.com",
            password="account-secret",
            blid=blid,
        )


def test_save_map_result_writes_png_and_safe_meta(tmp_path: Path) -> None:
    from custom_components.roomba_local import map_store
    from custom_components.roomba_local.map_cloud import MapFetchResult

    class FakeConfig:
        def path(self, *parts: str) -> str:
            return str(tmp_path.joinpath(*parts))

    class FakeHass:
        config = FakeConfig()

    result = MapFetchResult(
        png_bytes=b"\x89PNG\r\n\x1a\n" + b"fakepng",
        blid="ABCDEF123456",
        pmap_id="pmap-1",
        pmapv_id="v-1",
        fetched_at="2026-10-29T12:00:00+00:00",
        robot_name="Rosie",
        notes=["n1"],
    )
    path = map_store.save_map_result(FakeHass(), result)  # type: ignore[arg-type]
    assert path.is_file()
    meta = json.loads((path.parent / "map_meta.json").read_text(encoding="utf-8"))
    assert set(meta).issubset(SAFE_META_KEYS)
    assert "password" not in meta
    assert "email" not in meta
    assert_no_secrets_in_entry_data(meta)
