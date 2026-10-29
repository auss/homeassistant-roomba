"""DataUpdateCoordinator wrapping the local Roomba MQTT client."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import CONF_BLID, CONF_HOST, CONF_PASSWORD, DOMAIN
from .roomba_mqtt import RoombaMqttClient, RoombaState

_LOGGER = logging.getLogger(__name__)


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

    async def async_config_entry_first_refresh(self) -> None:
        """Connect MQTT before entities are set up."""
        await self._async_connect()
        await super().async_config_entry_first_refresh()

    async def _async_connect(self) -> None:
        data = self.entry.data
        client = RoombaMqttClient(
            blid=data[CONF_BLID],
            password=data[CONF_PASSWORD],
            host=data.get(CONF_HOST) or None,
            on_state=self._on_mqtt_state,
        )
        await self.hass.async_add_executor_job(client.connect)
        self.client = client
        self.async_set_updated_data(client.get_state())

    @callback
    def _on_mqtt_state(self, state: RoombaState) -> None:
        """MQTT thread callback → schedule HA update on the event loop."""
        self.hass.loop.call_soon_threadsafe(self.async_set_updated_data, state)

    async def _async_update_data(self) -> RoombaState:
        if self.client is None:
            return RoombaState()
        return await self.hass.async_add_executor_job(self.client.get_state)

    async def async_send_command(self, command: str) -> None:
        """Send a mission command via the MQTT client."""
        if self.client is None:
            raise ConnectionError("Roomba MQTT client is not connected")
        await self.hass.async_add_executor_job(self.client.send_command, command)

    async def async_shutdown(self) -> None:
        """Disconnect MQTT and shut down the coordinator."""
        client = self.client
        self.client = None
        if client is not None:
            await self.hass.async_add_executor_job(client.disconnect)
        await super().async_shutdown()
