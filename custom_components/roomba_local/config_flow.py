"""Config flow for Roomba Local."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_HOST, CONF_PASSWORD
from homeassistant.core import HomeAssistant

from .const import CONF_BLID, DOMAIN
from .roomba_mqtt import validate_connection

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_BLID): str,
        vol.Required(CONF_PASSWORD): str,
        vol.Optional(CONF_HOST): str,
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

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                info = await _validate_input(self.hass, user_input)
            except ConnectionError:
                _LOGGER.exception("Cannot connect to Roomba")
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
                return self.async_create_entry(title=info["title"], data=data)

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
        )
