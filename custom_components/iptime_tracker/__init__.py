"""The ipTIME Tracker integration."""
from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_URL, CONF_PASSWORD, CONF_ID, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DOMAIN, DEFAULT_INTERVAL
from .api import IPTimeAPI

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.DEVICE_TRACKER]

# ------------------------------------------------------------------
# [추가됨] 이 함수가 없어서 Setup failed 오류가 발생했습니다.
# ------------------------------------------------------------------
async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up the ipTIME Tracker component."""
    # YAML 설정이 있어도 여기서 처리하지 않고 Config Entry(UI)로 넘깁니다.
    # 단, 함수 자체가 존재해야 오류가 나지 않습니다.
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up ipTIME Tracker from a config entry."""
    
    api = IPTimeAPI(
        hass,
        entry.data[CONF_URL],
        entry.data[CONF_ID],
        entry.data[CONF_PASSWORD],
    )

    async def async_update_data():
        """Fetch data from API."""
        result = await api.async_update()
        if result is None:
             raise UpdateFailed("Failed to fetch data from ipTIME")
        return result

    coordinator = DataUpdateCoordinator(
        hass,
        _LOGGER,
        name=DOMAIN,
        update_method=async_update_data,
        update_interval=timedelta(seconds=DEFAULT_INTERVAL),
    )

    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok
