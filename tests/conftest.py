"""Pytest path setup that loads MQTT helpers without importing Home Assistant."""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PKG_DIR = ROOT / "custom_components" / "roomba_local"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _ensure_namespace(name: str, path: Path) -> types.ModuleType:
    mod = sys.modules.get(name)
    if mod is None:
        mod = types.ModuleType(name)
        mod.__path__ = [str(path)]  # type: ignore[attr-defined]
        sys.modules[name] = mod
    return mod


def _load_file(modname: str, file_path: Path) -> types.ModuleType:
    if modname in sys.modules:
        return sys.modules[modname]
    spec = importlib.util.spec_from_file_location(modname, file_path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[modname] = mod
    spec.loader.exec_module(mod)
    return mod


_ensure_namespace("custom_components", ROOT / "custom_components")
pkg = _ensure_namespace("custom_components.roomba_local", PKG_DIR)
const = _load_file("custom_components.roomba_local.const", PKG_DIR / "const.py")
mqtt = _load_file(
    "custom_components.roomba_local.roomba_mqtt", PKG_DIR / "roomba_mqtt.py"
)
pkg.const = const  # type: ignore[attr-defined]
pkg.roomba_mqtt = mqtt  # type: ignore[attr-defined]
