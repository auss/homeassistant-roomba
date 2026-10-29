# Roomba Local (Home Assistant)

Home Assistant custom integration for iRobot Roomba robots on your LAN. It connects
over local MQTT (TLS port 8883) to show status, battery, and location telemetry, and
to control cleaning missions.

Supports Roomba Combo j9+ and the same local MQTT / LSS family.

## Features (Phase 1 + on-demand maps)

- **Config flow**: BLID, local MQTT password, optional host/IP
- **Vacuum entity**: start / pause / resume / stop / return to dock / locate
- **Sensors**: battery, status, mission phase, location/pose summary, bin full
- **Find button**: locate beep
- **Map image**: on-demand cloud fetch via Configure → *Fetch cloud map* (or
  `roomba_local.fetch_map`). iRobot account credentials are used for that run
  only and are **not** stored in the config entry.
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

Prefer the robot **LAN IP** if mDNS from the Home Assistant host is unreliable.
Credentials are stored in Home Assistant’s encrypted config entries. Never commit
BLID or passwords to git.

### Connection troubleshooting

Config flow errors are classified:

| Error | Meaning |
| --- | --- |
| DNS / mDNS | Hostname lookup failed — use LAN IP |
| Unreachable | TCP `:8883` not reachable from the HA host (VLAN / container / firewall) |
| TLS | Robot reached, handshake failed (legacy OpenSSL cipher / renegotiation) |
| Auth | BLID / local password rejected after TLS |

If Python scripts on another VM on the same Proxmox host work but HA does not,
verify the **Home Assistant OS / container** can route to the robot VLAN (not
only that a sibling VM can). That is a networking issue, not bad credentials.

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

## On-demand map fetch

Maps are refreshed only when you ask:

1. **Configure → Fetch cloud map** and enter iRobot email/password (and optional
   country code), or call `roomba_local.fetch_map`.
2. The integration downloads the active persistent map and updates the **Map**
   image entity (`config/roomba_local/<blid>/map.png`).
3. Account password and cloud tokens are discarded immediately after the fetch.

There is no persistent cloud login. Always-on remote cloud MQTT remains out of scope.

## Unsupported

Remote cloud IoT MQTT for live status when off-LAN is not implemented.
