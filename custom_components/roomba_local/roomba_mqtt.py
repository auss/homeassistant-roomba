"""Local MQTT client for Roomba robots (LAN TLS on port 8883).

Connect behavior mirrors the proven LAN recipe: create_default_context,
legacy cipher loop, OP_LEGACY_SERVER_CONNECT (0x4), paho CallbackAPI VERSION1,
client_id=BLID, MQTT 3.1.1, port 8883.
"""

from __future__ import annotations

import errno
import json
import logging
import socket
import ssl
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

import paho.mqtt.client as mqtt

from .const import (
    DEFAULT_PORT,
    MQTT_CMD_TOPIC,
    MQTT_KEEPALIVE,
    MQTT_PROTOCOL,
    TLS_CIPHER_CANDIDATES,
    TLS_OP_LEGACY_SERVER_CONNECT,
)

_LOGGER = logging.getLogger(__name__)

MissionCommand = str  # start | pause | resume | stop | dock | find


class ConnectFailureKind(StrEnum):
    """Classified LAN MQTT failure for config-flow error keys."""

    DNS = "dns_error"
    UNREACHABLE = "unreachable"
    TLS = "tls_error"
    AUTH = "auth_invalid"
    PROTOCOL = "mqtt_protocol"
    TIMEOUT = "mqtt_timeout"
    UNKNOWN = "cannot_connect"


