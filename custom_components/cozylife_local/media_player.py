"""Media-player support for locally controlled CozyLife X7 speakers."""

from __future__ import annotations

from typing import Any

from homeassistant.components.media_player import (
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import CozyLifeCoordinator

_PLAYBACK_DPID = "18"
_VOLUME_DPID = "17"


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities,
) -> None:
    """Set up a media player for a locally reachable X7 Wi-Fi speaker."""
    coordinator: CozyLifeCoordinator = hass.data[DOMAIN][config_entry.entry_id]
    if coordinator.classification.is_smart_speaker:
        async_add_entities([CozyLifeSpeaker(coordinator)])


class CozyLifeSpeaker(CoordinatorEntity[CozyLifeCoordinator], MediaPlayerEntity):
    """A CozyLife X7 Wi-Fi speaker using panel-confirmed local DPIDs."""

    _attr_supported_features = (
        MediaPlayerEntityFeature.PLAY
        | MediaPlayerEntityFeature.PAUSE
        | MediaPlayerEntityFeature.VOLUME_SET
    )

    def __init__(self, coordinator: CozyLifeCoordinator) -> None:
        super().__init__(coordinator)
        name = coordinator.device.device_model_name or "CozyLife X7 Speaker"
        self._attr_name = name
        self._attr_unique_id = f"{coordinator.device.device_id}_speaker"

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self.coordinator.device.device_id)},
            name=self.coordinator.device.device_model_name,
            manufacturer="CozyLife",
            model=self.coordinator.device.pid,
        )

    @property
    def state(self) -> MediaPlayerState | None:
        playback = self.coordinator.data.get(_PLAYBACK_DPID)
        if not isinstance(playback, (int, float)):
            return None
        return MediaPlayerState.PLAYING if playback == 1 else MediaPlayerState.PAUSED

    @property
    def volume_level(self) -> float | None:
        raw_volume = self.coordinator.data.get(_VOLUME_DPID)
        if not isinstance(raw_volume, (int, float)):
            return None
        return min(1.0, max(0.0, raw_volume / 1000))

    async def async_media_play(self) -> None:
        await self._async_set_value(_PLAYBACK_DPID, 1)

    async def async_media_pause(self) -> None:
        await self._async_set_value(_PLAYBACK_DPID, 0)

    async def async_set_volume_level(self, volume: float) -> None:
        value = round(min(1.0, max(0.0, volume)) * 1000)
        await self._async_set_value(_VOLUME_DPID, value)

    async def _async_set_value(self, dpid: str, value: Any) -> None:
        if await self.coordinator.device.async_set_state({dpid: value}):
            self.coordinator.data[dpid] = value
            self.async_write_ha_state()

    @callback
    def _handle_coordinator_update(self) -> None:
        self.async_write_ha_state()
