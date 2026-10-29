"""Constants for the Roomba Local integration."""

from __future__ import annotations

DOMAIN = "roomba_local"

CONF_BLID = "blid"
CONF_PASSWORD = "password"
CONF_HOST = "host"

DEFAULT_PORT = 8883
DEFAULT_NAME = "Roomba"

# Local MQTT TLS: MQTT 3.1.1, legacy cipher required by robot firmware.
MQTT_PROTOCOL = 4  # MQTTv311
TLS_CIPHERS = "AES128-SHA256:@SECLEVEL=0"
MQTT_CMD_TOPIC = "cmd"
MQTT_KEEPALIVE = 60

ATTR_PHASE = "phase"
ATTR_CYCLE = "cycle"
ATTR_ERROR = "error"
ATTR_BIN_FULL = "bin_full"
ATTR_LOCATION = "location"
ATTR_POSE_X = "pose_x"
ATTR_POSE_Y = "pose_y"
ATTR_POSE_THETA = "pose_theta"
