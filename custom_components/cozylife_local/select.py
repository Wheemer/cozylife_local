import logging

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    DOMAIN,
    LIGHT_BOOT_STATUS,
    LIGHT_MUSIC_INDEX,
    LIGHT_POWER_MEMORY,
    LIGHT_RGB_TURN,
    PLUG_LED_STATUS,
    PLUG_POWER_ON_STATE,
    WORK_MODE,
)
from .coordinator import CozyLifeCoordinator
from .schedule import (
    ACTION_TURN_OFF,
    ACTION_TURN_ON,
    DEFAULT_SCHEDULE_ID,
    SCHEDULE_MANAGER,
    WEEKDAYS,
    CozyLifeScheduleManager,
)
from .switch_options import (
    supported_dpids,
    supports_light_schedule_options,
    supports_schedule_options,
)

_LOGGER = logging.getLogger(__name__)

POWER_ON_STATE_OPTIONS = {
    "Off": 0,
    "On": 1,
    "Previous state": 2,
}
POWER_ON_STATE_VALUES = {
    value: option for option, value in POWER_ON_STATE_OPTIONS.items()
}
LED_STATUS_OPTIONS = {
    "Off": 0,
    "State": 1,
    "Locator": 2,
}
LIGHT_POWER_MEMORY_OPTIONS = {
    "On": 0,
    "Previous state": 1,
}
LIGHT_POWER_MEMORY_VALUES = {
    value: option for option, value in LIGHT_POWER_MEMORY_OPTIONS.items()
}
LIGHT_RGB_TURN_OPTIONS = {
    "GR": 0,
    "RG": 1,
    "GB": 2,
    "RB": 3,
    "BR": 4,
    "BG": 5,
}
LIGHT_RGB_TURN_VALUES = {
    value: option for option, value in LIGHT_RGB_TURN_OPTIONS.items()
}
LIGHT_MUSIC_OPTIONS = {
    "Classic": 0,
    "Soft": 1,
    "Dynamic": 2,
    "Disco": 3,
}
LIGHT_MUSIC_VALUES = {
    value: option for option, value in LIGHT_MUSIC_OPTIONS.items()
}
LIGHT_BOOT_STATUS_OPTIONS = {
    "On": 0,
    "Previous state": 1,
    "Off": 2,
}
LIGHT_BOOT_STATUS_VALUES = {
    value: option for option, value in LIGHT_BOOT_STATUS_OPTIONS.items()
}
LED_STATUS_VALUES = {
    value: option for option, value in LED_STATUS_OPTIONS.items()
}
SCHEDULE_ACTION_OPTIONS = {
    "Turn off": ACTION_TURN_OFF,
    "Turn on": ACTION_TURN_ON,
}
SCHEDULE_ACTION_VALUES = {
    value: option for option, value in SCHEDULE_ACTION_OPTIONS.items()
}
SCHEDULE_REPEAT_OPTIONS = {
    "Once": [],
    "Every day": ["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
    "Weekdays": ["mon", "tue", "wed", "thu", "fri"],
    "Weekends": ["sat", "sun"],
}


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities,
):
    """Set up CozyLife select platform."""
    coordinator: CozyLifeCoordinator = hass.data[DOMAIN][config_entry.entry_id]

    if not coordinator.device.dpid:
        _LOGGER.debug(
            "Device %s has no supported select DPIDs, skipping select setup.",
            coordinator.device.ip_address,
        )
        return

    entities = []
    if coordinator.classification.supports_switch_entities:
        dpids = supported_dpids(coordinator)
        if PLUG_POWER_ON_STATE in dpids:
            entities.append(CozyLifePowerOnStateSelect(coordinator))
        if PLUG_LED_STATUS in dpids:
            entities.append(CozyLifeLedStatusSelect(coordinator))
        if supports_schedule_options(coordinator):
            entities.extend(
                [
                    CozyLifePlugScheduleActionSelect(coordinator),
                    CozyLifePlugScheduleRepeatSelect(coordinator),
                ]
            )

    if coordinator.classification.is_light:
        dpids = supported_dpids(coordinator)
        if LIGHT_POWER_MEMORY in dpids:
            entities.append(CozyLifeLightPowerMemorySelect(coordinator))
        if LIGHT_RGB_TURN in dpids:
            entities.append(CozyLifeLightRgbTurnSelect(coordinator))
        if {WORK_MODE, LIGHT_MUSIC_INDEX} <= dpids:
            entities.append(CozyLifeLightMusicSelect(coordinator))
        if LIGHT_BOOT_STATUS in dpids:
            entities.append(CozyLifeLightBootStatusSelect(coordinator))
        if supports_light_schedule_options(coordinator):
            entities.extend(
                [
                    CozyLifeLightScheduleActionSelect(coordinator),
                    CozyLifeLightScheduleRepeatSelect(coordinator),
                ]
            )

    if entities:
        async_add_entities(entities)
        return

    _LOGGER.debug(
        "Device %s has no supported select DPIDs, skipping select setup.",
        coordinator.device.ip_address,
    )


