"""Map HA clean selects → local MQTT region params (Combo j9+ family)."""

from __future__ import annotations

from typing import Any

# Combo / LSS selective-clean conventions (from observed app lastCommand shapes).
MODE_VACUUM = "vacuum"
MODE_VACUUM_AND_MOP = "vacuum_and_mop"
MODES = (MODE_VACUUM, MODE_VACUUM_AND_MOP)

POWER_LOW = "low"
POWER_MEDIUM = "medium"
POWER_HIGH = "high"
POWER_MAX = "max"
POWERS = (POWER_LOW, POWER_MEDIUM, POWER_HIGH, POWER_MAX)

MOP_LOW = "low"
MOP_MEDIUM = "medium"
MOP_HIGH = "high"
MOP_LEVELS = (MOP_LOW, MOP_MEDIUM, MOP_HIGH)

# operatingMode: 2 = vacuum only, 6 = vacuum + mop
_OPERATING_MODE = {
    MODE_VACUUM: 2,
    MODE_VACUUM_AND_MOP: 6,
}

# vacQty: 1..4 ≈ low..max suction
_VAC_QTY = {
    POWER_LOW: 1,
    POWER_MEDIUM: 2,
    POWER_HIGH: 3,
    POWER_MAX: 4,
}

# padWetness disposable/reusable: 1..3
_PAD_WETNESS = {
    MOP_LOW: 1,
    MOP_MEDIUM: 2,
    MOP_HIGH: 3,
}

DEFAULT_MODE = MODE_VACUUM
DEFAULT_POWER = POWER_MEDIUM
DEFAULT_MOP = MOP_MEDIUM


def build_region_params(
    *,
    mode: str = DEFAULT_MODE,
    vacuum_power: str = DEFAULT_POWER,
    mop_intensity: str = DEFAULT_MOP,
) -> dict[str, Any]:
    """Build per-region ``params`` for a selective start command."""
    mode_key = mode if mode in _OPERATING_MODE else DEFAULT_MODE
    power_key = vacuum_power if vacuum_power in _VAC_QTY else DEFAULT_POWER
    mop_key = mop_intensity if mop_intensity in _PAD_WETNESS else DEFAULT_MOP

    params: dict[str, Any] = {
        "twoPass": False,
        "noAutoPasses": True,
        "operatingMode": _OPERATING_MODE[mode_key],
        "vacQty": _VAC_QTY[power_key],
    }
    if mode_key == MODE_VACUUM_AND_MOP:
        wet = _PAD_WETNESS[mop_key]
        params["padWetness"] = {"disposable": wet, "reusable": wet}
        params["swScrub"] = 0
    return params


def parse_room_order_text(raw: str) -> list[str]:
    """Split comma/newline ordered room names; drop empties."""
    parts: list[str] = []
    for chunk in raw.replace("\n", ",").split(","):
        name = chunk.strip()
        if name:
            parts.append(name)
    return parts
