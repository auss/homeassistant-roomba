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


def resolve_ipv4(host: str, port: int) -> str:
    """Resolve *host* to an IPv4 literal (Roomba MQTT is IPv4-only in practice)."""
    infos = socket.getaddrinfo(host, port, socket.AF_INET, socket.SOCK_STREAM)
    if not infos:
        raise socket.gaierror(socket.EAI_NONAME, f"No IPv4 address for {host}")
    return str(infos[0][4][0])


def open_ipv4_tcp(host: str, port: int, *, timeout: float) -> socket.socket:
    """TCP connect like ``nc``: AF_INET only, no source bind, no proxy.

    paho's default path uses ``socket.create_connection(..., source_address=('', 0))``
    which always binds before connect and may walk non-IPv4 results. Match nc / the
    working LAN script as closely as possible.
    """
    infos = socket.getaddrinfo(host, port, socket.AF_INET, socket.SOCK_STREAM)
    if not infos:
        raise OSError(errno.ENETUNREACH, f"No IPv4 route to {host}:{port}")
    last_err: OSError | None = None
    for _family, socktype, proto, _canon, sockaddr in infos:
        sock = socket.socket(socket.AF_INET, socktype, proto)
        sock.settimeout(timeout)
        try:
            sock.connect(sockaddr)
            return sock
        except OSError as err:
            last_err = err
            sock.close()
    assert last_err is not None
    raise last_err


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


