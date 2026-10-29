"""Unit tests for Roomba local MQTT helpers (no live robot required)."""

from __future__ import annotations

import errno
import json
import socket
import ssl

from custom_components.roomba_local.const import TLS_OP_LEGACY_SERVER_CONNECT
from custom_components.roomba_local.roomba_mqtt import (
    ConnectFailureKind,
    RoombaState,
    _connack_int,
    _extract_reported,
    apply_reported,
    build_command_payload,
    build_ssl_context,
    classify_connack,
    classify_os_error,
    format_endpoint,
    map_activity,
    merge_reported,
    resolve_host,
)


def test_resolve_host_prefers_explicit() -> None:
    assert resolve_host("ABCDEF", "192.168.1.50") == "192.168.1.50"
    assert resolve_host("abcdef", " 10.0.0.8 ") == "10.0.0.8"


def test_resolve_host_mdns_fallback_lowercase() -> None:
    assert resolve_host("ABCDEF") == "irobot-abcdef.local"
    assert resolve_host("AbCdEf") == "irobot-abcdef.local"


def test_build_command_payload_shape() -> None:
    payload = build_command_payload("start")
    assert payload["command"] == "start"
    assert payload["initiator"] == "localApp"
    assert isinstance(payload["time"], int)


def test_ssl_context_matches_working_recipe() -> None:
    ctx = build_ssl_context()
    assert ctx.verify_mode == ssl.CERT_NONE
    assert ctx.check_hostname is False
    assert ctx.options & TLS_OP_LEGACY_SERVER_CONNECT
    # Must not require the aggressive 0x40000 renegotiation bit.


def test_format_endpoint() -> None:
    assert format_endpoint("192.168.132.27", 8883) == "192.168.132.27:8883"
    assert format_endpoint("irobot-abcdef.local", 8883) == "irobot-abcdef.local:8883"
    assert format_endpoint("2001:db8::1", 8883) == "[2001:db8::1]:8883"


def test_classify_dns_and_unreachable_errors() -> None:
    host, port = "irobot.local", 8883
    dns_err = classify_os_error(socket.gaierror(-2, "name not known"), host, port)
    assert dns_err.kind == ConnectFailureKind.DNS
    assert dns_err.endpoint == "irobot.local:8883"

    unreach = classify_os_error(OSError(errno.EHOSTUNREACH, "no route"), host, port)
    assert unreach.kind == ConnectFailureKind.UNREACHABLE
    assert unreach.endpoint == "irobot.local:8883"


def test_classify_tls_and_auth_errors() -> None:
    tls = classify_os_error(ssl.SSLError("WRONG_SIGNATURE_TYPE"), "10.0.0.5", 8883)
    assert tls.kind == ConnectFailureKind.TLS
    assert tls.endpoint == "10.0.0.5:8883"

    auth = classify_connack(4, "10.0.0.5", 8883)
    assert auth.kind == ConnectFailureKind.AUTH
    assert auth.endpoint == "10.0.0.5:8883"

    proto = classify_connack(2, "10.0.0.5", 8883)
    assert proto.kind == ConnectFailureKind.PROTOCOL


def test_connack_int_handles_reason_code_objects() -> None:
    assert _connack_int(0) == 0
    assert _connack_int(5) == 5

    class Reason:
        def __init__(self, value: int) -> None:
            self.value = value

    assert _connack_int(Reason(0)) == 0
    assert _connack_int(Reason(4)) == 4


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
    assert map_activity(state) == "cleaning"


def test_map_activity_docked_paused_returning_error() -> None:
    assert map_activity(RoombaState(phase="charge", cycle="none", error=0)) == "docked"
    assert map_activity(RoombaState(phase="stop", cycle="clean", error=0)) == "paused"
    assert map_activity(RoombaState(phase="hmUsrDock", cycle="clean", error=0)) == "returning"
    assert map_activity(RoombaState(phase="run", cycle="clean", error=14)) == "error"


def test_merge_reported_nested() -> None:
    existing = {"cleanMissionStatus": {"cycle": "clean", "phase": "run"}, "batPct": 50}
    updated = merge_reported(existing, {"cleanMissionStatus": {"phase": "stop"}, "batPct": 49})
    assert updated["batPct"] == 49
    assert updated["cleanMissionStatus"]["cycle"] == "clean"
    assert updated["cleanMissionStatus"]["phase"] == "stop"


def test_extract_reported_envelopes() -> None:
    assert _extract_reported({"state": {"reported": {"batPct": 1}}}) == {"batPct": 1}
    assert _extract_reported({"reported": {"batPct": 2}}) == {"batPct": 2}
    assert _extract_reported({"batPct": 3}) == {"batPct": 3}
    assert _extract_reported({"unrelated": True}) is None


def test_command_payload_json_serializable() -> None:
    for command in ("start", "pause", "resume", "stop", "dock", "find"):
        json.dumps(build_command_payload(command))
