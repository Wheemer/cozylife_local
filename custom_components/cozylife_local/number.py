import logging
from dataclasses import dataclass

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory, UnitOfTime
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    DOMAIN,
    LIGHT_CHIP_POINT,
    LIGHT_COUNTDOWN,
    LIGHT_SENSITIVITY,
    LIGHT_SPEED,
    PLUG_COUNTDOWN,
    SENSOR_REPORT_INTERVAL_DPID,
)
from .coordinator import CozyLifeCoordinator
from .discovery import dreamcolor_led_count_bounds

_LOGGER = logging.getLogger(__name__)

MAX_COUNTDOWN_SECONDS = 24 * 60 * 60


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities,
):
    """Set up CozyLife number platform."""
    coordinator: CozyLifeCoordinator = hass.data[DOMAIN][config_entry.entry_id]
    dpids = set(coordinator.device.dpid or [])
    if PLUG_COUNTDOWN in dpids and coordinator.classification.supports_plug_metering:
        async_add_entities([CozyLifePlugCountdownNumber(coordinator)])
        return

    if coordinator.classification.is_light:
        light_numbers = []
        if LIGHT_COUNTDOWN in dpids:
            light_numbers.append(CozyLifeLightCountdownNumber(coordinator))
        light_numbers.extend(
            CozyLifeLightConfigurationNumber(coordinator, description)
            for description in LIGHT_CONFIGURATION_NUMBERS
            if description.dpid in dpids
        )
        led_count_bounds = dreamcolor_led_count_bounds(coordinator.device.pid)
        if led_count_bounds and LIGHT_CHIP_POINT in dpids:
            light_numbers.append(
                CozyLifeLightLedCountNumber(coordinator, *led_count_bounds)
            )
        if light_numbers:
            async_add_entities(light_numbers)
            return

    if (
        coordinator.classification.is_environment_sensor
        and SENSOR_REPORT_INTERVAL_DPID in dpids
    ):
        async_add_entities([CozyLifeSensorReportIntervalNumber(coordinator)])
        return

    _LOGGER.debug(
        "Device %s has no supported number DPIDs, skipping number setup.",
        coordinator.device.ip_address,
    )


# The APK writes these together when its reporting interval changes. Preserve
# any values the awake sensor supplied without exposing them as HA controls.
SENSOR_REPORT_INTERVAL_PRESERVED_DPIDS = (
    "26",
    "14",
    "25",
    "24",
    "20",
    "21",
    "22",
    "23",
)


class CozyLifeSensorReportIntervalNumber(
    CoordinatorEntity[CozyLifeCoordinator],
    NumberEntity,
):
    """The sensor's APK-confirmed reporting interval, in minutes."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_mode = NumberMode.BOX
    _attr_native_min_value = 30
    _attr_native_max_value = 480
    _attr_native_step = 30
    _attr_native_unit_of_measurement = UnitOfTime.MINUTES

    def __init__(self, coordinator: CozyLifeCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_name = f"{coordinator.device.device_model_name} Reporting Interval"
        self._attr_unique_id = f"{coordinator.device.device_id}_reporting_interval"

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self.coordinator.device.device_id)},
            name=self.coordinator.device.device_model_name,
            manufacturer="CozyLife",
            model=self.coordinator.device.pid,
        )

    @property
    def native_value(self) -> float | None:
        try:
            return int(self.coordinator.data[SENSOR_REPORT_INTERVAL_DPID]) / 60
        except (KeyError, TypeError, ValueError):
            return None

    async def async_set_native_value(self, value: float) -> None:
        raw_value = round(value * 60)
        payload = {
            dpid: self.coordinator.data[dpid]
            for dpid in SENSOR_REPORT_INTERVAL_PRESERVED_DPIDS
            if dpid in self.coordinator.data
        }
        payload[SENSOR_REPORT_INTERVAL_DPID] = raw_value
        if not await self.coordinator.device.async_set_state(payload):
            _LOGGER.warning(
                "Failed to set reporting interval for sleeping CozyLife sensor %s",
                self.coordinator.device.ip_address,
            )
            return

        updated = dict(self.coordinator.data)
        updated[SENSOR_REPORT_INTERVAL_DPID] = raw_value
        self.coordinator.async_set_updated_data(updated)
        self.coordinator._effective_report_interval = raw_value
        self.coordinator._observed_wake_intervals = []
        self.coordinator._schedule_next_sensor_wake()


@dataclass(frozen=True)
class LightConfigurationNumberDescription:
    """One numeric DreamColor light panel setting."""

    dpid: str
    key: str
    name: str
    minimum: float
    maximum: float
    step: float
    scale: int
    icon: str


# DreamColor panel 2022011014060070: DPID 8 stores one tenth of the visible
# 1-100 percent speed, while DPID 10 is a 1-10 microphone sensitivity value.
LIGHT_CONFIGURATION_NUMBERS = (
    LightConfigurationNumberDescription(
        LIGHT_SPEED, "effect_speed", "Effect Speed", 1, 100, 1, 10, "mdi:speedometer"
    ),
    LightConfigurationNumberDescription(
        LIGHT_SENSITIVITY,
        "microphone_sensitivity",
        "Microphone Sensitivity",
        1,
        10,
        1,
        1,
        "mdi:microphone",
    ),
)


class CozyLifeLightConfigurationNumber(
    CoordinatorEntity[CozyLifeCoordinator],
    NumberEntity,
):
    """A source-backed numeric DreamColor light setting."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_mode = NumberMode.SLIDER

    def __init__(
        self,
        coordinator: CozyLifeCoordinator,
        description: LightConfigurationNumberDescription,
    ) -> None:
        super().__init__(coordinator)
        self._description = description
        self._attr_name = f"{coordinator.device.device_model_name} {description.name}"
        self._attr_unique_id = f"{coordinator.device.device_id}_{description.key}"
        self._attr_icon = description.icon
        self._attr_native_min_value = description.minimum
        self._attr_native_max_value = description.maximum
        self._attr_native_step = description.step

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self.coordinator.device.device_id)},
            name=self.coordinator.device.device_model_name,
            manufacturer="CozyLife",
            model=self.coordinator.device.pid,
        )

    @property
    def native_value(self) -> float | None:
        raw = self.coordinator.data.get(self._description.dpid)
        try:
            return int(raw) / self._description.scale
        except (TypeError, ValueError):
            return None

    async def async_set_native_value(self, value: float) -> None:
        raw_value = round(value * self._description.scale)
        if not await self.coordinator.device.async_set_state(
            {self._description.dpid: raw_value}
        ):
            _LOGGER.warning(
                "Failed to set %s for CozyLife light %s",
                self._description.name.lower(),
                self.coordinator.device.ip_address,
            )
            return

        updated = dict(self.coordinator.data)
        updated[self._description.dpid] = raw_value
        self.coordinator.async_set_updated_data(updated)


