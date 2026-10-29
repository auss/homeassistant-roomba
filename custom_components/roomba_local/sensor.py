"""Sensors for Roomba Local telemetry."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_BLID, DOMAIN
from .coordinator import RoombaDataUpdateCoordinator
from .roomba_mqtt import RoombaState, map_activity


@dataclass(frozen=True, kw_only=True)
class RoombaSensorEntityDescription(SensorEntityDescription):
    """Sensor description with a value extractor."""

    value_fn: Callable[[RoombaState], str | int | float | None]


SENSORS: tuple[RoombaSensorEntityDescription, ...] = (
    RoombaSensorEntityDescription(
        key="battery",
        translation_key="battery",
        native_unit_of_measurement=PERCENTAGE,
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda s: s.battery_percent,
    ),
    RoombaSensorEntityDescription(
        key="status",
        translation_key="status",
        value_fn=lambda s: map_activity(s),
    ),
    RoombaSensorEntityDescription(
        key="phase",
        translation_key="phase",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda s: s.phase,
    ),
    RoombaSensorEntityDescription(
        key="location",
        translation_key="location",
        value_fn=lambda s: s.location_summary,
    ),
    RoombaSensorEntityDescription(
        key="bin_full",
        translation_key="bin_full",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda s: None if s.bin_full is None else ("on" if s.bin_full else "off"),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Roomba sensors."""
    coordinator: RoombaDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        RoombaSensorEntity(coordinator, entry, description) for description in SENSORS
    )


class RoombaSensorEntity(
    CoordinatorEntity[RoombaDataUpdateCoordinator], SensorEntity
):
    """Sensor backed by Roomba MQTT reported state."""

    _attr_has_entity_name = True
    entity_description: RoombaSensorEntityDescription

    def __init__(
        self,
        coordinator: RoombaDataUpdateCoordinator,
        entry: ConfigEntry,
        description: RoombaSensorEntityDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        blid = entry.data[CONF_BLID]
        self._attr_unique_id = f"{blid}_{description.key}"
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

    @property
    def native_value(self) -> str | int | float | None:
        """Return the sensor value."""
        return self.entity_description.value_fn(self.coordinator.data)
