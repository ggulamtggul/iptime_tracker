"""Support for ipTIME Tracker."""
from __future__ import annotations

from homeassistant.components.device_tracker import ScannerEntity, SourceType
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
    created_devices = set()

    @callback
    def _create_entities():
        """Create entities for new devices."""
        new_entities = []
        if coordinator.data:
            for mac, info in coordinator.data.items():
                if mac == "session": continue
                if mac not in created_devices:
                    new_entities.append(IPTimeTrackerEntity(coordinator, mac, info))
                    created_devices.add(mac)
        
        if new_entities:
            async_add_entities(new_entities)

    entry.async_on_unload(coordinator.async_add_listener(_create_entities))
    _create_entities()


class IPTimeTrackerEntity(CoordinatorEntity, ScannerEntity):
    """Representation of an ipTIME device."""

    def __init__(self, coordinator, mac, info):
        """Initialize the entity."""
        super().__init__(coordinator)
        self._mac = mac
        # 고유 ID 설정 (MAC 주소 활용)
        self._attr_unique_id = mac
        # 기본 이름 설정 (추후 UI에서 변경 가능)
        self._attr_name = mac
        
    @property
    def source_type(self) -> SourceType:
        """Return the source type."""
        return SourceType.ROUTER

    @property
    def is_connected(self) -> bool:
        """Return true if the device is connected to the network."""
        if not self.coordinator.data or self.coordinator.data.get("session") is False:
            return False
        
        device_info = self.coordinator.data.get(self._mac)
        if device_info:
            return device_info.get("state") == "home"
        return False

    @property
    def ip_address(self) -> str | None:
        """Return the primary ip address of the device."""
        if self.coordinator.data and (info := self.coordinator.data.get(self._mac)):
            return info.get("ip")
        return None

    @property
    def mac_address(self) -> str:
        """Return the mac address of the device."""
        return self._mac.replace("-", ":")

    @property
    def hostname(self) -> str | None:
        """Return hostname if available."""
        # 호스트네임 정보가 있다면 여기서 반환 (현재 API는 MAC을 이름으로 씀)
        return self._attr_name

    @property
    def extra_state_attributes(self):
        """Return optional attributes."""
        if self.coordinator.data and (info := self.coordinator.data.get(self._mac)):
            return {
                "band": info.get("band"),
                "rssi": info.get("rssi"),
            }
        return {}
