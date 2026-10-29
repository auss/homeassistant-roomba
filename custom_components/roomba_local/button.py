"""Button entities for Roomba Local."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_BLID, DOMAIN
from .coordinator import RoombaDataUpdateCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Roomba buttons."""
    coordinator: RoombaDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([RoombaFindButton(coordinator, entry)])


class RoombaFindButton(CoordinatorEntity[RoombaDataUpdateCoordinator], ButtonEntity):
    """Trigger the robot locate (find) beep."""

    _attr_has_entity_name = True
    _attr_translation_key = "find"

    def __init__(
        self, coordinator: RoombaDataUpdateCoordinator, entry: ConfigEntry
    ) -> None:
        super().__init__(coordinator)
        blid = entry.data[CONF_BLID]
        self._attr_unique_id = f"{blid}_find"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, blid)},
            name=entry.title,
            manufacturer="iRobot",
            model=coordinator.data.sku or "Roomba",
        )

    @property
    def available(self) -> bool:
        """Return True when MQTT reports connected."""
        return bool(self.coordinator.data.connected)

    async def async_press(self) -> None:
        """Send the find command."""
        await self.coordinator.async_send_command("find")