class RoombaMqttError(ConnectionError):
    """Connection failure with a classified cause."""

    def __init__(
        self,
        kind: ConnectFailureKind,
        message: str,
        *,
        endpoint: str | None = None,
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.endpoint = endpoint


def format_endpoint(host: str, port: int) -> str:
    """Format host:port for UI/logs (bracket IPv6)."""
    if ":" in host and not host.startswith("["):
        return f"[{host}]:{port}"
    return f"{host}:{port}"


@dataclass
class RoombaState:
    """Parsed robot reported state used by Home Assistant entities."""

    connected: bool = False
    battery_percent: int | None = None
    phase: str | None = None
    cycle: str | None = None
    error: int | None = None
    bin_present: bool | None = None
    bin_full: bool | None = None
    pose_x: float | None = None
    pose_y: float | None = None
    pose_theta: float | None = None
    name: str | None = None
    sku: str | None = None
    raw_reported: dict[str, Any] = field(default_factory=dict)

    @property
    def location_summary(self) -> str:
        """Human-readable location / pose summary."""
        if self.pose_x is not None and self.pose_y is not None:
            theta = f", θ={self.pose_theta:.0f}" if self.pose_theta is not None else ""
            return f"x={self.pose_x:.0f}, y={self.pose_y:.0f}{theta}"
        if self.phase in {"charge", "recharge"} or self.cycle in {"none", None}:
            if self.phase == "charge":
                return "docked"
        if self.phase in {"hmUsrDock", "hmMidDock", "hmPostMsn"}:
            return "returning"
        if self.phase == "run":
            return "away"
        return "unknown"


def resolve_host(blid: str, host: str | None = None) -> str:
    """Prefer explicit host; else mDNS ``irobot-<blid>.local`` (lowercase)."""
    if host:
        return host.strip()
    return f"irobot-{blid.strip().lower()}.local"


def build_ssl_context() -> ssl.SSLContext:
    """TLS context matching the proven LAN MQTT recipe."""
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE

    last_error: Exception | None = None
    for cipher in TLS_CIPHER_CANDIDATES:
        try:
            context.set_ciphers(cipher)
            _LOGGER.debug("Roomba TLS cipher set to %s", cipher)
            break
        except ssl.SSLError as err:
            last_error = err
    else:
        raise RoombaMqttError(
            ConnectFailureKind.TLS,
            f"Could not configure Roomba TLS ciphers: {last_error}",
        )

    context.options |= TLS_OP_LEGACY_SERVER_CONNECT
    return context


def classify_os_error(err: OSError, host: str, port: int) -> RoombaMqttError:
    """Map socket/OS errors to user-facing failure kinds."""
    endpoint = format_endpoint(host, port)
    err_no = getattr(err, "errno", None)
    if isinstance(err, socket.gaierror):
        return RoombaMqttError(
            ConnectFailureKind.DNS,
            (
                f"DNS/mDNS lookup failed for {endpoint}. "
                f"Use the robot's LAN IP in Host if discovery is unreliable."
            ),
            endpoint=endpoint,
        )
    if isinstance(err, ssl.SSLError):
        return RoombaMqttError(
            ConnectFailureKind.TLS,
            (
                f"TLS handshake to {endpoint} failed ({err}). "
                f"Need AES128-SHA256 / SECLEVEL=0 and legacy server connect."
            ),
            endpoint=endpoint,
        )
    unreachable_errnos = {
        errno.ETIMEDOUT,
        errno.EHOSTUNREACH,
        errno.ENETUNREACH,
        errno.ECONNREFUSED,
        errno.ECONNRESET,
        errno.ENETDOWN,
    }
    if hasattr(errno, "EHOSTDOWN"):
        unreachable_errnos.add(errno.EHOSTDOWN)
    if isinstance(err, TimeoutError) or err_no in unreachable_errnos:
        return RoombaMqttError(
            ConnectFailureKind.UNREACHABLE,
            (
                f"Host {endpoint} is unreachable from this process ({err}). "
                f"Confirm the IP/port and that this same host can run the LAN MQTT smoke script."
            ),
            endpoint=endpoint,
        )
    return RoombaMqttError(
        ConnectFailureKind.UNKNOWN,
        f"Failed to open MQTT socket to {endpoint}: {err}",
        endpoint=endpoint,
    )


def classify_connack(rc: int, host: str, port: int) -> RoombaMqttError:
    """Map MQTT CONNACK return codes to failure kinds."""
    endpoint = format_endpoint(host, port)
    if rc in (4, 5):
        return RoombaMqttError(
            ConnectFailureKind.AUTH,
            (
                f"Robot at {endpoint} rejected MQTT username/password "
                f"(CONNACK {rc}). Check BLID and local MQTT password."
            ),
            endpoint=endpoint,
        )
    if rc == 1:
        return RoombaMqttError(
            ConnectFailureKind.PROTOCOL,
            f"Robot at {endpoint} rejected MQTT protocol version (CONNACK {rc}).",
            endpoint=endpoint,
        )
    if rc == 2:
        return RoombaMqttError(
            ConnectFailureKind.PROTOCOL,
            f"Robot at {endpoint} rejected MQTT client id (CONNACK {rc}).",
            endpoint=endpoint,
        )
    if rc == 3:
        return RoombaMqttError(
            ConnectFailureKind.UNREACHABLE,
            f"Robot MQTT broker at {endpoint} unavailable (CONNACK {rc}).",
            endpoint=endpoint,
        )
    return RoombaMqttError(
        ConnectFailureKind.UNKNOWN,
        f"MQTT CONNACK from {endpoint} failed with rc={rc}.",
        endpoint=endpoint,
    )


def build_command_payload(command: MissionCommand) -> dict[str, Any]:
    """Build a verified local mission command payload."""
    return {
        "command": command,
        "time": int(time.time()),
        "initiator": "localApp",
    }


def map_activity(state: RoombaState) -> str:
    """Map Roomba cycle/phase into a vacuum activity string."""
    phase = (state.phase or "").lower()
    cycle = (state.cycle or "").lower()
    error = state.error or 0

    if error not in (0, None):
        return "error"
    if phase in {"charge", "recharge"} and cycle in {"none", "", "dock"}:
        return "docked"
    if phase in {"hmusrdock", "hmmiddock", "hmpostmsn"}:
        return "returning"
    if phase == "stop" and cycle not in {"none", ""}:
        return "paused"
    if phase == "run" or cycle in {"clean", "quick", "spot"}:
        return "cleaning"
    if phase in {"stuck", "fault"}:
        return "error"
    if cycle in {"none", ""} and phase in {"", "stop", "charge", "recharge"}:
        return "docked" if phase in {"charge", "recharge"} else "idle"
    return "idle"


def merge_reported(existing: dict[str, Any], reported: dict[str, Any]) -> dict[str, Any]:
    """Shallow-merge reported shadow fragments into accumulated state."""
    merged = dict(existing)
    for key, value in reported.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            nested = dict(merged[key])
            nested.update(value)
            merged[key] = nested
        else:
            merged[key] = value
    return merged


def apply_reported(state: RoombaState, reported: dict[str, Any]) -> RoombaState:
    """Update RoombaState fields from a reported shadow document."""
    state.raw_reported = merge_reported(state.raw_reported, reported)

    if "batPct" in reported:
        try:
            state.battery_percent = int(reported["batPct"])
        except (TypeError, ValueError):
            pass

    mission = reported.get("cleanMissionStatus")
    if isinstance(mission, dict):
        if "phase" in mission:
            state.phase = mission.get("phase")
        if "cycle" in mission:
            state.cycle = mission.get("cycle")
        if "error" in mission:
            try:
                state.error = int(mission["error"])
            except (TypeError, ValueError):
                state.error = None

    bin_info = reported.get("bin")
    if isinstance(bin_info, dict):
        if "present" in bin_info:
            state.bin_present = bool(bin_info["present"])
        if "full" in bin_info:
            state.bin_full = bool(bin_info["full"])

    pose = reported.get("pose")
    if isinstance(pose, dict):
        point = pose.get("point")
        if isinstance(point, dict):
            if "x" in point:
                state.pose_x = float(point["x"])
            if "y" in point:
                state.pose_y = float(point["y"])
        if "theta" in pose:
            state.pose_theta = float(pose["theta"])

    if "name" in reported and reported["name"]:
        state.name = str(reported["name"])
    if "sku" in reported and reported["sku"]:
        state.sku = str(reported["sku"])

    return state


def _connack_int(reason_code: Any) -> int:
    """Normalize CONNACK rc (int or ReasonCode)."""
    if hasattr(reason_code, "value"):
        try:
            return int(reason_code.value)
        except (TypeError, ValueError):
            pass
    try:
        return int(reason_code)
    except (TypeError, ValueError):
        return -1


class RoombaMqttClient:
    """Blocking MQTT client run on a background thread for Home Assistant."""

    def __init__(
        self,
        blid: str,
        password: str,
        host: str | None = None,
        port: int = DEFAULT_PORT,
        on_state: Callable[[RoombaState], None] | None = None,
    ) -> None:
        self.blid = blid.strip()
        self.password = password
        self.host = resolve_host(self.blid, host)
        self.port = port
        self._on_state = on_state
        self.state = RoombaState()
        self._lock = threading.RLock()
        self._connected_event = threading.Event()
        self._error: RoombaMqttError | None = None

        # Proven recipe: CallbackAPI VERSION1, client_id = BLID, MQTT 3.1.1.
        client_kwargs: dict[str, Any] = {
            "client_id": self.blid,
            "protocol": mqtt.MQTTv311 if hasattr(mqtt, "MQTTv311") else MQTT_PROTOCOL,
            "clean_session": True,
        }
        if hasattr(mqtt, "CallbackAPIVersion"):
            client_kwargs["callback_api_version"] = mqtt.CallbackAPIVersion.VERSION1
        self._client = mqtt.Client(**client_kwargs)
        self._client.username_pw_set(self.blid, password)
        self._client.tls_set_context(build_ssl_context())
        self._client.on_connect = self._handle_connect
        self._client.on_disconnect = self._handle_disconnect
        self._client.on_message = self._handle_message

    @property
    def last_error(self) -> RoombaMqttError | None:
        """Last connection error, if any."""
        return self._error

    def connect(self, timeout: float = 15.0) -> None:
        """Connect via MQTT/TLS (same path as the working LAN recipe)."""
        self._error = None
        self._connected_event.clear()
        endpoint = format_endpoint(self.host, self.port)

        _LOGGER.warning(
            "Roomba Local dialing MQTT TLS endpoint %s (BLID …%s)",
            endpoint,
            self.blid[-6:],
        )

        # Do not pre-probe TCP: a separate socket path caused false "unreachable"
        # when the working script's MQTT/TLS connect succeeded on the same host.
        try:
            self._client.connect(self.host, self.port, keepalive=MQTT_KEEPALIVE)
        except ssl.SSLError as err:
            raise RoombaMqttError(
                ConnectFailureKind.TLS,
                (
                    f"TLS handshake to {endpoint} failed: {err}. "
                    f"Need AES128-SHA256 with SECLEVEL=0 and legacy server connect."
                ),
                endpoint=endpoint,
            ) from err
        except OSError as err:
            raise classify_os_error(err, self.host, self.port) from err

        self._client.loop_start()
        if not self._connected_event.wait(timeout):
            self.disconnect()
            raise RoombaMqttError(
                ConnectFailureKind.TIMEOUT,
                (
                    f"Timed out waiting for MQTT CONNACK from {endpoint}. "
                    f"TCP/TLS may have opened but MQTT auth/session did not complete."
                ),
                endpoint=endpoint,
            )

        if self._error is not None:
            err = self._error
            self.disconnect()
            raise err

    def disconnect(self) -> None:
        """Stop the network loop and disconnect."""
        try:
            self._client.loop_stop()
            self._client.disconnect()
        except Exception:  # noqa: BLE001 - best-effort shutdown
            _LOGGER.debug("Error during disconnect", exc_info=True)
        with self._lock:
            self.state.connected = False

    def send_command(self, command: MissionCommand) -> None:
        """Publish a mission command to the robot `cmd` topic."""
        payload = json.dumps(build_command_payload(command))
        result = self._client.publish(MQTT_CMD_TOPIC, payload, qos=0)
        if result.rc != mqtt.MQTT_ERR_SUCCESS:
            raise ConnectionError(f"Failed to publish command {command}: rc={result.rc}")
        _LOGGER.debug("Published Roomba command %s", command)

    def get_state(self) -> RoombaState:
        """Return a copy-like snapshot of current state."""
        with self._lock:
            current = self.state
            return RoombaState(
                connected=current.connected,
                battery_percent=current.battery_percent,
                phase=current.phase,
                cycle=current.cycle,
                error=current.error,
                bin_present=current.bin_present,
                bin_full=current.bin_full,
                pose_x=current.pose_x,
                pose_y=current.pose_y,
                pose_theta=current.pose_theta,
                name=current.name,
                sku=current.sku,
                raw_reported=dict(current.raw_reported),
            )

    def _notify(self) -> None:
        if self._on_state is None:
            return
        try:
            self._on_state(self.get_state())
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Error in Roomba state callback")

    def _handle_connect(
        self,
        client: mqtt.Client,
        userdata: Any,
        flags: Any,
        reason_code: Any,
        properties: Any = None,
    ) -> None:
        # VERSION1 signature is (client, userdata, flags, rc); accept optional 5th
        # arg so accidental VERSION2 wiring still works.
        rc = _connack_int(reason_code)
        if rc != 0:
            self._error = classify_connack(rc, self.host, self.port)
            self._connected_event.set()
            return
        with self._lock:
            self.state.connected = True
        client.subscribe("#", qos=0)
        self._connected_event.set()
        self._notify()
        _LOGGER.info("Connected to Roomba MQTT at %s", format_endpoint(self.host, self.port))

    def _handle_disconnect(
        self,
        client: mqtt.Client,
        userdata: Any,
        reason_code: Any,
        properties: Any = None,
    ) -> None:
        # VERSION1: (client, userdata, rc)
        with self._lock:
            self.state.connected = False
        self._notify()
        _LOGGER.warning("Disconnected from Roomba MQTT: %s", reason_code)

    def _handle_message(
        self,
        client: mqtt.Client,
        userdata: Any,
        message: mqtt.MQTTMessage,
    ) -> None:
        try:
            payload = message.payload.decode("utf-8")
            data = json.loads(payload)
        except (UnicodeDecodeError, json.JSONDecodeError, AttributeError):
            return

        reported = _extract_reported(data)
        if not reported:
            return

        with self._lock:
            apply_reported(self.state, reported)
        self._notify()


def _extract_reported(data: dict[str, Any]) -> dict[str, Any] | None:
    """Pull a reported document out of common Roomba MQTT envelope shapes."""
    if not isinstance(data, dict):
        return None
    state = data.get("state")
    if isinstance(state, dict):
        reported = state.get("reported")
        if isinstance(reported, dict):
            return reported
    reported = data.get("reported")
    if isinstance(reported, dict):
        return reported
    if any(key in data for key in ("batPct", "cleanMissionStatus", "pose", "bin")):
        return data
    return None


def validate_connection(blid: str, password: str, host: str | None = None) -> RoombaState:
    """Synchronously connect, wait briefly for state, then disconnect."""
    client = RoombaMqttClient(blid=blid, password=password, host=host)
    try:
        client.connect(timeout=15.0)
        deadline = time.time() + 5.0
        while time.time() < deadline:
            state = client.get_state()
            if state.battery_percent is not None or state.phase is not None:
                return state
            time.sleep(0.2)
        return client.get_state()
    finally:
        client.disconnect()