def log_addrinfo(host: str, port: int) -> list[str]:
    """Resolve and log getaddrinfo results; return human-readable sockaddr list."""
    endpoint = format_endpoint(host, port)
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as err:
        _LOGGER.warning("getaddrinfo failed for %s: %s", endpoint, err)
        raise
    rendered: list[str] = []
    for family, _socktype, proto, _canon, sockaddr in infos:
        family_name = {socket.AF_INET: "IPv4", socket.AF_INET6: "IPv6"}.get(
            family, str(family)
        )
        rendered.append(f"{family_name} {sockaddr} (proto={proto})")
    _LOGGER.warning(
        "Roomba Local getaddrinfo for %s → %s",
        endpoint,
        "; ".join(rendered) if rendered else "(empty)",
    )
    return rendered


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
    if err_no == errno.ECONNREFUSED:
        return RoombaMqttError(
            ConnectFailureKind.UNREACHABLE,
            (
                f"Connection refused to {endpoint} (errno 111). "
                f"Robot MQTT accepts only ONE local client — disable Core Roomba "
                f"(roombapy), close the iRobot app LAN session, and stop other "
                f"LAN MQTT scripts, then retry. If nc -vz to that IP:8883 is open "
                f"inside the HA container, networking is fine: run "
                f"scripts/lan_mqtt_smoke.py inside the same container to verify "
                f"this client path."
            ),
            endpoint=endpoint,
        )
    unreachable_errnos = {
        errno.ETIMEDOUT,
        errno.EHOSTUNREACH,
        errno.ENETUNREACH,
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
                f"Confirm the config-entry Host matches the IP you tested with nc, "
                f"and that nothing else holds the robot's single MQTT slot."
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


def build_region_start_payload(
    *,
    pmap_id: str,
    regions: list[dict[str, str]],
    user_pmapv_id: str | None = None,
    region_params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Selective clean: start + pmap_id + ordered regions (local MQTT cmd).

    When ``region_params`` is set (mode/power/mop), it is copied onto each region
    (Combo app shape). Callers may omit it for a bare regions-only start.
    """
    region_list: list[dict[str, Any]] = []
    for r in regions:
        entry: dict[str, Any] = {
            "region_id": str(r["region_id"]),
            "type": str(r.get("type") or "rid"),
        }
        if region_params:
            entry["params"] = dict(region_params)
        region_list.append(entry)
    payload: dict[str, Any] = {
        "command": "start",
        "time": int(time.time()),
        "initiator": "localApp",
        "pmap_id": pmap_id,
        "regions": region_list,
        "ordered": 1,
    }
    if user_pmapv_id:
        payload["user_pmapv_id"] = user_pmapv_id
    return payload


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
        self._client: mqtt.Client | None = None
        self._tls_ready = False
        self._dial_host = self.host

    def _ensure_client(self) -> mqtt.Client:
        """Build paho client + TLS context lazily (executor-safe, not on HA loop)."""
        if self._client is not None and self._tls_ready:
            return self._client

        # Proven recipe: CallbackAPI VERSION1, client_id = BLID, MQTT 3.1.1.
        client_kwargs: dict[str, Any] = {
            "client_id": self.blid,
            "protocol": mqtt.MQTTv311 if hasattr(mqtt, "MQTTv311") else MQTT_PROTOCOL,
            "clean_session": True,
        }
        if hasattr(mqtt, "CallbackAPIVersion"):
            client_kwargs["callback_api_version"] = mqtt.CallbackAPIVersion.VERSION1
        client = mqtt.Client(**client_kwargs)
        client.username_pw_set(self.blid, self.password)
        # ssl.create_default_context() must not run on the Home Assistant event loop.
        client.tls_set_context(build_ssl_context())
        # paho 2.x still calls ssl.match_hostname (removed in Python 3.12) unless
        # tls_insecure is set — required with CERT_NONE / IP hosts.
        client.tls_insecure_set(True)
        client.on_connect = self._handle_connect
        client.on_disconnect = self._handle_disconnect
        client.on_message = self._handle_message
        # Prefer a longer TCP timeout than paho's 5s default.
        client._connect_timeout = 30  # noqa: SLF001 - paho has no public setter
        self._client = client
        self._tls_ready = True
        return client

    def _install_direct_ipv4_socket(self, client: mqtt.Client, ipv4: str) -> None:
        """Make paho open TCP like nc: AF_INET, no source bind, no mqtt proxy."""

        def _create_socket_connection() -> socket.socket:
            sock = open_ipv4_tcp(
                ipv4, self.port, timeout=float(client._connect_timeout)
            )
            local = sock.getsockname()
            peer = sock.getpeername()
            _LOGGER.warning(
                "Roomba Local TCP established %s → %s (AF_INET, no source bind)",
                local,
                peer,
            )
            return sock

        # Instance override — avoid paho's create_connection(source_address=('',0)).
        client._create_socket_connection = _create_socket_connection  # type: ignore[method-assign]

    @property
    def last_error(self) -> RoombaMqttError | None:
        """Last connection error, if any."""
        return self._error

    def connect(self, timeout: float = 15.0) -> None:
        """Connect via MQTT/TLS (working LAN recipe + forced IPv4 TCP)."""
        self._error = None
        self._connected_event.clear()
        client = self._ensure_client()

        try:
            log_addrinfo(self.host, self.port)
            ipv4 = resolve_ipv4(self.host, self.port)
        except socket.gaierror as err:
            raise classify_os_error(err, self.host, self.port) from err

        # Dial the resolved IPv4 literal so getaddrinfo cannot prefer a dead AAAA.
        dial_host = ipv4
        endpoint = format_endpoint(dial_host, self.port)
        if dial_host != self.host:
            _LOGGER.warning(
                "Roomba Local resolved %s → IPv4 %s for MQTT dial",
                self.host,
                dial_host,
            )
        self._dial_host = dial_host
        self._install_direct_ipv4_socket(client, dial_host)

        _LOGGER.warning(
            "Roomba Local dialing MQTT TLS endpoint %s (BLID …%s)",
            endpoint,
            self.blid[-6:],
        )

        # Do not pre-probe TCP with a separate socket — use one connect path only.
        try:
            client.connect(dial_host, self.port, keepalive=MQTT_KEEPALIVE)
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
            raise classify_os_error(err, dial_host, self.port) from err
        except AttributeError as err:
            # paho + Python 3.12 without tls_insecure_set hits missing match_hostname.
            raise RoombaMqttError(
                ConnectFailureKind.TLS,
                (
                    f"TLS post-handshake failed for {endpoint}: {err}. "
                    f"Need tls_insecure_set with CERT_NONE on Python 3.12+."
                ),
                endpoint=endpoint,
            ) from err

        client.loop_start()
        if not self._connected_event.wait(timeout):
            self.disconnect()
            raise RoombaMqttError(
                ConnectFailureKind.TIMEOUT,
                (
                    f"Timed out waiting for MQTT CONNACK from {endpoint}. "
                    f"TCP/TLS may have opened but MQTT auth/session did not complete. "
                    f"Another client may hold the robot's single MQTT slot."
                ),
                endpoint=endpoint,
            )

        if self._error is not None:
            err = self._error
            self.disconnect()
            raise err

    def disconnect(self) -> None:
        """Stop the network loop and disconnect."""
        client = self._client
        if client is not None:
            try:
                client.loop_stop()
                client.disconnect()
            except Exception:  # noqa: BLE001 - best-effort shutdown
                _LOGGER.debug("Error during disconnect", exc_info=True)
        with self._lock:
            self.state.connected = False

    def send_command(self, command: MissionCommand) -> None:
        """Publish a mission command to the robot `cmd` topic."""
        self.publish_cmd(build_command_payload(command))

    def publish_cmd(self, payload: dict[str, Any]) -> None:
        """Publish an arbitrary verified payload to the robot `cmd` topic."""
        client = self._client
        if client is None or not self.state.connected:
            raise ConnectionError("Roomba MQTT client is not connected")
        body = json.dumps(payload)
        result = client.publish(MQTT_CMD_TOPIC, body, qos=0)
        if result.rc != mqtt.MQTT_ERR_SUCCESS:
            raise ConnectionError(
                f"Failed to publish command {payload.get('command')}: rc={result.rc}"
            )
        _LOGGER.debug("Published Roomba cmd %s", payload.get("command"))

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
        dial = getattr(self, "_dial_host", self.host)
        if rc != 0:
            self._error = classify_connack(rc, dial, self.port)
            self._connected_event.set()
            return
        with self._lock:
            self.state.connected = True
        client.subscribe("#", qos=0)
        self._connected_event.set()
        self._notify()
        _LOGGER.info("Connected to Roomba MQTT at %s", format_endpoint(dial, self.port))

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
