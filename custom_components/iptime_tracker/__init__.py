"""The ipTIME Tracker integration."""
from __future__ import annotations

import asyncio
from datetime import timedelta
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform, CONF_URL, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import IPTimeAPI
from .const import (
    DOMAIN,
    CONF_ID,
    DEFAULT_INTERVAL,
    CONF_RSS_LIMIT,
    CONF_HOME_THRESHOLD,
    CONF_NOT_HOME_THRESHOLD,
    RSS_LIMIT,
)

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.DEVICE_TRACKER]

async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up the ipTIME Tracker component."""
    return True

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up ipTIME Tracker from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    url = entry.data[CONF_URL]
    user_id = entry.data[CONF_ID]
    user_pw = entry.data[CONF_PASSWORD]
    
    # Options or defaults
    scan_interval = entry.options.get("scan_interval", DEFAULT_INTERVAL)
    rss_limit = entry.options.get(CONF_RSS_LIMIT, RSS_LIMIT)
    # thresholds can be passed to entities
    
    api = IPTimeAPI(hass, url, user_id, user_pw)

    async def async_update_data():
        """Fetch data from API."""
        try:
            return await api.async_update()
        except Exception as err:
            raise UpdateFailed(f"Error communicating with API: {err}")

    coordinator = DataUpdateCoordinator(
        hass,
        _LOGGER,
        name=DOMAIN,
        update_method=async_update_data,
        update_interval=timedelta(seconds=scan_interval),
    )

    await coordinator.async_config_entry_first_refresh()

    hass.data[DOMAIN][entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    entry.async_on_unload(entry.add_update_listener(async_update_options))

    return True

async def async_update_options(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Update options."""
    await hass.config_entries.async_reload(entry.entry_id)

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok
