"""Constants for the Roomba Local integration."""

from __future__ import annotations

DOMAIN = "roomba_local"

CONF_BLID = "blid"
CONF_PASSWORD = "password"
CONF_HOST = "host"
CONF_EMAIL = "email"
CONF_COUNTRY = "country"

DEFAULT_PORT = 8883
DEFAULT_NAME = "Roomba"
DEFAULT_COUNTRY = "US"

# Local MQTT TLS: MQTT 3.1.1, legacy cipher required by robot firmware.
MQTT_PROTOCOL = 4  # MQTTv311
# Proven OpenSSL 3 cipher candidates (order matters).
TLS_CIPHER_CANDIDATES = (
    "AES128-SHA256:@SECLEVEL=0",
    "AES128-SHA256",
    "DEFAULT:@SECLEVEL=0",
)
TLS_CIPHERS = TLS_CIPHER_CANDIDATES[0]
# SSL_OP_LEGACY_SERVER_CONNECT
TLS_OP_LEGACY_SERVER_CONNECT = 0x4
MQTT_CMD_TOPIC = "cmd"
MQTT_KEEPALIVE = 60

# Fixed mobile app id used for one-shot cloud login (map fetch only).
CLOUD_APP_ID = "ANDROID-A1B2C3D4-E5F6-7890-ABCD-EF1234567890"

# Options / service metadata keys (never credentials).
OPT_LAST_MAP_FETCHED_AT = "last_map_fetched_at"
OPT_LAST_PMAP_ID = "last_pmap_id"
OPT_LAST_PMAPV_ID = "last_pmapv_id"
OPT_CLEAN_ROOM_ORDER = "clean_room_order"
OPT_CLEAN_MODE = "clean_mode"
OPT_VACUUM_POWER = "vacuum_power"
OPT_MOP_INTENSITY = "mop_intensity"

SERVICE_FETCH_MAP = "fetch_map"
SERVICE_CLEAN_ROOMS = "clean_rooms"
SERVICE_SET_ROOM_ORDER = "set_room_order"

ATTR_PHASE = "phase"
ATTR_CYCLE = "cycle"
ATTR_ERROR = "error"
ATTR_BIN_FULL = "bin_full"
ATTR_LOCATION = "location"
ATTR_POSE_X = "pose_x"
ATTR_POSE_Y = "pose_y"
ATTR_POSE_THETA = "pose_theta"
