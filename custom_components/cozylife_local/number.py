import logging
from dataclasses import dataclass

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    PERCENTAGE,
    EntityCategory,
    UnitOfTemperature,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    DOMAIN,
    LIGHT_COUNTDOWN,
    PLUG_COUNTDOWN,
    SENSOR_HUMIDITY_HIGH_DPID,
    SENSOR_HUMIDITY_LOW_DPID,
    SENSOR_HUMIDITY_SENSITIVITY_DPID,
    SENSOR_REPORT_INTERVAL_DPID,
    SENSOR_TEMP_SENSITIVITY_DPID,
    SENSOR_TEMPERATURE_HIGH_DPID,
    SENSOR_TEMPERATURE_LOW_DPID,
)
from .coordinator import CozyLifeCoordinator
from .discovery import get_model_info

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
    model_info = get_model_info(coordinator.device.pid)
    if model_info:
        dpids.update(model_info.dpids)

    if (
        PLUG_COUNTDOWN in dpids
        and coordinator.classification.supports_plug_metering
    ):
        async_add_entities([CozyLifePlugCountdownNumber(coordinator)])
        return

    if LIGHT_COUNTDOWN in dpids and coordinator.classification.is_light:
        async_add_entities([CozyLifeLightCountdownNumber(coordinator)])
        return

    if coordinator.classification.is_environment_sensor:
        sensor_config_numbers = [
            CozyLifeSensorConfigurationNumber(coordinator, description)
            for description in SENSOR_CONFIGURATION_NUMBERS
            if description.dpid in dpids
        ]
        if sensor_config_numbers:
            async_add_entities(sensor_config_numbers)
            return

    _LOGGER.debug(
        "Device %s has no supported number DPIDs, skipping number setup.",
        coordinator.device.ip_address,
    )


@dataclass(frozen=True)
class SensorConfigurationNumberDescription:
    """One APK-confirmed temperature/humidity sensor configuration field."""

    dpid: str
    key: str
    name: str
    minimum: float
    maximum: float
    step: float
    scale: int
    unit: str


# The bundled 2023120210160168 CozyLife panel sends all of these together in
# one type-2 configuration message after a change. DPID 26 is the app display
# unit preference; it has no Home Assistant control but must be preserved.
SENSOR_PANEL_CONFIGURATION_DPIDS = (
    "26",
    SENSOR_REPORT_INTERVAL_DPID,
    SENSOR_TEMP_SENSITIVITY_DPID,
    SENSOR_HUMIDITY_SENSITIVITY_DPID,
    SENSOR_TEMPERATURE_HIGH_DPID,
    SENSOR_TEMPERATURE_LOW_DPID,
    SENSOR_HUMIDITY_HIGH_DPID,
    SENSOR_HUMIDITY_LOW_DPID,
)

# The bundled 2023120210160168 CozyLife panel defines these DPIDs and ranges.
SENSOR_CONFIGURATION_NUMBERS = (
    SensorConfigurationNumberDescription(
        SENSOR_REPORT_INTERVAL_DPID, "report_interval", "Reporting Interval",
        30, 480, 30, 60, UnitOfTime.MINUTES,
    ),
    SensorConfigurationNumberDescription(
        SENSOR_TEMP_SENSITIVITY_DPID, "temperature_sensitivity", "Temperature Sensitivity",
        0.5, 3.0, 0.1, 10, UnitOfTemperature.CELSIUS,
    ),
    SensorConfigurationNumberDescription(
        SENSOR_HUMIDITY_SENSITIVITY_DPID, "humidity_sensitivity", "Humidity Sensitivity",
        5, 30, 1, 1, PERCENTAGE,
    ),
    SensorConfigurationNumberDescription(
        SENSOR_TEMPERATURE_HIGH_DPID, "temperature_upper_limit", "Temperature Upper Limit",
        -10, 110, 0.1, 10, UnitOfTemperature.CELSIUS,
    ),
    SensorConfigurationNumberDescription(
        SENSOR_TEMPERATURE_LOW_DPID, "temperature_lower_limit", "Temperature Lower Limit",
        -10, 110, 0.1, 10, UnitOfTemperature.CELSIUS,
    ),
    SensorConfigurationNumberDescription(
        SENSOR_HUMIDITY_HIGH_DPID, "humidity_upper_limit", "Humidity Upper Limit",
        0, 100, 1, 1, PERCENTAGE,
    ),
    SensorConfigurationNumberDescription(
        SENSOR_HUMIDITY_LOW_DPID, "humidity_lower_limit", "Humidity Lower Limit",
        0, 100, 1, 1, PERCENTAGE,
    ),
)


class CozyLifeSensorConfigurationNumber(
    CoordinatorEntity[CozyLifeCoordinator],
    NumberEntity,
):
    """A local control backed by a sensor-panel configuration DPID."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_mode = NumberMode.BOX

    def __init__(
        self,
        coordinator: CozyLifeCoordinator,
        description: SensorConfigurationNumberDescription,
    ) -> None:
        super().__init__(coordinator)
        self._description = description
        self._attr_name = f"{coordinator.device.device_model_name} {description.name}"
        self._attr_unique_id = f"{coordinator.device.device_id}_{description.key}"
        self._attr_native_min_value = description.minimum
        self._attr_native_max_value = description.maximum
        self._attr_native_step = description.step
        self._attr_native_unit_of_measurement = description.unit

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
            return round(int(raw) / self._description.scale, 1)
        except (TypeError, ValueError):
            return None

    async def async_set_native_value(self, value: float) -> None:
        raw_value = round(value * self._description.scale)
        payload = {
            dpid: self.coordinator.data[dpid]
            for dpid in SENSOR_PANEL_CONFIGURATION_DPIDS
            if dpid in self.coordinator.data
        }
        payload[self._description.dpid] = raw_value
        if not await self.coordinator.device.async_set_state(payload):
            _LOGGER.warning(
                "Failed to set %s for sleeping CozyLife sensor %s",
                self._description.name.lower(),
                self.coordinator.device.ip_address,
            )
            return

        updated = dict(self.coordinator.data)
        updated[self._description.dpid] = raw_value
        self.coordinator.async_set_updated_data(updated)
        if self._description.dpid == SENSOR_REPORT_INTERVAL_DPID:
            self.coordinator._effective_report_interval = raw_value
            self.coordinator._schedule_next_sensor_wake()



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
