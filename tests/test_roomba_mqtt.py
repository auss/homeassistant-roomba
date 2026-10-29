"""Unit tests for Roomba local MQTT helpers (no live robot required)."""

from __future__ import annotations

import errno
import json
import socket
import ssl
from unittest.mock import MagicMock, patch

import pytest
from custom_components.roomba_local.roomba_mqtt import (
    ConnectFailureKind,
    RoombaMqttError,
    RoombaState,
    _connack_int,
    _extract_reported,
    apply_reported,
    build_command_payload,
    build_ssl_context,
    classify_connack,
    classify_os_error,
    map_activity,
    merge_reported,
    probe_tcp,
    resolve_host,
)


def test_resolve_host_prefers_explicit() -> None:
    assert resolve_host("ABCDEF", "192.168.1.50") == "192.168.1.50"
    assert resolve_host("abcdef", " 10.0.0.8 ") == "10.0.0.8"


def test_resolve_host_mdns_fallback_uppercases_blid() -> None:
    assert resolve_host("abcdef") == "iRobot-ABCDEF.local"


def test_build_command_payload_shape() -> None:
    payload = build_command_payload("start")
    assert payload["command"] == "start"
    assert payload["initiator"] == "localApp"
    assert isinstance(payload["time"], int)


def test_ssl_context_uses_legacy_openssl3_settings() -> None:
    ctx = build_ssl_context()
    assert ctx.verify_mode == ssl.CERT_NONE
    assert ctx.check_hostname is False
    if hasattr(ssl, "OP_LEGACY_SERVER_CONNECT"):
        assert ctx.options & ssl.OP_LEGACY_SERVER_CONNECT
    # Unsafe legacy renegotiation bit used by OpenSSL 3 Roomba clients.
    assert ctx.options & 0x40000


def test_classify_dns_and_unreachable_errors() -> None:
    host, port = "irobot.local", 8883
    dns_err = classify_os_error(socket.gaierror(-2, "name not known"), host, port)
    assert dns_err.kind == ConnectFailureKind.DNS
    assert "DNS" in str(dns_err) or "mDNS" in str(dns_err)

    unreach = classify_os_error(OSError(errno.EHOSTUNREACH, "no route"), host, port)
    assert unreach.kind == ConnectFailureKind.UNREACHABLE
    assert "unreachable" in str(unreach).lower()


def test_classify_tls_and_auth_errors() -> None:
    tls = classify_os_error(ssl.SSLError("WRONG_SIGNATURE_TYPE"), "10.0.0.5", 8883)
    assert tls.kind == ConnectFailureKind.TLS
    assert "TLS" in str(tls)

    auth = classify_connack(4, "10.0.0.5", 8883)
    assert auth.kind == ConnectFailureKind.AUTH
    assert "password" in str(auth).lower()

    proto = classify_connack(2, "10.0.0.5", 8883)
    assert proto.kind == ConnectFailureKind.PROTOCOL


def test_connack_int_handles_reason_code_objects() -> None:
    assert _connack_int(0) == 0
    assert _connack_int(5) == 5

    class Reason:
        def __init__(self, value: int) -> None:
            self.value = value
            self.is_failure = value != 0

    assert _connack_int(Reason(0)) == 0
    assert _connack_int(Reason(4)) == 4


@patch("custom_components.roomba_local.roomba_mqtt.socket.getaddrinfo")
@patch("custom_components.roomba_local.roomba_mqtt.socket.socket")
def test_probe_tcp_success(mock_socket_cls: MagicMock, mock_gai: MagicMock) -> None:
    mock_gai.return_value = [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.5", 8883)),
    ]
    sock = MagicMock()
    mock_socket_cls.return_value.__enter__.return_value = sock
    probe_tcp("10.0.0.5", 8883)
    sock.connect.assert_called_once_with(("10.0.0.5", 8883))


@patch("custom_components.roomba_local.roomba_mqtt.socket.getaddrinfo")
def test_probe_tcp_dns_failure(mock_gai: MagicMock) -> None:
    mock_gai.side_effect = socket.gaierror(-2, "not found")
    with pytest.raises(RoombaMqttError) as raised:
        probe_tcp("missing.local", 8883)
    assert raised.value.kind == ConnectFailureKind.DNS


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