class CozyLifeLightLedCountNumber(CozyLifeLightConfigurationNumber):
    """The default DreamColor panel's configurable LED-count field."""

    def __init__(
        self, coordinator: CozyLifeCoordinator, minimum: int, maximum: int
    ) -> None:
        super().__init__(
            coordinator,
            LightConfigurationNumberDescription(
                LIGHT_CHIP_POINT,
                "led_count",
                "LED Count",
                minimum,
                maximum,
                1,
                1,
                "mdi:led-strip-variant",
            ),
        )


class CozyLifeCountdownNumberBase(
    CoordinatorEntity[CozyLifeCoordinator],
    NumberEntity,
):
    """Countdown/timer control for CozyLife devices."""

    _attr_icon = "mdi:timer-outline"
    _attr_mode = NumberMode.BOX
    _attr_native_min_value = 0
    _attr_native_max_value = MAX_COUNTDOWN_SECONDS
    _attr_native_step = 1
    _attr_native_unit_of_measurement = UnitOfTime.SECONDS

    _countdown_dpid: str
    _device_kind: str

    def __init__(
        self,
        coordinator: CozyLifeCoordinator,
        *,
        dpid: str,
        unique_suffix: str,
        device_kind: str,
    ) -> None:
        super().__init__(coordinator)
        self._countdown_dpid = dpid
        self._device_kind = device_kind
        self._attr_name = f"{coordinator.device.device_model_name} Countdown"
        self._attr_unique_id = f"{coordinator.device.device_id}_{unique_suffix}"

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self.coordinator.device.device_id)},
            name=self.coordinator.device.device_model_name,
            manufacturer="CozyLife",
            model=self.coordinator.device.pid,
        )

    @property
    def native_value(self) -> int | None:
        raw = self.coordinator.data.get(self._countdown_dpid)
        if raw is None:
            return None

        try:
            value = int(raw)
        except (TypeError, ValueError):
            return None

        return max(0, min(MAX_COUNTDOWN_SECONDS, value))

    async def async_set_native_value(self, value: float) -> None:
        countdown = max(0, min(MAX_COUNTDOWN_SECONDS, int(value)))
        payload = {self._countdown_dpid: countdown}

        if await self.coordinator.device.async_set_state(payload):
            self.coordinator.data[self._countdown_dpid] = countdown
            await self._async_after_countdown_set(countdown)
            self.async_write_ha_state()
        else:
            _LOGGER.warning(
                "Failed to set %s countdown for CozyLife device %s to %s seconds",
                self._device_kind,
                self.coordinator.device.ip_address,
                countdown,
            )

    async def _async_after_countdown_set(self, countdown: int) -> None:
        """Handle additional behavior after countdown writes."""

    @callback
    def _handle_coordinator_update(self) -> None:
        self.async_write_ha_state()


class CozyLifePlugCountdownNumber(CozyLifeCountdownNumberBase):
    """Countdown/timer control for CozyLife metered smart plugs."""

    def __init__(self, coordinator: CozyLifeCoordinator) -> None:
        super().__init__(
            coordinator,
            dpid=PLUG_COUNTDOWN,
            unique_suffix="countdown",
            device_kind="plug",
        )


class CozyLifeLightCountdownNumber(CozyLifeCountdownNumberBase):
    """Countdown/timer control for CozyLife lights."""

    def __init__(self, coordinator: CozyLifeCoordinator) -> None:
        super().__init__(
            coordinator,
            dpid=LIGHT_COUNTDOWN,
            unique_suffix="countdown",
            device_kind="light",
        )
