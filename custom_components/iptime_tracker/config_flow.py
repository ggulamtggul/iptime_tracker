"""Config flow for ipTIME Tracker."""
from __future__ import annotations

import logging
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_URL, CONF_PASSWORD, CONF_ID
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import DOMAIN
from .api import IPTimeAPI

_LOGGER = logging.getLogger(__name__)

class IPTimeConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for ipTIME Tracker."""

    VERSION = 1

    async def async_step_user(self, user_input=None):
        """Handle the initial step."""
        errors = {}

        if user_input is not None:
            # Validate connection
            api = IPTimeAPI(
                self.hass, 
                user_input[CONF_URL], 
                user_input[CONF_ID], 
                user_input[CONF_PASSWORD]
            )
            
            try:
                # Try to login and fetch data once
                result = await api.async_update()
                if result is None:
                    errors["base"] = "cannot_connect"
                else:
                    return self.async_create_entry(
                        title=f"ipTIME ({user_input[CONF_URL]})", 
                        data=user_input
                    )
            except Exception:
                errors["base"] = "unknown"

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({
                vol.Required(CONF_URL): str,
                vol.Required(CONF_ID): str,
                vol.Required(CONF_PASSWORD): str,
            }),
            errors=errors,
        )
