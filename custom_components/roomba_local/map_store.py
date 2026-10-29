"""Persist fetched map images only (never credentials)."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .const import DOMAIN
from .map_cloud import MapFetchResult

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

MAP_FILENAME = "map.png"
META_FILENAME = "map_meta.json"

# Keys allowed in persisted metadata — never secrets.
SAFE_META_KEYS = frozenset(
    {"blid", "pmap_id", "pmapv_id", "fetched_at", "robot_name", "notes", "content_type"}
)


def map_directory(hass: HomeAssistant, blid: str) -> Path:
    """Return per-robot storage directory under HA config."""
    return Path(hass.config.path(DOMAIN, blid.lower()))


def save_map_result(hass: HomeAssistant, result: MapFetchResult) -> Path:
    """Write PNG + non-secret metadata; return path to the PNG."""
    directory = map_directory(hass, result.blid)
    directory.mkdir(parents=True, exist_ok=True)
    png_path = directory / MAP_FILENAME
    png_path.write_bytes(result.png_bytes)
    meta = {
        "blid": result.blid,
        "pmap_id": result.pmap_id,
        "pmapv_id": result.pmapv_id,
        "fetched_at": result.fetched_at,
        "robot_name": result.robot_name,
        "notes": list(result.notes),
        "content_type": result.content_type,
    }
    (directory / META_FILENAME).write_text(json.dumps(meta, indent=2), encoding="utf-8")
    _LOGGER.info("Saved Roomba map image for %s to %s", result.blid[-6:], png_path)
    return png_path


def load_map_png(hass: HomeAssistant, blid: str) -> bytes | None:
    """Load previously fetched map PNG bytes, if any."""
    path = map_directory(hass, blid) / MAP_FILENAME
    if not path.is_file():
        return None
    return path.read_bytes()


def load_map_meta(hass: HomeAssistant, blid: str) -> dict[str, Any] | None:
    """Load non-secret map metadata."""
    path = map_directory(hass, blid) / META_FILENAME
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    return {k: v for k, v in data.items() if k in SAFE_META_KEYS}


def assert_no_secrets_in_entry_data(data: dict[str, Any]) -> None:
    """Raise if a config-entry-like dict contains cloud account secrets."""
    forbidden = {
        "email",
        "account_email",
        "account_password",
        "cloud_password",
        "session_token",
        "SessionToken",
        "SecretKey",
        "AccessKeyId",
        "gigya",
        "iot_token",
    }
    forbidden_lower = {f.lower() for f in forbidden}
    for key in data:
        if key in forbidden or key.lower() in forbidden_lower:
            raise AssertionError(f"Secret-like key persisted in entry data: {key}")
    if "loginID" in data or "UIDSignature" in data:
        raise AssertionError("Gigya fields must not be persisted")
