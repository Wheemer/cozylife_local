"""Button entities for APK-defined CozyLife commands."""

from __future__ import annotations

import logging

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, PLUG_CLEAR_LOG
from .coordinator import CozyLifeCoordinator
from .switch_options import supported_dpids

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities,
) -> None:
    """Set up source-backed CozyLife button entities."""
    coordinator: CozyLifeCoordinator = hass.data[DOMAIN][config_entry.entry_id]
    if PLUG_CLEAR_LOG not in supported_dpids(coordinator):
        return
    async_add_entities([CozyLifeClearDeviceLogButton(coordinator)])


class CozyLifeClearDeviceLogButton(
    CoordinatorEntity[CozyLifeCoordinator],
    ButtonEntity,
):
    """Clear a plug's device-side event log."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:clipboard-text-off-outline"

    def __init__(self, coordinator: CozyLifeCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_name = f"{coordinator.device.device_model_name} Clear Device Log"
        self._attr_unique_id = f"{coordinator.device.device_id}_clear_device_log"

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self.coordinator.device.device_id)},
            name=self.coordinator.device.device_model_name,
            manufacturer="CozyLife",
            model=self.coordinator.device.pid,
        )

    async def async_press(self) -> None:
        if not await self.coordinator.device.async_set_state({PLUG_CLEAR_LOG: 1}):
            _LOGGER.warning(
                "Failed to clear device log for CozyLife device %s",
                self.coordinator.device.ip_address,
            )
