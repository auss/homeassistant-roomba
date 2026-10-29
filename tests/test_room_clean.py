"""Tests for room extraction and selective clean payloads."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

from custom_components.roomba_local.map_cloud import MapFetchResult, extract_rooms
from custom_components.roomba_local.map_store import (
    ROOMS_FILENAME,
    resolve_room_names,
    save_map_result,
)
from custom_components.roomba_local.roomba_mqtt import (
    build_command_payload,
    build_region_start_payload,
)


def test_extract_rooms_from_umf() -> None:
    umf = {
        "regions": [
            {"id": "1", "name": "Kitchen", "type": "rid"},
            {"region_id": "2", "name": "Hall", "type": "zid"},
            {"id": "3"},  # nameless — skip
        ]
    }
    rooms = extract_rooms(umf)
    assert rooms == [
        {"name": "Kitchen", "region_id": "1", "type": "rid"},
        {"name": "Hall", "region_id": "2", "type": "zid"},
    ]


def test_resolve_room_names_case_insensitive() -> None:
    rooms = [
        {"name": "Kitchen", "region_id": "1", "type": "rid"},
        {"name": "Office", "region_id": "9", "type": "rid"},
    ]
    matched = resolve_room_names(rooms, ["kitchen", "OFFICE"])
    assert [r["region_id"] for r in matched] == ["1", "9"]


def test_resolve_room_names_unknown() -> None:
    rooms = [{"name": "Kitchen", "region_id": "1", "type": "rid"}]
    try:
        resolve_room_names(rooms, ["Garage"])
        raise AssertionError("expected KeyError")
    except KeyError as err:
        assert "Garage" in str(err)
        assert "Kitchen" in str(err)


def test_region_start_payload_shape() -> None:
    payload = build_region_start_payload(
        pmap_id="pmap-abc",
        regions=[{"region_id": "1", "type": "rid"}],
        user_pmapv_id="v-1",
    )
    assert payload["command"] == "start"
    assert payload["initiator"] == "localApp"
    assert payload["pmap_id"] == "pmap-abc"
    assert payload["user_pmapv_id"] == "v-1"
    assert payload["ordered"] == 1
    assert payload["regions"] == [{"region_id": "1", "type": "rid"}]
    assert isinstance(payload["time"], int)

    # Whole-home start stays minimal (no regions).
    whole = build_command_payload("start")
    assert "regions" not in whole
    assert "pmap_id" not in whole


def test_save_map_result_writes_rooms(tmp_path: Path, monkeypatch) -> None:
    hass = MagicMock()
    hass.config.path = lambda *parts: str(tmp_path.joinpath(*parts))

    result = MapFetchResult(
        png_bytes=b"\x89PNG\r\n\x1a\n" + b"\x00" * 20,
        blid="ABCDEF123456",
        pmap_id="pmap-1",
        pmapv_id="v-1",
        fetched_at="2026-01-01T00:00:00Z",
        rooms=[{"name": "Kitchen", "region_id": "1", "type": "rid"}],
    )
    save_map_result(hass, result)
    rooms_path = tmp_path / "roomba_local" / "abcdef123456" / ROOMS_FILENAME
    assert rooms_path.is_file()
    data = json.loads(rooms_path.read_text(encoding="utf-8"))
    assert data[0]["name"] == "Kitchen"
    assert data[0]["region_id"] == "1"
