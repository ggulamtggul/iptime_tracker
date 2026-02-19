"""Platform for sensor integration."""
from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from homeassistant.config_entries import ConfigEntry
import re

import voluptuous as vol



from homeassistant.components.device_tracker import PLATFORM_SCHEMA, ScannerEntity, SourceType
from homeassistant.components.device_tracker.const import CONF_SCAN_INTERVAL
from homeassistant.const import (
    CONF_NAME,
    CONF_MAC,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    CoordinatorEntity,
    UpdateFailed,
)
from homeassistant.util import dt as dt_util
from homeassistant.util import slugify

from .api import IPTimeAPI
from .const import (
    DOMAIN,
    CONF_URL,
    CONF_ID,
    CONF_PASSWORD,
    CONF_TARGET,
    DEFAULT_INTERVAL,
    HOSTINFO_URN,
    LOGIN_URN,
    LOGOUT_URN,
    WLAN_2G_URN,
    WLAN_5G_URN,
    MESH_URN,
    M_LOGIN_URN,
    M_LOGOUT_URN,
    M_WLAN_2G_URN,
    M_WLAN_5G_URN,
    M_MESH_URN,
    MESH_STATION_URN,
    TIME_OUT,
    BETA_UI_URN,
    BETA_SERVICE_URN,
    RSS_LIMIT,
    CONF_RSS_LIMIT,
    CONF_HOME_THRESHOLD,
    CONF_NOT_HOME_THRESHOLD,
    CONF_TRACKED_MACS,
)

_LOGGER = logging.getLogger(__name__)

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend(
    {
        vol.Required(CONF_URL): cv.string,
        vol.Required(CONF_ID): cv.string,
        vol.Required(CONF_PASSWORD): cv.string,
        vol.Required(CONF_TARGET): vol.All(
            cv.ensure_list,
            [
                {
                    vol.Required(CONF_NAME): cv.string,
                    vol.Required(CONF_MAC): cv.string,
                }
            ],
        ),
        vol.Optional(CONF_RSS_LIMIT, default=RSS_LIMIT): vol.Coerce(int),
        vol.Optional(CONF_HOME_THRESHOLD, default=2): vol.Coerce(int),
        vol.Optional(CONF_NOT_HOME_THRESHOLD, default=5): vol.Coerce(int),
    }
)

