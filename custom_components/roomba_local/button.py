"""Button entities for Roomba Local."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .clean_options import (
    DEFAULT_MODE,
    DEFAULT_MOP,
    DEFAULT_POWER,
    build_region_params,
)
from .const import (
    CONF_BLID,
    DOMAIN,
    OPT_CLEAN_MODE,
    OPT_CLEAN_ROOM_ORDER,
    OPT_LAST_PMAP_ID,
    OPT_LAST_PMAPV_ID,
    OPT_MOP_INTENSITY,
    OPT_VACUUM_POWER,
)
from .coordinator import RoombaDataUpdateCoordinator
from .map_store import load_map_meta, load_rooms, resolve_room_names
from .roomba_mqtt import build_region_start_payload


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Roomba buttons."""
    coordinator: RoombaDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            RoombaFindButton(coordinator, entry),
            RoombaStartRoomCleanButton(coordinator, entry),
        ]
    )


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


class RoombaStartRoomCleanButton(
    CoordinatorEntity[RoombaDataUpdateCoordinator], ButtonEntity
):
    """Start selective clean using saved room order + mode/power/mop selects."""

    _attr_has_entity_name = True
    _attr_translation_key = "start_room_clean"

    def __init__(
        self, coordinator: RoombaDataUpdateCoordinator, entry: ConfigEntry
    ) -> None:
        super().__init__(coordinator)
        self._entry = entry
        blid = entry.data[CONF_BLID]
        self._attr_unique_id = f"{blid}_start_room_clean"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, blid)},
            name=entry.title,
            manufacturer="iRobot",
            model=coordinator.data.sku or "Roomba",
        )

    @property
    def available(self) -> bool:
        """Available when MQTT is up and a room plan exists."""
        order = self._entry.options.get(OPT_CLEAN_ROOM_ORDER) or []
        return bool(self.coordinator.data.connected) and bool(order)

    async def async_press(self) -> None:
        """Publish selective start from options + select entities."""
        blid = self._entry.data[CONF_BLID]
        order = list(self._entry.options.get(OPT_CLEAN_ROOM_ORDER) or [])
        if not order:
            raise HomeAssistantError(
                "No room clean plan. Configure → Set room clean plan "
                "(or call roomba_local.set_room_order)."
            )

        rooms = await self.hass.async_add_executor_job(load_rooms, self.hass, blid)
        meta = await self.hass.async_add_executor_job(load_map_meta, self.hass, blid)
        pmap_id = (meta or {}).get("pmap_id") or self._entry.options.get(
            OPT_LAST_PMAP_ID
        )
        pmapv_id = (meta or {}).get("pmapv_id") or self._entry.options.get(
            OPT_LAST_PMAPV_ID
        )
        if not rooms or not pmap_id:
            raise HomeAssistantError(
                "No room map yet. Fetch the cloud map first, then set a room plan."
            )

        try:
            matched = resolve_room_names(rooms, order)
        except KeyError as err:
            raise HomeAssistantError(str(err)) from err

        params = build_region_params(
            mode=str(self._entry.options.get(OPT_CLEAN_MODE, DEFAULT_MODE)),
            vacuum_power=str(
                self._entry.options.get(OPT_VACUUM_POWER, DEFAULT_POWER)
            ),
            mop_intensity=str(
                self._entry.options.get(OPT_MOP_INTENSITY, DEFAULT_MOP)
            ),
        )
        payload = build_region_start_payload(
            pmap_id=str(pmap_id),
            regions=matched,
            user_pmapv_id=str(pmapv_id) if pmapv_id else None,
            region_params=params,
        )
        await self.coordinator.async_publish_cmd(payload)
