import asyncio
import logging
import math
import statistics
import time
from datetime import datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .const import (
    LIGHT_COUNTDOWN,
    PLUG_CURRENT,
    PLUG_LED_STATUS,
    PLUG_OVERCURRENT_PROTECTION,
    PLUG_POWER_ON_STATE,
    PLUG_TIMER_SCHEDULE,
    PLUG_TIMER_STATUS,
    PLUG_VOLTAGE,
    SENSOR_HUMIDITY,
    SENSOR_REPORT_INTERVAL_DPID,
    SENSOR_TEMPERATURE,
)
from .cozylife_api import CozyLifeDevice
from .discovery import classify_device

_LOGGER = logging.getLogger(__name__)
_UPDATE_LOGGER = logging.getLogger(f"{__name__}.updates")
_UPDATE_LOGGER.setLevel(logging.INFO)

UPDATE_INTERVAL = timedelta(seconds=30)
SENSOR_WAKE_WINDOW_LEAD = 10
SENSOR_CATCH_POLL_INTERVAL = 1
# The APK sets a 1000 ms TCP connect timeout. Use that same one-second
# bound for a fresh local probe while acquiring or recovering a sensor wake.
SENSOR_CATCH_REQUEST_TIMEOUT = 1
SENSOR_CATCH_TIMEOUT = 120
SENSOR_WAKE_SAMPLE_SECONDS = 4
SENSOR_WAKE_SAMPLE_DELAY = 0.15
SENSOR_WAKE_INTERVAL_SAMPLE_LIMIT = 5
SENSOR_WAKE_INTERVAL_MIN_RATIO = 0.75
SENSOR_WAKE_INTERVAL_MAX_RATIO = 1.25
ENVIRONMENT_SENSOR_MEASUREMENTS = {
    SENSOR_TEMPERATURE: "temperature",
    SENSOR_HUMIDITY: "humidity",
}
EXTRA_SWITCH_OPTION_DPIDS = (
    PLUG_TIMER_SCHEDULE,
    PLUG_POWER_ON_STATE,
    PLUG_OVERCURRENT_PROTECTION,
    PLUG_LED_STATUS,
)
EXTRA_PLUG_METERING_DPIDS = (
    PLUG_CURRENT,
    PLUG_TIMER_STATUS,
    PLUG_VOLTAGE,
)


def _decode_sensor_report_interval(value: Any) -> int | None:
    """Decode the vendor temperature/humidity reporting setting."""
    try:
        raw_value = int(value)
    except (TypeError, ValueError):
        return None

    # The bundled CozyLife sensor panel reads this field directly as seconds.
    # It writes 30-minute choices as 1800..28800.
    # Other catalogued sensor models declare longer values (for example 86400),
    # so retain every positive raw seconds value rather than remapping it.
    return raw_value if raw_value > 0 else None


def _next_sensor_wake_delay(report_interval: int) -> int:
    """Return how long to wait before polling near the next expected wake."""
    return max(SENSOR_CATCH_POLL_INTERVAL, report_interval - SENSOR_WAKE_WINDOW_LEAD)


def _next_sensor_cycle_delay(
    last_response_at: datetime,
    report_interval: int,
    now: datetime,
) -> int:
    """Return delay to the next expected wake window from a previous response."""
    elapsed = max(0, (now - last_response_at).total_seconds())
    cycle = math.floor((elapsed + SENSOR_WAKE_WINDOW_LEAD) / report_interval) + 1
    next_window_elapsed = (cycle * report_interval) - SENSOR_WAKE_WINDOW_LEAD
    return max(
        SENSOR_CATCH_POLL_INTERVAL,
        int(next_window_elapsed - elapsed),
    )


def _scheduled_sensor_wake_interval(
    report_interval: int,
    observed_intervals: list[float],
) -> int:
    """Return a sensor's measured wake period, or its reported fallback."""
    valid_samples = [
        interval
        for interval in observed_intervals
        if (
            report_interval * SENSOR_WAKE_INTERVAL_MIN_RATIO
            <= interval
            <= report_interval * SENSOR_WAKE_INTERVAL_MAX_RATIO
        )
    ]
    if not valid_samples:
        return report_interval
    return round(statistics.median(valid_samples))


def _is_valid_environment_measurement(
    dpid: str,
    value: Any,
    *,
    zero_is_placeholder: bool,
) -> bool:
    """Return whether a temperature/humidity reading looks like a real report."""
    if not isinstance(value, (int, float)):
        return False

    # Sleeping environment sensors can intermittently report 0 while waking.
    # Treat that as a placeholder so Home Assistant does not record false spikes.
    if zero_is_placeholder and value == 0:
        return False

    if dpid == SENSOR_TEMPERATURE:
        return -400 <= value <= 800

    if dpid == SENSOR_HUMIDITY:
        return 0 < value <= 100

    return True


class CozyLifeCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Manages fetching data from a single CozyLife device."""

    def __init__(self, hass: HomeAssistant, device: CozyLifeDevice, entry: ConfigEntry):
        """Initialize coordinator."""
        self.classification = classify_device(
            device.pid,
            device.device_type_code,
            device.dpid,
        )
        self._uses_sensor_polling = (
            self.classification.is_sensor_category
            or self.classification.is_environment_sensor
        )
        interval = (
            timedelta(seconds=SENSOR_CATCH_POLL_INTERVAL)
            if self._uses_sensor_polling
            else UPDATE_INTERVAL
        )

        super().__init__(
            hass,
            _UPDATE_LOGGER,
            name=f"CozyLife device {device.ip_address}",
            update_interval=interval,
            config_entry=entry,
        )
        self.device = device
        if self._uses_sensor_polling:
            # Each short sensor wake must receive a new local request; keep the
            # full connect-and-read attempt within the APK's one-second budget.
            self.device.set_timeout(SENSOR_CATCH_REQUEST_TIMEOUT)
        self._entry = entry
        self._effective_report_interval: int | None = None
        self._observed_wake_intervals: list[float] = []
        self._missed_expected_wake_logged = False
        self._catch_started_at: datetime | None = None
        self._fallback_logged = False
        self._last_sensor_response_at: datetime | None = None
        self._current_sensor_response_at: datetime | None = None
        self._first_success_wait_logged = False
        if self.classification.is_environment_sensor:
            _LOGGER.debug(
                "[COZYLIFE] Sensor %s starts catch polling every %ss until "
                "it reports its own interval.",
                device.ip_address,
                SENSOR_CATCH_POLL_INTERVAL,
            )

    @property
    def is_sensor(self) -> bool:
        return self._uses_sensor_polling

    def _schedule_next_sensor_wake(self) -> None:
        """Keep probing a sleeping sensor with fresh, short local requests."""
        if not self._uses_sensor_polling:
            return
        now = dt_util.utcnow()
        response_at = self._current_sensor_response_at or now
        previous_response_at = getattr(self, "_last_sensor_response_at", None)
        self._last_sensor_response_at = response_at
        if self._effective_report_interval is None:
            # No vendor interval was supplied. Keep catching rather than making
            # up a cadence and missing an unknown wake window.
            self.update_interval = timedelta(seconds=SENSOR_CATCH_POLL_INTERVAL)
            self._current_sensor_response_at = None
            return

        if previous_response_at is not None:
            observed_interval = (response_at - previous_response_at).total_seconds()
            if (
                self._effective_report_interval * SENSOR_WAKE_INTERVAL_MIN_RATIO
                <= observed_interval
                <= self._effective_report_interval * SENSOR_WAKE_INTERVAL_MAX_RATIO
            ):
                self._observed_wake_intervals.append(observed_interval)
                del self._observed_wake_intervals[:-SENSOR_WAKE_INTERVAL_SAMPLE_LIMIT]

        wake_interval = _scheduled_sensor_wake_interval(
            self._effective_report_interval,
            self._observed_wake_intervals,
        )
        delay = _next_sensor_cycle_delay(response_at, wake_interval, now)
        self.update_interval = timedelta(seconds=delay)
        self._missed_expected_wake_logged = False
        self._catch_started_at = None
        self._fallback_logged = False
        self._first_success_wait_logged = False
        self._current_sensor_response_at = None
        _LOGGER.debug(
            "[COZYLIFE] Sensor %s responded; next poll window starts in %ss "
            "(reported interval: %ss; scheduled interval: %ss)",
            self.device.ip_address,
            delay,
            self._effective_report_interval,
            wake_interval,
        )

    def _schedule_sensor_catch_polling(self) -> None:
        """Poll frequently while waiting for the sleeping sensor to respond."""
        if not self._uses_sensor_polling:
            return

        now = dt_util.utcnow()
        if self._catch_started_at is None:
            self._catch_started_at = now

        catch_seconds = (now - self._catch_started_at).total_seconds()
        if catch_seconds >= SENSOR_CATCH_TIMEOUT:
            if self._last_sensor_response_at is None:
                self.update_interval = timedelta(seconds=SENSOR_CATCH_POLL_INTERVAL)
                if not self._first_success_wait_logged:
                    self._first_success_wait_logged = True
                    _LOGGER.debug(
                        "[COZYLIFE] Sensor %s has not responded yet; continuing "
                        "first-acquisition polling every %ss so the short wake "
                        "window is not missed.",
                        self.device.ip_address,
                        SENSOR_CATCH_POLL_INTERVAL,
                    )
                return

            if self._effective_report_interval is None:
                self.update_interval = timedelta(seconds=SENSOR_CATCH_POLL_INTERVAL)
                return
            wake_interval = _scheduled_sensor_wake_interval(
                self._effective_report_interval,
                self._observed_wake_intervals,
            )
            delay = _next_sensor_cycle_delay(
                self._last_sensor_response_at,
                wake_interval,
                now,
            )
            self.update_interval = timedelta(seconds=delay)
            self._catch_started_at = None
            if not self._fallback_logged:
                self._fallback_logged = True
                _LOGGER.debug(
                    "[COZYLIFE] Sensor %s did not respond within %ss of the "
                    "expected wake window; next poll window starts in %ss "
                    "based on scheduled interval=%ss (reported interval=%ss).",
                    self.device.ip_address,
                    SENSOR_CATCH_TIMEOUT,
                    delay,
                    wake_interval,
                    self._effective_report_interval,
                )
            return

        self.update_interval = timedelta(seconds=SENSOR_CATCH_POLL_INTERVAL)
        if not self._missed_expected_wake_logged:
            self._missed_expected_wake_logged = True
            _LOGGER.debug(
                "[COZYLIFE] Sensor %s missed the expected wake window; "
                "catch polling every %ss",
                self.device.ip_address,
                SENSOR_CATCH_POLL_INTERVAL,
            )

    def _preserve_environment_measurements(
        self,
        state_data: dict[str, Any],
    ) -> dict[str, Any]:
        """Keep the last good temp/humidity values across placeholder reports."""
        if not self.classification.is_environment_sensor:
            return state_data

        previous_data = self.data or {}
        sanitized = dict(state_data)
        for dpid, name in ENVIRONMENT_SENSOR_MEASUREMENTS.items():
            current = sanitized.get(dpid)
            previous = previous_data.get(dpid)
            if _is_valid_environment_measurement(
                dpid,
                current,
                zero_is_placeholder=True,
            ):
                continue

            if _is_valid_environment_measurement(
                dpid,
                previous,
                zero_is_placeholder=True,
            ):
                sanitized[dpid] = previous
                _LOGGER.debug(
                    "[COZYLIFE] Ignoring invalid %s reading from %s: %r; "
                    "preserving previous value %r",
                    name,
                    self.device.ip_address,
                    current,
                    previous,
                )
            elif dpid in sanitized:
                sanitized.pop(dpid)
                _LOGGER.debug(
                    "[COZYLIFE] Ignoring invalid %s reading from %s: %r",
                    name,
                    self.device.ip_address,
                    current,
                )

        return sanitized

    def _has_valid_environment_measurements(self, state_data: dict[str, Any]) -> bool:
        """Return whether state data contains usable temperature and humidity."""
        if not self.classification.is_environment_sensor:
            return True

        return all(
            _is_valid_environment_measurement(
                dpid,
                state_data.get(dpid),
                zero_is_placeholder=True,
            )
            for dpid in ENVIRONMENT_SENSOR_MEASUREMENTS
        )

    async def _async_get_environment_state(self) -> dict[str, Any]:
        """Sample a waking environment sensor until real measurements appear."""
        state_data = await self.device.async_get_state()
        if state_data is None:
            raise UpdateFailed(f"Failed to query state from device {self.device.ip_address}")

        if self._uses_sensor_polling and self._current_sensor_response_at is None:
            self._current_sensor_response_at = dt_util.utcnow()

        if not self.classification.is_environment_sensor:
            return state_data

        if self._has_valid_environment_measurements(state_data):
            return state_data

        end_time = time.monotonic() + SENSOR_WAKE_SAMPLE_SECONDS
        best_state = state_data
        while time.monotonic() < end_time:
            await asyncio.sleep(SENSOR_WAKE_SAMPLE_DELAY)
            next_state = await self.device.async_get_state()
            if next_state is None:
                continue

            best_state = {**best_state, **next_state}
            if self._has_valid_environment_measurements(best_state):
                _LOGGER.debug(
                    "[COZYLIFE] Sensor %s produced valid environment "
                    "measurements during wake sampling: %s",
                    self.device.ip_address,
                    best_state,
                )
                return best_state

        _LOGGER.debug(
            "[COZYLIFE] Sensor %s wake sampling ended without valid "
            "temperature/humidity; using best state: %s",
            self.device.ip_address,
            best_state,
        )
        return best_state

    async def _async_add_hidden_switch_state(
        self,
        state_data: dict[str, Any],
    ) -> dict[str, Any]:
        """Merge switch option DPIDs that are omitted from default state."""
        if not self.classification.supports_switch_entities:
            return state_data

        known_dpids = set(self.device.dpid or [])

        candidate_dpids = set(EXTRA_SWITCH_OPTION_DPIDS)
        if self.classification.supports_plug_metering:
            candidate_dpids.update(EXTRA_PLUG_METERING_DPIDS)

        missing_dpids = [
            dpid
            for dpid in candidate_dpids
            if dpid in known_dpids and dpid not in state_data
        ]
        if not missing_dpids:
            return state_data

        hidden_state = await self.device.async_get_state(missing_dpids)
        if hidden_state is None:
            _LOGGER.debug(
                "[COZYLIFE] Failed to query hidden switch DPIDs %s from %s",
                missing_dpids,
                self.device.ip_address,
            )
            return state_data

        return {**state_data, **hidden_state}

    async def _async_add_hidden_light_state(
        self,
        state_data: dict[str, Any],
    ) -> dict[str, Any]:
        """Merge safe light countdown state omitted from default state."""
        if not self.classification.is_light or LIGHT_COUNTDOWN in state_data:
            return state_data

        known_dpids = set(self.device.dpid or [])

        if LIGHT_COUNTDOWN not in known_dpids:
            return state_data

        hidden_state = await self.device.async_get_state([LIGHT_COUNTDOWN])
        if hidden_state is None:
            _LOGGER.debug(
                "[COZYLIFE] Failed to query hidden light countdown DPID %s from %s",
                LIGHT_COUNTDOWN,
                self.device.ip_address,
            )
            return state_data

        return {**state_data, **hidden_state}

    async def _async_add_hidden_sensor_state(
        self,
        state_data: dict[str, Any],
    ) -> dict[str, Any]:
        """Query the report interval when an awake sensor omits it from status."""
        if (
            not self.classification.is_environment_sensor
            or SENSOR_REPORT_INTERVAL_DPID in state_data
            or SENSOR_REPORT_INTERVAL_DPID not in set(self.device.dpid or [])
        ):
            return state_data

        hidden_state = await self.device.async_get_state([SENSOR_REPORT_INTERVAL_DPID])
        if hidden_state is None:
            _LOGGER.debug(
                "[COZYLIFE] Failed to query sensor report interval DPID %s from %s",
                SENSOR_REPORT_INTERVAL_DPID,
                self.device.ip_address,
            )
            return state_data

        return {**state_data, **hidden_state}

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch data from device."""
        try:
            if (
                (not self.device.device_id or not self.device.dpid)
                and not await self.device.async_update_device_info()
            ):
                raise UpdateFailed(
                    f"Failed to get full device info for {self.device.ip_address}"
                )

            state_data = await self._async_get_environment_state()
            if (
                getattr(self, "_entry", None)
                and self._entry.data.get("ip_address") != self.device.ip_address
            ):
                self.hass.config_entries.async_update_entry(
                    self._entry,
                    data={**self._entry.data, "ip_address": self.device.ip_address},
                )

            state_data = await self._async_add_hidden_switch_state(state_data)
            state_data = await self._async_add_hidden_light_state(state_data)
            state_data = await self._async_add_hidden_sensor_state(state_data)

            # The vendor sensor panel stores DPID 14 as raw seconds. Only a
            # value the device reports may set the expected wake cadence.
            if self.classification.is_environment_sensor:
                reported_interval = _decode_sensor_report_interval(
                    state_data.get(SENSOR_REPORT_INTERVAL_DPID)
                )
                if reported_interval != self._effective_report_interval:
                    self._observed_wake_intervals.clear()
                self._effective_report_interval = reported_interval

            state_data = self._preserve_environment_measurements(state_data)
            self._schedule_next_sensor_wake()
            _LOGGER.debug(f"[COZYLIFE] Successfully fetched state for {self.device.ip_address}: {state_data}")
            return state_data

        except UpdateFailed:
            # For sleeping sensors: if we have previous data, return it to keep entities available.
            # The device only wakes periodically so connection failures are expected.
            if self._uses_sensor_polling and self.data is not None:
                self._schedule_sensor_catch_polling()
                return self.data if self.data else {}
            raise

        except Exception as err:
            _LOGGER.error(f"Error communicating with device {self.device.ip_address}: {err}")
            raise UpdateFailed(f"Error communicating with device {self.device.ip_address}: {err}") from err