class CozyLifePowerOnStateSelect(
    CoordinatorEntity[CozyLifeCoordinator],
    SelectEntity,
):
    """Power-on restore behavior for the metering socket."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_options = tuple(POWER_ON_STATE_OPTIONS)

    def __init__(self, coordinator: CozyLifeCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_name = f"{coordinator.device.device_model_name} Power-on State"
        self._attr_unique_id = f"{coordinator.device.device_id}_power_on_state"

    @property
    def device_info(self) -> DeviceInfo:
        """Return the device info for the parent device."""
        return DeviceInfo(
            identifiers={(DOMAIN, self.coordinator.device.device_id)},
            name=self.coordinator.device.device_model_name,
            manufacturer="CozyLife",
            model=self.coordinator.device.pid,
        )

    @property
    def current_option(self) -> str | None:
        """Return the selected power-on state."""
        raw = self.coordinator.data.get(PLUG_POWER_ON_STATE)
        try:
            return POWER_ON_STATE_VALUES.get(int(raw))
        except (TypeError, ValueError):
            return None

    async def async_select_option(self, option: str) -> None:
        """Set the power-on restore behavior."""
        value = POWER_ON_STATE_OPTIONS[option]
        if await self.coordinator.device.async_set_state({PLUG_POWER_ON_STATE: value}):
            self.coordinator.data[PLUG_POWER_ON_STATE] = value
            self.async_write_ha_state()
        else:
            _LOGGER.warning(
                "Failed to set power-on state for CozyLife device %s",
                self.coordinator.device.ip_address,
            )

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self.async_write_ha_state()


class CozyLifePlugScheduleActionSelect(SelectEntity):
    """Default plug schedule action."""

    _attr_icon = "mdi:calendar-clock"
    _attr_options = tuple(SCHEDULE_ACTION_OPTIONS)
    _entity_domain = "switch"
    _unique_suffix = "schedule_action"
    _name_suffix = "Schedule Action"

    def __init__(
        self,
        coordinator: CozyLifeCoordinator,
        schedule_id: str = DEFAULT_SCHEDULE_ID,
    ) -> None:
        self.coordinator = coordinator
        self._schedule_id = schedule_id
        self._attr_name = (
            f"{coordinator.device.device_model_name} {self._name_suffix}"
        )
        self._attr_unique_id = (
            f"{coordinator.device.device_id}_{self._unique_suffix}"
        )

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self.coordinator.device.device_id)},
            name=self.coordinator.device.device_model_name,
            manufacturer="CozyLife",
            model=self.coordinator.device.pid,
        )

    @property
    def _manager(self) -> CozyLifeScheduleManager:
        return self.coordinator.hass.data[DOMAIN][SCHEDULE_MANAGER]

    @property
    def current_option(self) -> str:
        schedule = self._manager.schedule_for_coordinator(
            self.coordinator,
            self._schedule_id,
            entity_domain=self._entity_domain,
        )
        return SCHEDULE_ACTION_VALUES.get(
            schedule.get("action"),
            "Turn off",
        )

    async def async_select_option(self, option: str) -> None:
        await self._manager.async_update_coordinator_schedule(
            self.coordinator,
            self._schedule_id,
            action=SCHEDULE_ACTION_OPTIONS[option],
            entity_domain=self._entity_domain,
        )
        self.async_write_ha_state()


class CozyLifePlugScheduleRepeatSelect(SelectEntity):
    """Default plug schedule repeat preset."""

    _attr_icon = "mdi:calendar-repeat"
    _attr_options = tuple(SCHEDULE_REPEAT_OPTIONS)
    _entity_domain = "switch"
    _unique_suffix = "schedule_repeat"
    _name_suffix = "Schedule Repeat"

    def __init__(
        self,
        coordinator: CozyLifeCoordinator,
        schedule_id: str = DEFAULT_SCHEDULE_ID,
    ) -> None:
        self.coordinator = coordinator
        self._schedule_id = schedule_id
        self._attr_name = (
            f"{coordinator.device.device_model_name} {self._name_suffix}"
        )
        self._attr_unique_id = (
            f"{coordinator.device.device_id}_{self._unique_suffix}"
        )

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self.coordinator.device.device_id)},
            name=self.coordinator.device.device_model_name,
            manufacturer="CozyLife",
            model=self.coordinator.device.pid,
        )

    @property
    def _manager(self) -> CozyLifeScheduleManager:
        return self.coordinator.hass.data[DOMAIN][SCHEDULE_MANAGER]

    @property
    def current_option(self) -> str:
        schedule = self._manager.schedule_for_coordinator(
            self.coordinator,
            self._schedule_id,
            entity_domain=self._entity_domain,
        )
        repeat = sorted(schedule.get("repeat", []), key=lambda day: WEEKDAYS[day])
        for option, days in SCHEDULE_REPEAT_OPTIONS.items():
            if repeat == days:
                return option
        return "Custom"

    @property
    def options(self) -> list[str]:
        schedule = self._manager.schedule_for_coordinator(
            self.coordinator,
            self._schedule_id,
            entity_domain=self._entity_domain,
        )
        repeat = sorted(schedule.get("repeat", []), key=lambda day: WEEKDAYS[day])
        if repeat and repeat not in SCHEDULE_REPEAT_OPTIONS.values():
            return [*self._attr_options, "Custom"]
        return self._attr_options

    async def async_select_option(self, option: str) -> None:
        if option == "Custom":
            return

        await self._manager.async_update_coordinator_schedule(
            self.coordinator,
            self._schedule_id,
            repeat=list(SCHEDULE_REPEAT_OPTIONS[option]),
            entity_domain=self._entity_domain,
        )
        self.async_write_ha_state()


class CozyLifeLedStatusSelect(
    CoordinatorEntity[CozyLifeCoordinator],
    SelectEntity,
):
    """Indicator LED behavior for the metering socket."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_options = tuple(LED_STATUS_OPTIONS)

    def __init__(self, coordinator: CozyLifeCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_name = f"{coordinator.device.device_model_name} LED Status"
        self._attr_unique_id = f"{coordinator.device.device_id}_led_status"

    @property
    def device_info(self) -> DeviceInfo:
        """Return the device info for the parent device."""
        return DeviceInfo(
            identifiers={(DOMAIN, self.coordinator.device.device_id)},
            name=self.coordinator.device.device_model_name,
            manufacturer="CozyLife",
            model=self.coordinator.device.pid,
        )

    @property
    def current_option(self) -> str | None:
        """Return the selected LED behavior."""
        raw = self.coordinator.data.get(PLUG_LED_STATUS)
        try:
            return LED_STATUS_VALUES.get(int(raw))
        except (TypeError, ValueError):
            return None

    async def async_select_option(self, option: str) -> None:
        """Set the indicator LED behavior."""
        value = LED_STATUS_OPTIONS[option]
        if await self.coordinator.device.async_set_state({PLUG_LED_STATUS: value}):
            self.coordinator.data[PLUG_LED_STATUS] = value
            self.async_write_ha_state()
        else:
            _LOGGER.warning(
                "Failed to set LED status for CozyLife device %s",
                self.coordinator.device.ip_address,
            )

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self.async_write_ha_state()


class CozyLifeLightPowerMemorySelect(
    CoordinatorEntity[CozyLifeCoordinator],
    SelectEntity,
):
    """Power-memory behavior confirmed by the bundled DreamColor panel."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_options = tuple(LIGHT_POWER_MEMORY_OPTIONS)

    def __init__(self, coordinator: CozyLifeCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_name = f"{coordinator.device.device_model_name} Power-on State"
        self._attr_unique_id = f"{coordinator.device.device_id}_light_power_memory"

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self.coordinator.device.device_id)},
            name=self.coordinator.device.device_model_name,
            manufacturer="CozyLife",
            model=self.coordinator.device.pid,
        )

    @property
    def current_option(self) -> str | None:
        raw = self.coordinator.data.get(LIGHT_POWER_MEMORY)
        try:
            return LIGHT_POWER_MEMORY_VALUES.get(int(raw))
        except (TypeError, ValueError):
            return None

    async def async_select_option(self, option: str) -> None:
        value = LIGHT_POWER_MEMORY_OPTIONS[option]
        if await self.coordinator.device.async_set_state({LIGHT_POWER_MEMORY: value}):
            self.coordinator.data[LIGHT_POWER_MEMORY] = value
            self.async_write_ha_state()
        else:
            _LOGGER.warning(
                "Failed to set light power-on state for CozyLife device %s",
                self.coordinator.device.ip_address,
            )

    @callback
    def _handle_coordinator_update(self) -> None:
        self.async_write_ha_state()


class CozyLifeLightRgbTurnSelect(
    CoordinatorEntity[CozyLifeCoordinator],
    SelectEntity,
):
    """LED color-channel order defined by the bundled DreamColor panel."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_options = tuple(LIGHT_RGB_TURN_OPTIONS)
    _attr_icon = "mdi:format-color-fill"

    def __init__(self, coordinator: CozyLifeCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_name = f"{coordinator.device.device_model_name} LED Color Order"
        self._attr_unique_id = f"{coordinator.device.device_id}_light_rgb_turn"

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self.coordinator.device.device_id)},
            name=self.coordinator.device.device_model_name,
            manufacturer="CozyLife",
            model=self.coordinator.device.pid,
        )

    @property
    def current_option(self) -> str | None:
        raw = self.coordinator.data.get(LIGHT_RGB_TURN)
        try:
            return LIGHT_RGB_TURN_VALUES.get(int(raw))
        except (TypeError, ValueError):
            return None

    async def async_select_option(self, option: str) -> None:
        value = LIGHT_RGB_TURN_OPTIONS[option]
        if await self.coordinator.device.async_set_state({LIGHT_RGB_TURN: value}):
            self.coordinator.data[LIGHT_RGB_TURN] = value
            self.async_write_ha_state()
        else:
            _LOGGER.warning(
                "Failed to set LED color order for CozyLife device %s",
                self.coordinator.device.ip_address,
            )

    @callback
    def _handle_coordinator_update(self) -> None:
        self.async_write_ha_state()


