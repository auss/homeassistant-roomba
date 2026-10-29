"""Config flow for Roomba Local (LAN setup + map + room clean plan)."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_HOST, CONF_PASSWORD
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import config_validation as cv

from .clean_options import parse_room_order_text
from .const import (
    CONF_BLID,
    CONF_COUNTRY,
    CONF_EMAIL,
    DEFAULT_COUNTRY,
    DEFAULT_PORT,
    DOMAIN,
    OPT_CLEAN_ROOM_ORDER,
    OPT_LAST_MAP_FETCHED_AT,
    OPT_LAST_PMAP_ID,
    OPT_LAST_PMAPV_ID,
)
from .map_cloud import MapFetchError, fetch_map_image
from .map_store import assert_no_secrets_in_entry_data, load_rooms, save_map_result
from .roomba_mqtt import RoombaMqttError, format_endpoint, resolve_host, validate_connection

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_BLID): str,
        vol.Required(CONF_PASSWORD): str,
        vol.Optional(CONF_HOST): str,
    }
)

STEP_FETCH_MAP_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_EMAIL): str,
        vol.Required(CONF_PASSWORD): str,
        vol.Optional(CONF_COUNTRY, default=DEFAULT_COUNTRY): cv.string,
    }
)


async def _validate_input(hass: HomeAssistant, data: dict[str, Any]) -> dict[str, str]:
    """Validate credentials by attempting a local MQTT connection."""
    blid = data[CONF_BLID].strip()
    password = data[CONF_PASSWORD].strip()
    host = (data.get(CONF_HOST) or "").strip() or None

    state = await hass.async_add_executor_job(validate_connection, blid, password, host)
    title = state.name or f"Roomba {blid[-6:].upper()}"
    return {"title": title, "blid": blid}


class RoombaLocalConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Roomba Local."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> RoombaLocalOptionsFlow:
        """Return the options flow."""
        return RoombaLocalOptionsFlow(config_entry)

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial LAN setup step."""
        errors: dict[str, str] = {}
        placeholders: dict[str, str] = {"endpoint": "—"}

        if user_input is not None:
            blid = user_input[CONF_BLID].strip()
            host_input = (user_input.get(CONF_HOST) or "").strip() or None
            placeholders["endpoint"] = format_endpoint(
                resolve_host(blid, host_input), DEFAULT_PORT
            )
            try:
                info = await _validate_input(self.hass, user_input)
            except RoombaMqttError as err:
                if err.endpoint:
                    placeholders["endpoint"] = err.endpoint
                _LOGGER.warning(
                    "Cannot connect to Roomba at %s (%s): %s",
                    placeholders["endpoint"],
                    err.kind,
                    err,
                )
                errors["base"] = err.kind.value
            except ConnectionError:
                _LOGGER.exception(
                    "Cannot connect to Roomba at %s", placeholders["endpoint"]
                )
                errors["base"] = "cannot_connect"
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Unexpected error validating Roomba credentials")
                errors["base"] = "unknown"
            else:
                await self.async_set_unique_id(info["blid"].lower())
                self._abort_if_unique_id_configured()
                data = {
                    CONF_BLID: info["blid"],
                    CONF_PASSWORD: user_input[CONF_PASSWORD].strip(),
                }
                host = (user_input.get(CONF_HOST) or "").strip()
                if host:
                    data[CONF_HOST] = host
                assert_no_secrets_in_entry_data(
                    {k: v for k, v in data.items() if k != CONF_PASSWORD}
                )
                return self.async_create_entry(title=info["title"], data=data)

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
            description_placeholders=placeholders,
        )


class RoombaLocalOptionsFlow(OptionsFlow):
    """Options: fetch map + set ordered room clean plan."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        self._config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show options menu."""
        return self.async_show_menu(
            step_id="init",
            menu_options=["fetch_map", "room_plan"],
        )

    async def async_step_fetch_map(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Prompt for account credentials for this run only, then discard them."""
        errors: dict[str, str] = {}

        if user_input is not None:
            email = user_input[CONF_EMAIL].strip()
            password = user_input[CONF_PASSWORD]
            country = (user_input.get(CONF_COUNTRY) or DEFAULT_COUNTRY).strip().upper()
            blid = self._config_entry.data[CONF_BLID]

            try:
                result = await self.hass.async_add_executor_job(
                    lambda: fetch_map_image(
                        email=email,
                        password=password,
                        blid=blid,
                        country=country,
                    )
                )
            except MapFetchError:
                _LOGGER.exception("Map fetch failed")
                errors["base"] = "map_fetch_failed"
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Unexpected map fetch error")
                errors["base"] = "unknown"
            else:
                email = ""
                password = ""
                user_input = {}

                await self.hass.async_add_executor_job(save_map_result, self.hass, result)

                new_options = {
                    **dict(self._config_entry.options),
                    OPT_LAST_MAP_FETCHED_AT: result.fetched_at,
                    OPT_LAST_PMAP_ID: result.pmap_id,
                    OPT_LAST_PMAPV_ID: result.pmapv_id,
                }
                assert_no_secrets_in_entry_data(new_options)
                assert CONF_EMAIL not in new_options
                assert CONF_PASSWORD not in new_options

                self.hass.bus.async_fire(
                    f"{DOMAIN}_map_updated",
                    {"blid": blid, "fetched_at": result.fetched_at},
                )
                return self.async_create_entry(title="", data=new_options)

            email = ""
            password = ""

        return self.async_show_form(
            step_id="fetch_map",
            data_schema=STEP_FETCH_MAP_SCHEMA,
            errors=errors,
            description_placeholders={
                "blid_suffix": self._config_entry.data[CONF_BLID][-6:].upper()
            },
        )

    async def async_step_room_plan(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Set ordered room list for the Start room clean button."""
        blid = self._config_entry.data[CONF_BLID]
        known = await self.hass.async_add_executor_job(load_rooms, self.hass, blid)
        known_names = [r["name"] for r in known]
        known_text = ", ".join(known_names) if known_names else "(fetch map first)"
        current = list(self._config_entry.options.get(OPT_CLEAN_ROOM_ORDER) or [])
        current_text = ", ".join(current)
        errors: dict[str, str] = {}

        if user_input is not None:
            order = parse_room_order_text(str(user_input.get("room_order") or ""))
            if not known_names:
                errors["base"] = "no_rooms"
            elif not order:
                errors["base"] = "empty_order"
            else:
                known_fold = {n.casefold(): n for n in known_names}
                resolved: list[str] = []
                bad: list[str] = []
                for name in order:
                    canon = known_fold.get(name.casefold())
                    if canon is None:
                        bad.append(name)
                    else:
                        resolved.append(canon)
                if bad:
                    errors["base"] = "unknown_rooms"
                else:
                    new_options = {
                        **dict(self._config_entry.options),
                        OPT_CLEAN_ROOM_ORDER: resolved,
                    }
                    assert_no_secrets_in_entry_data(new_options)
                    return self.async_create_entry(title="", data=new_options)

        schema = vol.Schema(
            {
                vol.Required("room_order", default=current_text): str,
            }
        )
        return self.async_show_form(
            step_id="room_plan",
            data_schema=schema,
            errors=errors,
            description_placeholders={"known_rooms": known_text},
        )
