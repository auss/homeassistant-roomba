"""DataUpdateCoordinator wrapping the local Roomba MQTT client."""

from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import CONF_BLID, CONF_HOST, CONF_PASSWORD, DOMAIN
from .roomba_mqtt import RoombaMqttClient, RoombaMqttError, RoombaState

_LOGGER = logging.getLogger(__name__)

# Retry MQTT connect while entities stay unavailable (container/net flaps).
RECONNECT_INTERVAL = timedelta(seconds=60)


class RoombaDataUpdateCoordinator(DataUpdateCoordinator[RoombaState]):
    """Coordinator that pushes MQTT state into Home Assistant entities."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            config_entry=entry,
            update_interval=None,
        )
        self.entry = entry
        self.client: RoombaMqttClient | None = None
        self.last_connect_error: str | None = None

    async def async_config_entry_first_refresh(self) -> None:
        """Try MQTT connect; soft-fail so the entry loads even if refused."""
        await self._async_try_connect(raise_on_failure=False)
        # Do not raise ConfigEntryNotReady — keep the entry loaded and retry.
        self.async_set_updated_data(
            self.client.get_state() if self.client else RoombaState()
        )
        self.last_update_success = True

    async def _async_try_connect(self, *, raise_on_failure: bool) -> bool:
        data = self.entry.data
        # Robot MQTT accepts only one LAN client. Core Roomba (roombapy) steals it.
        if "roomba" in getattr(self.hass.config, "components", set()):
            _LOGGER.error(
                "Core Home Assistant Roomba integration is loaded. The robot allows "
                "only ONE local MQTT connection — disable Core Roomba before using "
                "roomba_local, or both will fight for port 8883."
            )
        client = RoombaMqttClient(
            blid=data[CONF_BLID],
            password=data[CONF_PASSWORD],
            host=data.get(CONF_HOST) or None,
            on_state=self._on_mqtt_state,
        )
        try:
            await self.hass.async_add_executor_job(client.connect)
        except RoombaMqttError as err:
            self.last_connect_error = str(err)
            _LOGGER.warning(
                "Roomba MQTT connect soft-failed (%s): %s — will retry every %s",
                err.kind.value,
                err,
                RECONNECT_INTERVAL,
            )
            await self.hass.async_add_executor_job(client.disconnect)
            self.client = None
            self.update_interval = RECONNECT_INTERVAL
            if raise_on_failure:
                raise
            self.async_set_updated_data(RoombaState())
            return False
        except Exception:
            self.last_connect_error = "unexpected connect failure"
            _LOGGER.exception(
                "Roomba MQTT connect soft-failed unexpectedly — will retry every %s",
                RECONNECT_INTERVAL,
            )
            await self.hass.async_add_executor_job(client.disconnect)
            self.client = None
            self.update_interval = RECONNECT_INTERVAL
            if raise_on_failure:
                raise
            self.async_set_updated_data(RoombaState())
            return False

        self.client = client
        self.last_connect_error = None
        self.update_interval = None
        self.async_set_updated_data(client.get_state())
        return True

    @callback
    def _on_mqtt_state(self, state: RoombaState) -> None:
        """MQTT thread callback → schedule HA update on the event loop."""
        self.hass.loop.call_soon_threadsafe(self.async_set_updated_data, state)

    async def _async_update_data(self) -> RoombaState:
        if self.client is None:
            await self._async_try_connect(raise_on_failure=False)
            if self.client is None:
                return RoombaState()
        return await self.hass.async_add_executor_job(self.client.get_state)

    async def async_send_command(self, command: str) -> None:
        """Send a mission command via the MQTT client."""
        if self.client is None:
            connected = await self._async_try_connect(raise_on_failure=False)
            if not connected or self.client is None:
                raise ConnectionError(
                    self.last_connect_error or "Roomba MQTT client is not connected"
                )
        await self.hass.async_add_executor_job(self.client.send_command, command)

    async def async_shutdown(self) -> None:
        """Disconnect MQTT and shut down the coordinator."""
        client = self.client
        self.client = None
        if client is not None:
            await self.hass.async_add_executor_job(client.disconnect)
        await super().async_shutdown()
