"""Image entity showing the last on-demand fetched Roomba map."""

from __future__ import annotations

from homeassistant.components.image import ImageEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import CONF_BLID, DOMAIN, OPT_LAST_MAP_FETCHED_AT
from .coordinator import RoombaDataUpdateCoordinator
from .map_store import load_map_meta, load_map_png


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the map image entity."""
    coordinator: RoombaDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([RoombaMapImageEntity(hass, coordinator, entry)])


class RoombaMapImageEntity(
    CoordinatorEntity[RoombaDataUpdateCoordinator], ImageEntity
):
    """Serves the last map PNG fetched via ephemeral cloud login."""

    _attr_has_entity_name = True
    _attr_translation_key = "map"
    _attr_content_type = "image/png"

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: RoombaDataUpdateCoordinator,
        entry: ConfigEntry,
    ) -> None:
        super().__init__(coordinator)
        ImageEntity.__init__(self, hass)
        self._entry = entry
        self._blid: str = entry.data[CONF_BLID]
        self._attr_unique_id = f"{self._blid}_map"
        self._image_bytes: bytes | None = None
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, self._blid)},
            name=entry.title,
            manufacturer="iRobot",
            model=coordinator.data.sku or "Roomba",
        )
        self._reload_from_disk()

    def _reload_from_disk(self) -> None:
        self._image_bytes = load_map_png(self.hass, self._blid)
        meta = load_map_meta(self.hass, self._blid) or {}
        fetched = meta.get("fetched_at") or self._entry.options.get(
            OPT_LAST_MAP_FETCHED_AT
        )
        if fetched:
            parsed = dt_util.parse_datetime(str(fetched))
            self._attr_image_last_updated = parsed or dt_util.utcnow()
        elif self._image_bytes:
            self._attr_image_last_updated = dt_util.utcnow()
        else:
            self._attr_image_last_updated = None

    @callback
    def refresh_from_storage(self) -> None:
        """Reload PNG after a successful on-demand fetch."""
        self._reload_from_disk()
        self.async_write_ha_state()

    async def async_added_to_hass(self) -> None:
        """Register for map-updated events."""
        await super().async_added_to_hass()
        self.async_on_remove(
            self.hass.bus.async_listen(
                f"{DOMAIN}_map_updated", self._handle_map_updated
            )
        )

    @callback
    def _handle_map_updated(self, event) -> None:
        if event.data.get("blid", "").lower() != self._blid.lower():
            return
        self.refresh_from_storage()

    async def async_image(self) -> bytes | None:
        """Return bytes of the last fetched map image."""
        if self._image_bytes is None:
            self._reload_from_disk()
        return self._image_bytes

    @property
    def available(self) -> bool:
        """Available when a map has been fetched at least once."""
        return self._image_bytes is not None

    @property
    def extra_state_attributes(self) -> dict[str, str | None]:
        """Expose non-secret map metadata."""
        meta = load_map_meta(self.hass, self._blid) or {}
        return {
            "pmap_id": meta.get("pmap_id"),
            "pmapv_id": meta.get("pmapv_id"),
            "fetched_at": meta.get("fetched_at"),
        }
