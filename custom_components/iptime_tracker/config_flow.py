"""Config flow for ipTIME Tracker integration."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

import homeassistant.helpers.config_validation as cv
from homeassistant import config_entries
from homeassistant.const import CONF_PASSWORD, CONF_URL
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.exceptions import HomeAssistantError

from .api import IPTimeAPI
from .const import (
    DOMAIN,
    CONF_ID,
    CONF_RSS_LIMIT,
    CONF_HOME_THRESHOLD,
    CONF_NOT_HOME_THRESHOLD,
    CONF_TRACKED_MACS,
    RSS_LIMIT,
    DEFAULT_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_URL, default="192.168.0.1"): str,
        vol.Required(CONF_ID, default="admin"): str,
        vol.Required(CONF_PASSWORD): str,
    }
)

async def validate_input(hass: HomeAssistant, data: dict[str, Any]) -> dict[str, Any]:
    """Validate the user input allows us to connect."""
    api = IPTimeAPI(hass, data[CONF_URL], data[CONF_ID], data[CONF_PASSWORD])

    if await api.verify_beta_ui():
        if not await api.login_beta_ui():
            raise InvalidAuth
    elif await api.verify_mobile():
        if not await api.m_login():
             raise InvalidAuth
    else:
        if not await api.login():
            raise InvalidAuth

    # If login successful, return title
    return {"title": data[CONF_URL]}


class IPTimeConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for ipTIME Tracker."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                info = await validate_input(self.hass, user_input)
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except Exception:  # pylint: disable=broad-except
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"
            else:
                return self.async_create_entry(title=info["title"], data=user_input)

        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_DATA_SCHEMA, errors=errors
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        """Create the options flow."""
        return IPTimeOptionsFlowHandler(config_entry)


class IPTimeOptionsFlowHandler(config_entries.OptionsFlow):
    """Handle options."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """Initialize options flow."""
        self.config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Manage the options."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        try:
            # Safely get options
            options = self.config_entry.options
            
            # Get current devices from coordinator safely
            devices = {}
            try:
                coordinator = self.hass.data[DOMAIN][self.config_entry.entry_id]
                if coordinator.data:
                    for mac, info in coordinator.data.items():
                        if mac == "session":
                            continue
                        name = info.get("name") or info.get("nickname") or mac
                        devices[mac] = f"{name} ({mac})"
            except Exception:
                pass

            # Build schema with Coerce for safety
            options_schema = {
                vol.Optional(
                    "scan_interval",
                    default=options.get("scan_interval", DEFAULT_INTERVAL),
                ): int,
                vol.Optional(
                    CONF_RSS_LIMIT,
                    default=options.get(CONF_RSS_LIMIT, RSS_LIMIT),
                ): int,
                vol.Optional(
                    CONF_HOME_THRESHOLD,
                    default=options.get(CONF_HOME_THRESHOLD, 2),
                ): int,
                vol.Optional(
                    CONF_NOT_HOME_THRESHOLD,
                    default=options.get(CONF_NOT_HOME_THRESHOLD, 5),
                ): int,
            }

            # Add multi-select if devices are available or we have tracked devices
            current_tracked = options.get(CONF_TRACKED_MACS)
            if current_tracked:
                # Critical: Ensure all default selections are in the devices list
                for mac in current_tracked:
                    if mac not in devices:
                        devices[mac] = f"{mac} (Offline/Unknown)"

            if devices:
                # Default to current_tracked or empty list
                default_selection = current_tracked if current_tracked else []
                options_schema[
                    vol.Optional(CONF_TRACKED_MACS, default=default_selection)
                ] = cv.multi_select(devices)

            return self.async_show_form(
                step_id="init",
                data_schema=vol.Schema(options_schema),
            )
        
        except Exception:
            _LOGGER.exception("Unexpected error in options flow")
            return self.async_show_form(
                step_id="init",
                data_schema=vol.Schema({}),
                errors={"base": "unknown"},
            )


class CannotConnect(HomeAssistantError):
    """Error to indicate we cannot connect."""


class InvalidAuth(HomeAssistantError):
    """Error to indicate there is invalid auth."""
