#!/usr/bin/env python3
"""Smoke test for MQTT helpers without Home Assistant installed."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "custom_components" / "roomba_local"


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def main() -> None:
    const = load("roomba_local_const", PKG / "const.py")
    sys.modules["custom_components.roomba_local.const"] = const
    # Provide a fake parent package for relative imports
    import types

    parent = types.ModuleType("custom_components.roomba_local")
    parent.__path__ = [str(PKG)]
    parent.const = const
    sys.modules.setdefault("custom_components", types.ModuleType("custom_components"))
    sys.modules["custom_components.roomba_local"] = parent

    mqtt = load("custom_components.roomba_local.roomba_mqtt", PKG / "roomba_mqtt.py")
    payload = mqtt.build_command_payload("find")
    assert payload["command"] == "find"
    state = mqtt.RoombaState(phase="run", cycle="clean", error=0)
    assert mqtt.map_activity(state) == "cleaning"
    mqtt.build_ssl_context()
    print("smoke_test ok")


if __name__ == "__main__":
    main()
