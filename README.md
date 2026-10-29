# Roomba Local (Home Assistant)

Home Assistant custom integration for iRobot Roomba robots on your LAN. It connects
over local MQTT (TLS port 8883) to show status, battery, and location telemetry, and
to control cleaning missions.

Supports Roomba Combo j9+ and the same local MQTT / LSS family.

## Features

- **Config flow**: BLID, local MQTT password, optional host/IP
- **Vacuum entity**: start / pause / resume / stop / return to dock / locate
- **Clean rooms**: ordered plan + Clean mode / Vacuum power / Mop intensity selects
  + **Start room clean** button (also `roomba_local.clean_rooms`)
- **Sensors**: battery, status, mission phase, location/pose summary, bin full
- **Find button**: locate beep
- **Map image + rooms**: on-demand cloud fetch via Configure → *Fetch cloud map*
  (or `roomba_local.fetch_map`). Saves PNG, `pmap_id`, and `rooms.json`. Account
  credentials are used for that run only and are **not** stored.
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
| Host | no | Prefer robot **LAN IP**. Leave blank only if mDNS works from HA (`irobot-<blid>.local`) |

Leave Host blank only when mDNS resolves from the Home Assistant host; otherwise set
the explicit IP. Failed setup errors show the **exact host:port** that was dialed.

### LAN MQTT smoke — run it **inside** the HA container

The robot’s MQTT broker on `:8883` accepts **only one** local client. If Core
Roomba (`roombapy`), the iRobot app (LAN), or `roomba-control.py` holds the slot,
`roomba_local` gets `ECONNREFUSED` even when `nc -vz IP 8883` says **open**.

**If `nc` is open inside the Home Assistant container, networking is not the
problem.** Run the smoke script in that same container — it uses the same
`RoombaMqttClient` as the integration (forced IPv4 TCP, `create_default_context`,
cipher loop, `OP_LEGACY_SERVER_CONNECT`, paho VERSION1, `tls_insecure_set`).

```bash
# Inside HA container (docker exec -it homeassistant bash / ha core enter):
nc -vz 192.168.x.x 8883
pip install 'paho-mqtt>=2.1.0,<3'
python3 scripts/lan_mqtt_smoke.py --blid YOUR_BLID --password YOUR_PASSWORD \
  --host 192.168.x.x
```

Also compare host SSH only to rule out VLAN/firewall mistakes:

| Inside HA `nc` | Inside HA smoke | Likely cause |
| --- | --- | --- |
| open | OK | Integration/config — check Host matches the IP you tested |
| open | refused / fail | Single-client slot held, or credentials/TLS — disable Core Roomba |
| refused | refused | Container cannot reach robot VLAN |

Disable **Core Roomba** before using this integration. Credentials stay in HA’s
encrypted config entries — never commit BLID or passwords.

### Not the built-in “Roomba” integration

Home Assistant Core also ships a separate **Roomba** integration that uses
`roombapy` (often MQTT attempts that show up as `roombapy.remote_client` /
`ConnectionRefusedError` in logs). **Roomba Local** (`roomba_local`) does **not**
use `roombapy`. It connects with its own paho-mqtt client over **TLS port 8883**
and legacy OpenSSL ciphers. If you see `roombapy` in the traceback, that failure
is from Core’s Roomba integration (or another custom component), not this one.

Pillow is **not** pinned in the integration manifest — Home Assistant Core already
provides it. Map rendering imports that shared package.

### Connection troubleshooting

Config flow errors are classified:

| Error | Meaning |
| --- | --- |
| DNS / mDNS | Hostname lookup failed — use LAN IP |
| Unreachable / refused | TCP `:8883` refused — often another client holds the single MQTT slot |
| TLS | Robot reached, handshake failed (legacy OpenSSL cipher / renegotiation) |
| Auth | BLID / local password rejected after TLS |

If `nc` works **inside** the HA container but the integration does not, run
`lan_mqtt_smoke.py` there before blaming Docker networking. Confirm the config
entry Host is the same IP you probed.

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
2. The integration downloads the active persistent map, updates the **Map**
   image entity, and saves `rooms.json` for selective clean.
3. Call `roomba_local.clean_rooms` with `rooms: ["Kitchen"]` (names from that map).
4. Account password and cloud tokens are discarded immediately after the fetch.

There is no persistent cloud login. Always-on remote cloud MQTT remains out of scope.

## Unsupported

Remote cloud IoT MQTT for live status when off-LAN is not implemented.