class CozyLifeLightMusicSelect(
    CoordinatorEntity[CozyLifeCoordinator],
    SelectEntity,
):
    """Music effect mode from the bundled DreamColor panel."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_options = tuple(LIGHT_MUSIC_OPTIONS)
    _attr_icon = "mdi:music"

    def __init__(self, coordinator: CozyLifeCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_name = f"{coordinator.device.device_model_name} Music Effect"
        self._attr_unique_id = f"{coordinator.device.device_id}_light_music_effect"

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self.coordinator.device.device_id)},
            name=self.coordinator.device.device_model_name,
            manufacturer="CozyLife",
            model=self.coordinator.device.pid,
        )

    @property
    def current_option(self) -> str | None:
        raw = self.coordinator.data.get(LIGHT_MUSIC_INDEX)
        try:
            return LIGHT_MUSIC_VALUES.get(int(raw))
        except (TypeError, ValueError):
            return None

    async def async_select_option(self, option: str) -> None:
        value = LIGHT_MUSIC_OPTIONS[option]
        payload = {WORK_MODE: 4, LIGHT_MUSIC_INDEX: value}
        if await self.coordinator.device.async_set_state(payload):
            self.coordinator.data.update(payload)
            self.async_write_ha_state()
        else:
            _LOGGER.warning(
                "Failed to set music effect for CozyLife device %s",
                self.coordinator.device.ip_address,
            )

    @callback
    def _handle_coordinator_update(self) -> None:
        self.async_write_ha_state()


class CozyLifeLightBootStatusSelect(
    CoordinatorEntity[CozyLifeCoordinator],
    SelectEntity,
):
    """DreamColor power-on behavior defined by the bundled panel."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_options = tuple(LIGHT_BOOT_STATUS_OPTIONS)
    _attr_icon = "mdi:power-settings"

    def __init__(self, coordinator: CozyLifeCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_name = f"{coordinator.device.device_model_name} Power-on State"
        self._attr_unique_id = f"{coordinator.device.device_id}_light_boot_status"

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self.coordinator.device.device_id)},
            name=self.coordinator.device.device_model_name,
            manufacturer="CozyLife",
            model=self.coordinator.device.pid,
        )

    @property
    def current_option(self) -> str | None:
        raw = self.coordinator.data.get(LIGHT_BOOT_STATUS)
        try:
            return LIGHT_BOOT_STATUS_VALUES.get(int(raw))
        except (TypeError, ValueError):
            return None

    async def async_select_option(self, option: str) -> None:
        value = LIGHT_BOOT_STATUS_OPTIONS[option]
        if await self.coordinator.device.async_set_state({LIGHT_BOOT_STATUS: value}):
            self.coordinator.data[LIGHT_BOOT_STATUS] = value
            self.async_write_ha_state()
        else:
            _LOGGER.warning(
                "Failed to set power-on state for CozyLife device %s",
                self.coordinator.device.ip_address,
            )

    @callback
    def _handle_coordinator_update(self) -> None:
        self.async_write_ha_state()


class CozyLifeLightScheduleActionSelect(CozyLifePlugScheduleActionSelect):
    """Default light schedule action."""

    _entity_domain = "light"
    _unique_suffix = "light_schedule_action"


class CozyLifeLightScheduleRepeatSelect(CozyLifePlugScheduleRepeatSelect):
    """Default light schedule repeat preset."""

    _entity_domain = "light"
    _unique_suffix = "light_schedule_repeat"
