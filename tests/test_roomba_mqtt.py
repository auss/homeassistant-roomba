"""Unit tests for Roomba local MQTT helpers (no live robot required)."""

from __future__ import annotations

import json
import ssl

from custom_components.roomba_local.roomba_mqtt import (
    RoombaState,
    _extract_reported,
    apply_reported,
    build_command_payload,
    build_ssl_context,
    map_activity,
    merge_reported,
    resolve_host,
)


def test_resolve_host_prefers_explicit() -> None:
    assert resolve_host("ABCDEF", "192.168.1.50") == "192.168.1.50"


def test_resolve_host_mdns_fallback() -> None:
    assert resolve_host("ABCDEF") == "iRobot-ABCDEF.local"


def test_build_command_payload_shape() -> None:
    payload = build_command_payload("start")
    assert payload["command"] == "start"
    assert payload["initiator"] == "localApp"
    assert isinstance(payload["time"], int)


def test_ssl_context_uses_legacy_cipher() -> None:
    ctx = build_ssl_context()
    assert ctx.verify_mode == ssl.CERT_NONE
    assert ctx.check_hostname is False


def test_apply_reported_mission_and_battery() -> None:
    state = RoombaState()
    apply_reported(
        state,
        {
            "batPct": 87,
            "cleanMissionStatus": {"cycle": "clean", "phase": "run", "error": 0},
            "bin": {"present": True, "full": False},
            "pose": {"theta": 90, "point": {"x": 10, "y": 20}},
            "name": "Rosie",
            "sku": "j9+",
        },
    )
    assert state.battery_percent == 87
    assert state.phase == "run"
    assert state.cycle == "clean"
    assert state.bin_full is False
    assert state.pose_x == 10
    assert state.pose_y == 20
    assert state.pose_theta == 90
    assert state.name == "Rosie"
    assert map_activity(state) == "cleaning"
    assert "x=10" in state.location_summary


def test_map_activity_docked_paused_returning_error() -> None:
    docked = RoombaState(phase="charge", cycle="none", error=0)
    assert map_activity(docked) == "docked"

    paused = RoombaState(phase="stop", cycle="clean", error=0)
    assert map_activity(paused) == "paused"

    returning = RoombaState(phase="hmUsrDock", cycle="clean", error=0)
    assert map_activity(returning) == "returning"

    errored = RoombaState(phase="run", cycle="clean", error=14)
    assert map_activity(errored) == "error"


def test_merge_reported_nested() -> None:
    existing = {"cleanMissionStatus": {"cycle": "clean", "phase": "run"}, "batPct": 50}
    updated = merge_reported(existing, {"cleanMissionStatus": {"phase": "stop"}, "batPct": 49})
    assert updated["batPct"] == 49
    assert updated["cleanMissionStatus"]["cycle"] == "clean"
    assert updated["cleanMissionStatus"]["phase"] == "stop"


def test_extract_reported_envelopes() -> None:
    shadow = {"state": {"reported": {"batPct": 1}}}
    assert _extract_reported(shadow) == {"batPct": 1}
    assert _extract_reported({"reported": {"batPct": 2}}) == {"batPct": 2}
    assert _extract_reported({"batPct": 3}) == {"batPct": 3}
    assert _extract_reported({"unrelated": True}) is None


def test_command_payload_json_serializable() -> None:
    for command in ("start", "pause", "resume", "stop", "dock", "find"):
        json.dumps(build_command_payload(command))
