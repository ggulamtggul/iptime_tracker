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
                # Store credentials for the next step
                self.login_info = user_input
                self.title = info["title"]
                return await self.async_step_pick_devices()
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except Exception:  # pylint: disable=broad-except
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"

        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_DATA_SCHEMA, errors=errors
        )

    async def async_step_pick_devices(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the device selection step."""
        if user_input is not None:
             # Create entry with credentials and selected options
            options = {
                CONF_TRACKED_MACS: user_input.get(CONF_TRACKED_MACS, []),
                "scan_interval": user_input.get("scan_interval", DEFAULT_INTERVAL),
                CONF_RSS_LIMIT: user_input.get(CONF_RSS_LIMIT, RSS_LIMIT),
                CONF_HOME_THRESHOLD: user_input.get(CONF_HOME_THRESHOLD, 2),
                CONF_NOT_HOME_THRESHOLD: user_input.get(CONF_NOT_HOME_THRESHOLD, 5),
            }
            return self.async_create_entry(
                title=self.title,
                data=self.login_info,
                options=options
            )

        # Fetch devices using the credentials
        api = IPTimeAPI(self.hass, self.login_info[CONF_URL], self.login_info[CONF_ID], self.login_info[CONF_PASSWORD])
        
        devices = {}
        try:
            # We need to perform an update to get the list
            # We reuse the logic from verify/login but we need the actual data now
            # Rely on async_update which handles login/mesh/etc
            result = await api.async_update()
            
            if result and "session" in result and result["session"] is not False:
                # result is the dict of devices
                for mac, info in result.items():
                    if mac == "session": continue
                    name = info.get("name") or info.get("nickname") or mac
                    devices[mac] = f"{name} ({mac})"
        except Exception:
            _LOGGER.warning("Could not fetch devices for selection step", exc_info=True)
            pass

        return self.async_show_form(
            step_id="pick_devices",
            data_schema=vol.Schema({
                vol.Optional(CONF_TRACKED_MACS, default=[]): cv.multi_select(devices),
                vol.Optional("scan_interval", default=DEFAULT_INTERVAL): int,
                vol.Optional(CONF_RSS_LIMIT, default=RSS_LIMIT): int,
                vol.Optional(CONF_HOME_THRESHOLD, default=2): int,
                vol.Optional(CONF_NOT_HOME_THRESHOLD, default=5): int,
            }),
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
        self.entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Manage the options."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        try:
            # Safely get options
            options = self.entry.options
            
            # Get current devices from coordinator safely
            devices = {}
            try:
                coordinator = self.hass.data[DOMAIN][self.entry.entry_id]
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
                ): vol.All(vol.Coerce(int), vol.Range(min=1)),
                vol.Optional(
                    CONF_RSS_LIMIT,
                    default=options.get(CONF_RSS_LIMIT, RSS_LIMIT),
                ): vol.Coerce(int),
                vol.Optional(
                    CONF_HOME_THRESHOLD,
                    default=options.get(CONF_HOME_THRESHOLD, 2),
                ): vol.Coerce(int),
                vol.Optional(
                    CONF_NOT_HOME_THRESHOLD,
                    default=options.get(CONF_NOT_HOME_THRESHOLD, 5),
                ): vol.Coerce(int),
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
