"""Support for ipTIME Tracker."""
from __future__ import annotations

from homeassistant.components.device_tracker import SourceType, TrackerEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN

async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up ipTIME Tracker based on a config entry."""
    coordinator = hass.data[DOMAIN][entry.entry_id]

    # 이미 추가된 기기 추적용
    created_devices = set()

    @callback
    def _create_entities():
        """Create entities for new devices."""
        new_entities = []
        # 코디네이터 데이터(MAC 목록)를 순회
        if coordinator.data:
            for mac, info in coordinator.data.items():
                if mac == "session": continue
                if mac not in created_devices:
                    new_entities.append(IPTimeTrackerEntity(coordinator, mac, info))
                    created_devices.add(mac)
        
        if new_entities:
            async_add_entities(new_entities)

    # 데이터가 업데이트될 때마다 신규 기기 확인
    entry.async_on_unload(coordinator.async_add_listener(_create_entities))
    
    # 최초 실행
    _create_entities()


class IPTimeTrackerEntity(CoordinatorEntity, TrackerEntity):
    """Representation of an ipTIME device."""

    def __init__(self, coordinator, mac, info):
        """Initialize the entity."""
        super().__init__(coordinator)
        self._mac = mac
        self._attr_unique_id = mac
        self._attr_name = mac  # 기본 이름은 MAC (사용자가 UI에서 변경 가능)
        
    @property
    def source_type(self) -> SourceType:
        """Return the source type."""
        return SourceType.ROUTER

    @property
    def is_connected(self) -> bool:
        """Return true if the device is connected to the network."""
        if not self.coordinator.data or self.coordinator.data.get("session") is False:
            return False # 연결 끊김 처리 (원하면 마지막 상태 유지 로직 추가 가능)
        
        device_info = self.coordinator.data.get(self._mac)
        if device_info:
            return device_info.get("state") == "home"
        return False

    @property
    def extra_state_attributes(self):
        """Return optional attributes."""
        if self.coordinator.data and (info := self.coordinator.data.get(self._mac)):
            return {
                "ip": info.get("ip"),
                "band": info.get("band"),
                "rssi": info.get("rssi"),
            }
        return {}

    @property
    def mac_address(self) -> str:
        """Return the mac address of the device."""
        return self._mac.replace("-", ":")
