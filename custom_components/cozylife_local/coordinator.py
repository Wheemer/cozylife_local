import asyncio
import logging
from datetime import timedelta
from typing import Any, Callable

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import EVENT_HOMEASSISTANT_STARTED, Event, HomeAssistant, callback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

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
    SENSOR_TEMPERATURE,
)
from .cozylife_api import CozyLifeDevice, CozyLifeUdpDiscovery
from .discovery import classify_device

_LOGGER = logging.getLogger(__name__)
_UPDATE_LOGGER = logging.getLogger(f"{__name__}.updates")
_UPDATE_LOGGER.setLevel(logging.INFO)

UPDATE_INTERVAL = timedelta(seconds=30)
SENSOR_DISCOVERY_INTERVAL_SECONDS = 10.0
SENSOR_CONNECT_TIMEOUT_SECONDS = 1.0
SENSOR_WAKE_PROBE_INTERVAL_SECONDS = 0.25
SENSOR_WAKE_PROBE_CONCURRENCY = 4
DEVICE_DISCOVERY_INTERVAL_SECONDS = 30.0
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
        # The APK keeps a socket reader alive; sensor reports arrive on that
        # stream rather than through guessed wake-window polling.
        # Sensor updates are delivered by the local socket reader after a
        # matching UDP discovery reply.  A DataUpdateCoordinator interval
        # would call ``_async_update_data`` directly and bypass the APK's
        # discovery gate.
        interval = None if self._uses_sensor_polling else UPDATE_INTERVAL

        super().__init__(
            hass,
            _UPDATE_LOGGER,
            name=f"CozyLife device {device.ip_address}",
            update_interval=interval,
            config_entry=entry,
        )
        self.device = device
        self._udp_discovery: CozyLifeUdpDiscovery | None = None
        self._sensor_probe_task: asyncio.Task[None] | None = None
        self._sensor_probe_requests: set[asyncio.Task[bool]] = set()
        self._sensor_start_unsub: Callable[[], None] | None = None
        self._sensor_probes_started = False
        if self._uses_sensor_polling:
            # The APK's TCP_GLOAL_CONNECT_TIMEOUT is 1000 ms.  Keep that
            # connection deadline independent from the probe cadence: a
            # 250 ms deadline can abort the TCP handshake before a waking
            # device is able to accept the request.
            self.device.set_timeout(SENSOR_CONNECT_TIMEOUT_SECONDS)
            self.device.set_message_listener(self._async_handle_device_message)
        self._entry = entry

    async def async_start_discovery(self) -> None:
        """Start local UDP address discovery after cached device info loads."""
        if not self.device.device_id or self._udp_discovery:
            return
        interval = (
            SENSOR_DISCOVERY_INTERVAL_SECONDS
            if self._uses_sensor_polling
            else DEVICE_DISCOVERY_INTERVAL_SECONDS
        )
        self._udp_discovery = CozyLifeUdpDiscovery(
            self.device.device_id,
            self._async_handle_discovered_address,
            interval,
        )
        await self._udp_discovery.async_start()
        if self._uses_sensor_polling:
            # Do not create pending TCP connection tasks during Home
            # Assistant's bootstrap. A sleeping sensor can legitimately
            # leave several 1-second connection attempts in flight; those
            # tasks must never be inherited by the startup task tree.
            if self.hass.is_running:
                self._async_start_sensor_probes()
            else:
                self._sensor_start_unsub = self.hass.bus.async_listen_once(
                    EVENT_HOMEASSISTANT_STARTED,
                    self._async_start_sensor_probes,
                )

    @callback
    def _async_start_sensor_probes(self, _event: Event | None = None) -> None:
        """Start wake-window probing only after Home Assistant is running."""
        self._sensor_start_unsub = None
        if self._sensor_probe_task is not None:
            return
        self._sensor_probes_started = True
        self._sensor_probe_task = self.hass.async_create_background_task(
            self._async_probe_sensor_wake_window(),
            "cozylife sensor wake-window probing",
        )

    async def _async_probe_sensor_wake_window(self) -> None:
        """Keep local connection attempts present through a short wake window."""
        while True:
            if len(self._sensor_probe_requests) < SENSOR_WAKE_PROBE_CONCURRENCY:
                request = self.hass.async_create_task(self.device.async_send_status())
                self._sensor_probe_requests.add(request)
                request.add_done_callback(self._sensor_probe_requests.discard)
            await asyncio.sleep(SENSOR_WAKE_PROBE_INTERVAL_SECONDS)

    async def _async_handle_discovered_address(self, ip_address: str) -> None:
        """Use an APK discovery reply to refresh this entry's local address."""
        moved = await self.device.async_update_ip_address(ip_address)
        if moved:
            self.hass.config_entries.async_update_entry(
                self._entry,
                data={**self._entry.data, "ip_address": ip_address},
            )
        if self._uses_sensor_polling:
            # The APK only queues its local ``attr:[1]`` status message after
            # a matching UDP discovery reply has populated its online cache.
            # It does not synchronously await that frame; the socket reader
            # publishes the resulting device report.
            if self._sensor_probes_started:
                await self.device.async_send_status()
            return
        await self.async_request_refresh()

    @property
    def is_sensor(self) -> bool:
        return self._uses_sensor_polling

    async def async_close(self) -> None:
        """Release the device reader owned by this config entry."""
        if self._sensor_start_unsub:
            self._sensor_start_unsub()
            self._sensor_start_unsub = None
        if self._udp_discovery:
            await self._udp_discovery.async_stop()
            self._udp_discovery = None
        if self._sensor_probe_task:
            self._sensor_probe_task.cancel()
            try:
                await self._sensor_probe_task
            except asyncio.CancelledError:
                pass
            self._sensor_probe_task = None
        for request in self._sensor_probe_requests:
            request.cancel()
        self._sensor_probe_requests.clear()
        await self.device.async_close()

    async def _async_handle_device_message(self, frame: dict[str, Any]) -> None:
        """Publish a sensor report received by the persistent device reader."""
        if not self._uses_sensor_polling:
            return
        message = frame.get("msg", frame)
        state_data = message.get("data") if isinstance(message, dict) else None
        if not isinstance(state_data, dict):
            return

        received_state = {str(dpid): value for dpid, value in state_data.items()}
        if not self._has_valid_environment_measurements(received_state):
            return
        state_data = self._preserve_environment_measurements(
            {**(self.data or {}), **received_state}
        )

        self.async_set_updated_data(state_data)
        _LOGGER.debug(
            "[COZYLIFE] Received sensor report from persistent socket %s: %s",
            self.device.ip_address,
            received_state,
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
        """Request state with the APK's local device-status payload."""
        state_data = await self.device.async_get_status()
        if state_data is None:
            raise UpdateFailed(f"Failed to query state from device {self.device.ip_address}")
        return state_data

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
            state_data = self._preserve_environment_measurements(state_data)
            _LOGGER.debug(f"[COZYLIFE] Successfully fetched state for {self.device.ip_address}: {state_data}")
            return state_data

        except UpdateFailed:
            # For sleeping sensors: if we have previous data, return it to keep entities available.
            # The device only wakes periodically so connection failures are expected.
            if self._uses_sensor_polling and self.data is not None:
                return self.data
            raise

        except Exception as err:
            _LOGGER.error(f"Error communicating with device {self.device.ip_address}: {err}")
            raise UpdateFailed(f"Error communicating with device {self.device.ip_address}: {err}") from err
