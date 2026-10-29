# Roomba Local (Home Assistant)

Home Assistant custom integration for iRobot Roomba robots on your LAN. It connects
over local MQTT (TLS port 8883) to show status, battery, and location telemetry, and
to control cleaning missions.

Supports Roomba Combo j9+ and the same local MQTT / LSS family.

## Features (Phase 1)

- **Config flow**: BLID, local MQTT password, optional host/IP
- **Vacuum entity**: start / pause / resume / stop / return to dock / locate
- **Sensors**: battery, status, mission phase, location/pose summary, bin full
- **Find button**: locate beep
- Transport: MQTT 3.1.1 over TLS with the robot’s required legacy cipher

Manual drive / joystick control is **not supported** — the robot rejects local drive
commands. Do not expect velocity RC from this integration.

## Installation

### HACS (recommended)

1. Add this repository as a custom repository (Integration).
2. Install **Roomba Local**.
3. Restart Home Assistant.
4. Settings → Devices & services → Add integration → **Roomba Local**.

### Manual

Copy `custom_components/roomba_local` into your Home Assistant
`config/custom_components/` directory, restart, then add the integration from the UI.

## Configuration

| Field | Required | Notes |
| --- | --- | --- |
| BLID | yes | Robot identifier; MQTT username |
| Password | yes | Local MQTT password |
| Host | no | IP or hostname; defaults to `iRobot-<BLID>.local` via mDNS |

Credentials are stored in Home Assistant’s encrypted config entries. Never commit
BLID or passwords to git.

## Development

Requires Python 3.12+.

```bash
python3 -m pip install -r requirements_test.txt
python3 -m pip install -e .
ruff check custom_components tests
pytest
```

Unit tests cover MQTT payload shaping, TLS context setup, and reported-state mapping.
A live robot is not required for CI.

## Cloud / maps (later)

Remote cloud IoT MQTT and map image fetch are planned for a later phase. Phase 1 is
LAN-only.
