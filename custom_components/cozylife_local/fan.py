"""Fan entities for APK-catalogued CozyLife Wi-Fi motor products."""

from __future__ import annotations

from homeassistant.components.fan import FanEntity, FanEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import CozyLifeCoordinator

_POWER_DPID = "1"
_PERCENTAGE_SPEED_DPID = "4"
_GEAR_SPEED_DPID = "16"
_THREE_SPEED_DPID = "9"
_WIND_MODE_DPID = "11"
_WIND_MODE_OPTIONS = {
    0: "Normal",
    1: "Natural",
    2: "Sleep",
    3: "Forest",
    4: "Maternal and infant",
    5: "AI smart",
}
_WIND_MODE_VALUES = {value: key for key, value in _WIND_MODE_OPTIONS.items()}


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities,
) -> None:
    """Set up catalogued Wi-Fi fan entities."""
    coordinator: CozyLifeCoordinator = hass.data[DOMAIN][config_entry.entry_id]
    if coordinator.classification.is_fan:
        async_add_entities([CozyLifeFan(coordinator)])


class CozyLifeFan(CoordinatorEntity[CozyLifeCoordinator], FanEntity):
    """A CozyLife Wi-Fi fan using the vendor motor DPID definitions."""

    _attr_has_entity_name = True
    _attr_name = None

    def __init__(self, coordinator: CozyLifeCoordinator) -> None:
        """Initialize a native Home Assistant fan entity."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.device.device_id}_fan"
        self._dpids = set(coordinator.device.dpid or [])
        features = FanEntityFeature(0)
        if self._speed_dpid is not None:
            features |= FanEntityFeature.SET_SPEED
        if _WIND_MODE_DPID in self._dpids:
            features |= FanEntityFeature.PRESET_MODE
            self._attr_preset_modes = list(_WIND_MODE_VALUES)
        self._attr_supported_features = features

    @property
    def _speed_dpid(self) -> str | None:
        """Return the highest-fidelity speed control the model exposes."""
        for dpid in (_GEAR_SPEED_DPID, _PERCENTAGE_SPEED_DPID, _THREE_SPEED_DPID):
            if dpid in self._dpids:
                return dpid
        return None

    @property
    def device_info(self) -> DeviceInfo:
        """Return the parent device metadata."""
        return DeviceInfo(
            identifiers={(DOMAIN, self.coordinator.device.device_id)},
            name=self.coordinator.device.device_model_name,
            manufacturer="CozyLife",
            model=self.coordinator.device.pid,
        )

    @property
    def is_on(self) -> bool | None:
        """Return the vendor motor relay state."""
        raw = (self.coordinator.data or {}).get(_POWER_DPID)
        if raw is None:
            return None
        return raw not in (0, "0", False, "stop")

    @property
    def percentage(self) -> int | None:
        """Translate the documented fan speed DPIDs to a percentage."""
        dpid = self._speed_dpid
        raw = (self.coordinator.data or {}).get(dpid) if dpid else None
        if raw is None:
            return None
        try:
            value = int(raw)
        except (TypeError, ValueError):
            return None
        if dpid == _THREE_SPEED_DPID:
            return (value + 1) * 100 // 3
        return max(0, min(100, value))

    @property
    def preset_mode(self) -> str | None:
        """Return the documented wind-class mode when present."""
        raw = (self.coordinator.data or {}).get(_WIND_MODE_DPID)
        try:
            return _WIND_MODE_OPTIONS.get(int(raw))
        except (TypeError, ValueError):
            return None

    async def async_turn_on(self, **kwargs) -> None:
        """Start the fan using the motor switch DPID."""
        if await self.coordinator.device.async_set_state({_POWER_DPID: 1}):
            self.coordinator.data[_POWER_DPID] = 1
            self.async_write_ha_state()

    async def async_turn_off(self, **kwargs) -> None:
        """Stop the fan using the motor switch DPID."""
        if await self.coordinator.device.async_set_state({_POWER_DPID: 0}):
            self.coordinator.data[_POWER_DPID] = 0
            self.async_write_ha_state()

    async def async_set_percentage(self, percentage: int) -> None:
        """Set the documented speed DPID without changing unrelated modes."""
        dpid = self._speed_dpid
        if dpid is None:
            return
        if percentage == 0:
            await self.async_turn_off()
            return
        value = percentage
        if dpid == _THREE_SPEED_DPID:
            value = min(2, max(0, (percentage * 3 - 1) // 100))
        if await self.coordinator.device.async_set_state({dpid: value}):
            self.coordinator.data[dpid] = value
            if not self.is_on:
                self.coordinator.data[_POWER_DPID] = 1
            self.async_write_ha_state()

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        """Set the APK/vendor wind-class mode."""
        value = _WIND_MODE_VALUES[preset_mode]
        if await self.coordinator.device.async_set_state({_WIND_MODE_DPID: value}):
            self.coordinator.data[_WIND_MODE_DPID] = value
            self.async_write_ha_state()

    @callback
    def _handle_coordinator_update(self) -> None:
        """Write a new Home Assistant state when a full property frame arrives."""
        self.async_write_ha_state()
