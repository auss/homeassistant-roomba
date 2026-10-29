"""Select entities for clean mode / vacuum power / mop intensity."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .clean_options import (
    DEFAULT_MODE,
    DEFAULT_MOP,
    DEFAULT_POWER,
    MODES,
    MOP_LEVELS,
    POWERS,
)
from .const import (
    CONF_BLID,
    DOMAIN,
    OPT_CLEAN_MODE,
    OPT_MOP_INTENSITY,
    OPT_VACUUM_POWER,
)
from .coordinator import RoombaDataUpdateCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up clean-option selects."""
    coordinator: RoombaDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            RoombaCleanModeSelect(coordinator, entry),
            RoombaVacuumPowerSelect(coordinator, entry),
            RoombaMopIntensitySelect(coordinator, entry),
        ]
    )


class _OptionsSelect(
    CoordinatorEntity[RoombaDataUpdateCoordinator], SelectEntity
):
    """Select backed by config entry options (no cloud secrets)."""

    _attr_has_entity_name = True
    _option_key: str
    _choices: tuple[str, ...]
    _default: str

    def __init__(
        self, coordinator: RoombaDataUpdateCoordinator, entry: ConfigEntry
    ) -> None:
        super().__init__(coordinator)
        self._entry = entry
        blid = entry.data[CONF_BLID]
        self._attr_unique_id = f"{blid}_{self._option_key}"
        self._attr_options = list(self._choices)
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, blid)},
            name=entry.title,
            manufacturer="iRobot",
            model=coordinator.data.sku or "Roomba",
        )

    @property
    def current_option(self) -> str | None:
        """Return the currently selected option."""
        value = self._entry.options.get(self._option_key, self._default)
        return value if value in self._choices else self._default

    async def async_select_option(self, option: str) -> None:
        """Persist selection into entry options."""
        if option not in self._choices:
            return
        new_options = {**dict(self._entry.options), self._option_key: option}
        self.hass.config_entries.async_update_entry(self._entry, options=new_options)
        self.async_write_ha_state()


class RoombaCleanModeSelect(_OptionsSelect):
    """Vacuum vs vacuum+mop."""

    _attr_translation_key = "clean_mode"
    _option_key = OPT_CLEAN_MODE
    _choices = MODES
    _default = DEFAULT_MODE


class RoombaVacuumPowerSelect(_OptionsSelect):
    """Suction level."""

    _attr_translation_key = "vacuum_power"
    _option_key = OPT_VACUUM_POWER
    _choices = POWERS
    _default = DEFAULT_POWER


class RoombaMopIntensitySelect(_OptionsSelect):
    """Pad wetness when mopping."""

    _attr_translation_key = "mop_intensity"
    _option_key = OPT_MOP_INTENSITY
    _choices = MOP_LEVELS
    _default = DEFAULT_MOP
