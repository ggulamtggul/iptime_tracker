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
# [필수] 이 함수가 없으면 "No setup function defined" 오류가 발생합니다.
# ------------------------------------------------------------------
async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up the ipTIME Tracker component."""
    # YAML 설정이 있어도 여기서 처리하지 않고 Config Entry(UI)로 넘깁니다.
    # 함수 자체가 존재해야 Home Assistant가 로드할 수 있습니다.
    return True

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up ipTIME Tracker from a config entry."""
    
    # 1. API 인스턴스 생성
    api = IPTimeAPI(
        hass,
        entry.data[CONF_URL],
        entry.data[CONF_ID],
        entry.data[CONF_PASSWORD],
    )

    # 2. 데이터 업데이트 함수 정의
    async def async_update_data():
        """Fetch data from API."""
        result = await api.async_update()
        if result is None:
             raise UpdateFailed("Failed to fetch data from ipTIME")
        return result

    # 3. 코디네이터 생성
    coordinator = DataUpdateCoordinator(
        hass,
        _LOGGER,
        name=DOMAIN,
        update_method=async_update_data,
        update_interval=timedelta(seconds=DEFAULT_INTERVAL),
    )

    # 4. 최초 데이터 갱신
    await coordinator.async_config_entry_first_refresh()

    # 5. 데이터 저장소 초기화
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = coordinator

    # 6. 플랫폼(device_tracker) 로드
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok
