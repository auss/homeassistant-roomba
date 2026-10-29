"""Vacuum entity for Roomba Local mission control."""

from __future__ import annotations

from homeassistant.components.vacuum import StateVacuumEntity, VacuumEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_BLID, DOMAIN
from .coordinator import RoombaDataUpdateCoordinator
from .roomba_mqtt import map_activity

try:
    from homeassistant.components.vacuum import VacuumActivity
except ImportError:  # pragma: no cover - older cores
    VacuumActivity = None  # type: ignore[misc, assignment]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the vacuum entity."""
    coordinator: RoombaDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([RoombaVacuumEntity(coordinator, entry)])


class RoombaVacuumEntity(CoordinatorEntity[RoombaDataUpdateCoordinator], StateVacuumEntity):
    """Roomba vacuum controlled over local MQTT."""

    _attr_has_entity_name = True
    _attr_name = None
    _attr_supported_features = (
        VacuumEntityFeature.STATE
        | VacuumEntityFeature.START
        | VacuumEntityFeature.PAUSE
        | VacuumEntityFeature.STOP
        | VacuumEntityFeature.RETURN_HOME
        | VacuumEntityFeature.LOCATE
    )

    def __init__(
        self, coordinator: RoombaDataUpdateCoordinator, entry: ConfigEntry
    ) -> None:
        super().__init__(coordinator)
        self._blid: str = entry.data[CONF_BLID]
        self._attr_unique_id = f"{self._blid}_vacuum"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, self._blid)},
            name=entry.title,
            manufacturer="iRobot",
            model=coordinator.data.sku or "Roomba",
        )

    @property
    def available(self) -> bool:
        """Return True when MQTT reports connected."""
        return bool(self.coordinator.data.connected)

    @property
    def battery_level(self) -> int | None:
        """Return battery percentage when known."""
        return self.coordinator.data.battery_percent

    @callback
    def _handle_coordinator_update(self) -> None:
        activity = map_activity(self.coordinator.data)
        if VacuumActivity is not None:
            mapping = {
                "cleaning": VacuumActivity.CLEANING,
                "docked": VacuumActivity.DOCKED,
                "paused": VacuumActivity.PAUSED,
                "returning": VacuumActivity.RETURNING,
                "error": VacuumActivity.ERROR,
                "idle": VacuumActivity.IDLE,
            }
            self._attr_activity = mapping.get(activity, VacuumActivity.IDLE)
        else:
            self._attr_state = activity
        super()._handle_coordinator_update()

    async def async_added_to_hass(self) -> None:
        """Prime activity from current coordinator data."""
        await super().async_added_to_hass()
        self._handle_coordinator_update()

    async def async_start(self) -> None:
        """Start cleaning, or resume if paused."""
        activity = map_activity(self.coordinator.data)
        command = "resume" if activity == "paused" else "start"
        await self.coordinator.async_send_command(command)

    async def async_pause(self) -> None:
        """Pause the current mission."""
        await self.coordinator.async_send_command("pause")

    async def async_stop(self, **kwargs) -> None:
        """Stop the current mission without docking."""
        await self.coordinator.async_send_command("stop")

    async def async_return_to_base(self, **kwargs) -> None:
        """Send the robot back to the dock."""
        await self.coordinator.async_send_command("dock")

    async def async_locate(self, **kwargs) -> None:
        """Make the robot emit a locate beep."""
        await self.coordinator.async_send_command("find")
