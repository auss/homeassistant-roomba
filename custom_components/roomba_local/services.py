"""Service handlers for Roomba Local."""

from __future__ import annotations

import logging

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv

from .const import (
    CONF_BLID,
    CONF_COUNTRY,
    CONF_EMAIL,
    DEFAULT_COUNTRY,
    DOMAIN,
    OPT_LAST_MAP_FETCHED_AT,
    OPT_LAST_PMAP_ID,
    OPT_LAST_PMAPV_ID,
    SERVICE_FETCH_MAP,
)
from .map_cloud import MapFetchError, fetch_map_image
from .map_store import assert_no_secrets_in_entry_data, save_map_result

_LOGGER = logging.getLogger(__name__)

SERVICE_FETCH_MAP_SCHEMA = vol.Schema(
    {
        vol.Optional("entry_id"): cv.string,
        vol.Required(CONF_EMAIL): cv.string,
        vol.Required(CONF_PASSWORD): cv.string,
        vol.Optional(CONF_COUNTRY, default=DEFAULT_COUNTRY): cv.string,
    }
)


def _resolve_entry(hass: HomeAssistant, entry_id: str | None) -> ConfigEntry:
    entries = list(hass.config_entries.async_entries(DOMAIN))
    if entry_id:
        for entry in entries:
            if entry.entry_id == entry_id:
                return entry
        raise HomeAssistantError(f"Unknown config entry id: {entry_id}")
    if len(entries) == 1:
        return entries[0]
    if not entries:
        raise HomeAssistantError("No Roomba Local config entries")
    raise HomeAssistantError("Multiple entries; pass entry_id")


async def async_fetch_map(hass: HomeAssistant, call: ServiceCall) -> None:
    """Fetch map with ephemeral credentials; do not persist them."""
    entry = _resolve_entry(hass, call.data.get("entry_id"))
    email = str(call.data[CONF_EMAIL]).strip()
    password = str(call.data[CONF_PASSWORD])
    country = str(call.data.get(CONF_COUNTRY) or DEFAULT_COUNTRY).strip().upper()
    blid = entry.data[CONF_BLID]

    try:
        result = await hass.async_add_executor_job(
            lambda: fetch_map_image(
                email=email,
                password=password,
                blid=blid,
                country=country,
            )
        )
    except MapFetchError as err:
        raise HomeAssistantError(str(err)) from err
    finally:
        # Scrub service-local secrets regardless of outcome.
        email = ""
        password = ""

    await hass.async_add_executor_job(save_map_result, hass, result)

    new_options = {
        **dict(entry.options),
        OPT_LAST_MAP_FETCHED_AT: result.fetched_at,
        OPT_LAST_PMAP_ID: result.pmap_id,
        OPT_LAST_PMAPV_ID: result.pmapv_id,
    }
    assert_no_secrets_in_entry_data(new_options)
    hass.config_entries.async_update_entry(entry, options=new_options)
    hass.bus.async_fire(
        f"{DOMAIN}_map_updated",
        {"blid": blid, "fetched_at": result.fetched_at},
    )
    _LOGGER.info("Map fetch service completed for %s", blid[-6:])


async def async_register_services(hass: HomeAssistant) -> None:
    """Register domain services once."""
    if hass.services.has_service(DOMAIN, SERVICE_FETCH_MAP):
        return

    async def _handle(call: ServiceCall) -> None:
        await async_fetch_map(hass, call)

    hass.services.async_register(
        DOMAIN,
        SERVICE_FETCH_MAP,
        _handle,
        schema=SERVICE_FETCH_MAP_SCHEMA,
    )


async def async_unregister_services(hass: HomeAssistant) -> None:
    """Remove domain services when last entry unloads."""
    if hass.config_entries.async_entries(DOMAIN):
        return
    if hass.services.has_service(DOMAIN, SERVICE_FETCH_MAP):
        hass.services.async_remove(DOMAIN, SERVICE_FETCH_MAP)
