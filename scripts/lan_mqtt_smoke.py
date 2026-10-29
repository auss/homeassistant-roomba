#!/usr/bin/env python3
"""Standalone LAN MQTT smoke — same connect path as roomba_local.

Run on the Home Assistant / Proxmox host (or any host that can reach the robot):

  python3 scripts/lan_mqtt_smoke.py --blid YOUR_BLID --password YOUR_PASSWORD \\
      --host 192.168.x.x

Omit --host to try mDNS irobot-<blid>.local (lowercase).
Does not print the password. Exit 0 on CONNACK success.

Requires: pip install 'paho-mqtt>=2.1.0,<3'
"""

from __future__ import annotations

import argparse
import importlib.util
import sys
import time
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "custom_components" / "roomba_local"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _load_roomba_mqtt():
    """Load MQTT helpers without importing Home Assistant via package __init__."""
    sys.modules.setdefault("custom_components", types.ModuleType("custom_components"))
    parent = types.ModuleType("custom_components.roomba_local")
    parent.__path__ = [str(PKG)]
    sys.modules["custom_components.roomba_local"] = parent
    const = _load("custom_components.roomba_local.const", PKG / "const.py")
    parent.const = const
    return _load("custom_components.roomba_local.roomba_mqtt", PKG / "roomba_mqtt.py")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--blid", required=True)
    parser.add_argument("--password", required=True)
    parser.add_argument(
        "--host",
        default=None,
        help="Robot LAN IP/hostname (recommended). Default: irobot-<blid>.local",
    )
    parser.add_argument("--timeout", type=float, default=15.0)
    args = parser.parse_args()

    mqtt = _load_roomba_mqtt()
    host = mqtt.resolve_host(args.blid, args.host)
    endpoint = mqtt.format_endpoint(host, 8883)
    print(f"Dialing {endpoint} (TLS MQTT, client_id=BLID, CallbackAPI VERSION1)…")

    client = mqtt.RoombaMqttClient(
        blid=args.blid, password=args.password, host=args.host
    )
    try:
        client.connect(timeout=args.timeout)
    except mqtt.RoombaMqttError as err:
        print(f"FAIL [{err.kind.value}] {err}", file=sys.stderr)
        return 1
    except Exception as err:  # noqa: BLE001
        print(f"FAIL unexpected: {err}", file=sys.stderr)
        return 1

    deadline = time.time() + 3.0
    state = client.get_state()
    while time.time() < deadline:
        state = client.get_state()
        if state.battery_percent is not None or state.phase is not None:
            break
        time.sleep(0.2)
    client.disconnect()

    print(
        "OK connected "
        f"battery={state.battery_percent} phase={state.phase} cycle={state.cycle}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