async def async_setup_scanner(
    hass: HomeAssistant, config: dict, async_see, discovery_info=None
):
    """Set up the device tracker."""
    url = config.get(CONF_URL)
    user_id = config.get(CONF_ID)
    user_pw = config.get(CONF_PASSWORD)
    targets = config.get(CONF_TARGET)
    scan_interval = config.get(
        CONF_SCAN_INTERVAL, timedelta(seconds=DEFAULT_INTERVAL)
    )
    rss_limit = config.get(CONF_RSS_LIMIT)
    home_threshold = config.get(CONF_HOME_THRESHOLD)
    not_home_threshold = config.get(CONF_NOT_HOME_THRESHOLD)

    api = IPTimeAPI(hass, url, user_id, user_pw)
    
    coordinator = DataUpdateCoordinator(
        hass,
        _LOGGER,
        name="iptime_tracker",
        update_method=api.async_update,
        update_interval=scan_interval,
    )

    await coordinator.async_refresh()

    sensors = [IPTimeSensor(target[CONF_NAME], target[CONF_MAC], api, rss_limit, home_threshold, not_home_threshold) for target in targets]

    async def async_update_devices():
        """코디네이터 업데이트 후 디바이스 상태를 HA에 알림"""
        for sensor in sensors:
            sensor.update_state_from_coordinator()
            await async_see(
                mac=f"{sensor.state_attributes.get('iptime_url', 'iptime')}_{sensor._target_mac}",
                host_name=sensor.name,
                location_name=sensor.state,
                attributes=sensor.state_attributes,
                source_type="ipTIME_Tracker",
            )

    @callback
    def _update_listener():
        hass.async_create_task(async_update_devices())

    coordinator.async_add_listener(_update_listener)
    await async_update_devices()


    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities):
    """Set up device tracker from a config entry."""
    coordinator = hass.data[DOMAIN][entry.entry_id]
    
    # We need to track all connected devices from the coordinator result
    # The coordinator result is a dict of mac -> info
    
    # In this initial implementation, we will add entities for ALL devices found in the first refresh.
    # Dynamically adding new devices is supported by the coordinator listener/entity management, 
    # but for simplicity, we start with what we have.
    # To support dynamic addition, we would need to listen to coordinator changes and add entities.
    
    # For now, let's create entities for currently connected devices.
    entities = []
    
    # Options
    rss_limit = entry.options.get(CONF_RSS_LIMIT, RSS_LIMIT)
    home_threshold = entry.options.get(CONF_HOME_THRESHOLD, 2)
    not_home_threshold = entry.options.get(CONF_NOT_HOME_THRESHOLD, 5)
    tracked_macs = entry.options.get(CONF_TRACKED_MACS)

    if coordinator.data:
        # coordinator.data is a dict where keys are MAC addresses (maybe with dashes)
        # But wait, api.py returns result_dict.
        # device_tracker.py IPTimeSensor logic expects result_dict[mac]
        
        for mac, device_info in coordinator.data.items():
            if mac == "session": continue # skip the session key if present
            
            # Filter if tracked_macs is set (not None)
            # If None, it means "Track All" (default)
            # If empty list [], it means "Track None"
            if tracked_macs is not None and mac not in tracked_macs:
                continue

            # The mac in the dict might be with dashes or colons depending on api implementation.
            # api.py replaces : with - in keys.
            
            entities.append(IPTimeTracker(coordinator, mac, device_info, rss_limit, home_threshold, not_home_threshold))
            
    async_add_entities(entities)


class IPTimeTracker(CoordinatorEntity, ScannerEntity):
    """Representation of a Device Tracker for ipTIME."""
    
    def __init__(self, coordinator, mac, device_info, rss_limit, home_threshold, not_home_threshold):
        """Initialize."""
        super().__init__(coordinator)
        self._mac = mac
        self._device_info = device_info
        self._rss_limit = rss_limit
        self._home_threshold = home_threshold
        self._not_home_threshold = not_home_threshold
        
        self._home_count = 0
        self._not_home_count = 0
        self._is_connected = True # Initially connected when found
        
        # Unique ID is essential for UI
        self._attr_unique_id = f"iptime_{mac}"
        self._attr_name = device_info.get("name") or f"Device {mac}"
        
        # Attempt to get hostname from somewhere if available? 
        # API doesn't seem to return hostname easily in all methods, strictly IP/MAC/RSSI.
        # If possible, we can improve this later.

    @property
    def source_type(self) -> SourceType:
        """Return the source type."""
        return SourceType.ROUTER

    @property
    def ip_address(self) -> str | None:
        """Return the primary ip address of the device."""
        if self.coordinator.data and self._mac in self.coordinator.data:
            return self.coordinator.data[self._mac].get("ip")
        return None

    @property
    def mac_address(self) -> str | None:
        """Return the mac address of the device."""
        return self._mac.replace("-", ":")

    @property
    def is_connected(self) -> bool:
        """Return true if the device is connected to the network."""
        if not self.coordinator.data:
            return False
            
        return self._is_connected

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        is_connected_now = False
        
        if self.coordinator.data and self._mac in self.coordinator.data:
            device_data = self.coordinator.data[self._mac]
            rss = device_data.get("rssi")
            
            # Check RSS
            if isinstance(rss, int) and rss < self._rss_limit:
                 is_connected_now = False
            else:
                 is_connected_now = True
        
        # Debounce logic
        if is_connected_now:
            self._not_home_count = 0
            if self._home_count < self._home_threshold:
                self._home_count += 1
        else:
            self._home_count = 0
            if self._not_home_count < self._not_home_threshold:
                self._not_home_count += 1
                
        # Update state based on counts
        if self._home_count >= self._home_threshold:
            self._is_connected = True
        elif self._not_home_count >= self._not_home_threshold:
            self._is_connected = False
            
        self.async_write_ha_state()

    @property
    def device_info(self) -> DeviceInfo:
        """Return the device info."""
        return DeviceInfo(
            identifiers={(DOMAIN, self._mac)},
            name=self.name,
            manufacturer="ipTIME",
            via_device=(DOMAIN, self.coordinator.config_entry.entry_id),
        )

    @property
    def extra_state_attributes(self):
        """Return entity specific state attributes."""
        if self.coordinator.data and self._mac in self.coordinator.data:
             data = self.coordinator.data[self._mac]
             return {
                 "band": data.get("band"),
                 "stay_time": data.get("stay_time"),
                 "rssi": data.get("rssi"),
             }
        return {}




class IPTimeSensor:
    """Representation of a Sensor."""

    def __init__(self, name, mac, api, rss_limit, home_threshold, not_home_threshold) -> None:
        self._state = "N/A"
        self._entity_id = name
        self._target_mac = mac.replace(":", "-")
        self._api = api
        self.error_count = 0
        self.error_threshold = 3
        self.not_home_count = 0
        self.not_home_threshold = not_home_threshold
        self._state_attributes = {}
        self.home_count = 0
        self.home_threshold = home_threshold  # 2번 연속으로 감지돼야 진짜 왔다고 인정
        self.rss_limit = rss_limit
        
    @property
    def name(self):
        if self._entity_id:
            return f"iptime_{self._entity_id}"
        return f"iptime_{self._api._user_id}"

    @property
    def state(self):
        return self._state

    @property
    def state_attributes(self):
        return self._state_attributes

    def update_state_from_coordinator(self):
        result_dict = self._api.result

        data = {
            "name": self._entity_id,
            "mac_address": self._target_mac,
            "iptime_url": self._api._url,
        }

        # 1. API가 한 번도 실행되지 않았거나 에러 상태(None)인 경우 -> N/A
        if result_dict is None:
            if self.error_count < self.error_threshold:
                self.error_count += 1
            else:
                self._state = "N/A"
            self._state_attributes = data
            return

        # 2. 세션 만료 등의 명시적 실패 -> 상태 유지 (return)
        if result_dict.get("session") is False:
             return

        # 3. 정상 응답 (빈 딕셔너리 {} 포함) -> 로직 수행
        self.error_count = 0
        
# [수정할 핵심 로직 부분]
        if self._target_mac in result_dict:
            # 목록에 있음 -> 바로 재실 처리하지 않고 카운트 체크
            self.not_home_count = 0 # 외출 카운트는 리셋
            
            if self.home_count < self.home_threshold:
                self.home_count += 1
            
            # 설정한 횟수만큼 연속으로 감지되었을 때만 상태 변경
            if self.home_count >= self.home_threshold:
                device_info = result_dict[self._target_mac]

                rss = device_info.get("rssi")
                if isinstance(rss, int):
                    if rss < self.rss_limit:
                        self._state = "not_home"
                    else:
                        self._state = "home"
                else:
                    self._state = device_info.get("state", "home")
                
                # 속성 업데이트
                data.update({
                    "stay_time": device_info.get("stay_time", "N/A"),
                    "band": device_info.get("band", "N/A"),
                    "ip": device_info.get("ip", "N/A"),
                    "rssi": device_info.get("rssi", "N/A"),
                })
            # 아직 카운트가 부족하면 이전 상태 유지 (아무것도 안 함)
            
        else:
            # 목록에 없음
            self.home_count = 0 # 재실 카운트 리셋
            
            if self.not_home_count < self.not_home_threshold:
                self.not_home_count += 1
            else:
                self._state = "not_home"
            
            # (속성 N/A 처리 부분은 그대로 유지)
            data.update({
                "stay_time": "N/A", "band": "N/A", "ip": "N/A", "rssi": "N/A"
            })

        self._state_attributes = data
