import asyncio
import logging
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .background_discovery import async_get_background_discovery
from .const import (
    LIGHT_COUNTDOWN,
    PLUG_CURRENT,
    PLUG_LED_STATUS,
    PLUG_OVERCURRENT_PROTECTION,
    PLUG_POWER_ON_STATE,
    PLUG_VOLTAGE,
    SENSOR_HUMIDITY,
    SENSOR_TEMPERATURE,
)
from .cozylife_api import CozyLifeDevice
from .discovery import classify_device

_LOGGER = logging.getLogger(__name__)
_UPDATE_LOGGER = logging.getLogger(f"{__name__}.updates")
_UPDATE_LOGGER.setLevel(logging.INFO)

UPDATE_INTERVAL = timedelta(seconds=30)
SENSOR_CONNECT_TIMEOUT_SECONDS = 1.0
SENSOR_WAKE_PROBE_INTERVAL_SECONDS = 0.5
ENVIRONMENT_SENSOR_MEASUREMENTS = {
    SENSOR_TEMPERATURE: "temperature",
    SENSOR_HUMIDITY: "humidity",
}
EXTRA_SWITCH_OPTION_DPIDS = (
    PLUG_POWER_ON_STATE,
    PLUG_OVERCURRENT_PROTECTION,
    PLUG_LED_STATUS,
)
EXTRA_PLUG_METERING_DPIDS = (
    PLUG_CURRENT,
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
        # Catalog category ``Sensor`` also contains Zigbee/BLE products. Only
        # explicitly identified Wi-Fi environment sensors use local sleeping
        # sensor transport.
        self._uses_sensor_polling = self.classification.is_environment_sensor
        # The APK keeps a socket reader alive; sensor reports arrive on that
        # stream rather than from an assumed report schedule.  Keep wake-window
        # probing outside DataUpdateCoordinator: it uses the configured local
        # address while UDP discovery independently refreshes that address.
        interval = None if self._uses_sensor_polling else UPDATE_INTERVAL

        super().__init__(
            hass,
            _UPDATE_LOGGER,
            name=f"CozyLife device {device.ip_address}",
            update_interval=interval,
            config_entry=entry,
        )
        self.device = device
        self._sensor_probe_task: asyncio.Task[None] | None = None
        self._identity_refresh_task: asyncio.Task[None] | None = None
        if self._uses_sensor_polling and self._sensor_probe_task is None:
            # A waking unit answers its local TCP request immediately. Limit
            # each failed attempt so a sleeping unit cannot delay the next
            # wake-window probe.
            self.device.set_timeout(SENSOR_CONNECT_TIMEOUT_SECONDS)
            self.device.set_message_listener(self._async_handle_device_message)
        self._entry = entry

    async def async_start_discovery(self) -> None:
        """Start sensor wake probing after entry setup.

        UDP reception belongs to the integration-wide APK-style listener. The
        entry registers its DID with that listener during setup, avoiding one
        socket and one broadcast schedule per configured device.
        """
        if self._uses_sensor_polling:
            # Keep the wake-window work outside Home Assistant's startup task
            # tree, but serialize the actual connection attempts.
            self._sensor_probe_task = self.hass.async_create_background_task(
                self._async_probe_sensor_wake_window(),
                "cozylife sensor wake-window probing",
            )

    async def _async_probe_sensor_wake_window(self) -> None:
        """Keep local connection attempts present through a short wake window."""
        while True:
            started = self.hass.loop.time()
            await self.device.async_send_status()
            remaining = SENSOR_WAKE_PROBE_INTERVAL_SECONDS - (
                self.hass.loop.time() - started
            )
            if remaining > 0:
                await asyncio.sleep(remaining)

    async def async_handle_discovered_address(self, ip_address: str) -> None:
        """Use an APK discovery reply to refresh this entry's local address."""
        moved = await self.device.async_update_ip_address(ip_address)
        if moved:
            self.hass.config_entries.async_update_entry(
                self._entry,
                data={**self._entry.data, "ip_address": ip_address},
            )
        if self._uses_sensor_polling:
            # A matching discovery reply verifies or refreshes the address.
            # Send a status frame immediately; the socket reader publishes any
            # resulting asynchronous device report.
            await self.device.async_send_status()
            return
        await self.async_request_refresh()

    async def _async_promote_sensor_identity(self) -> None:
        """Replace a sleeping-sensor setup ID after a verified local handshake.

        Manual sleeping-sensor setup intentionally works while the device is
        unavailable, so it starts with an entry-local placeholder ID. A UDP
        reply contains the device's real ID and cannot match that placeholder.
        Once a report proves the device is awake, read and verify the actual
        ID over its local TCP connection before using it for UDP discovery.
        """
        try:
            expected_pid = self._entry.data.get("pid")
            if not await self.device.async_update_device_info():
                return
            if self.device.pid != expected_pid:
                _LOGGER.warning(
                    "Ignoring local identity from %s: expected PID %s, got %s",
                    self.device.ip_address,
                    expected_pid,
                    self.device.pid,
                )
                await self.device.async_close()
                cached_device_id = self._entry.data.get("device_id")
                cached_type_code = self._entry.data.get("device_type_code")
                cached_dpids = self._entry.data.get("dpids")
                if cached_device_id and cached_type_code and cached_dpids:
                    self.device.restore_from_cache(
                        cached_device_id,
                        expected_pid,
                        cached_type_code,
                        [str(dpid) for dpid in cached_dpids],
                    )
                return

            device_id = self.device.device_id
            if not device_id or device_id == self._entry.data.get("device_id"):
                return

            old_device_id = self._entry.data.get("device_id")
            old_ip_address = self._entry.data.get("ip_address")
            data = {
                **self._entry.data,
                "device_id": device_id,
                "device_type_code": self.device.device_type_code,
            }
            if self.device.dpid:
                data["dpids"] = self.device.dpid
            self.hass.config_entries.async_update_entry(self._entry, data=data)

            discovery = await async_get_background_discovery(self.hass)
            if old_device_id:
                discovery.unregister_device(old_device_id)
            if old_ip_address:
                discovery.unregister_address(old_ip_address)
            discovery.register_device(device_id, self.async_handle_discovered_address)
            _LOGGER.info(
                "Verified local device ID for sleeping CozyLife sensor at %s",
                self.device.ip_address,
            )
        finally:
            self._identity_refresh_task = None

    @property
    def is_sensor(self) -> bool:
        return self._uses_sensor_polling

    async def async_close(self) -> None:
        """Release the device reader owned by this config entry."""
        if self._sensor_probe_task:
            self._sensor_probe_task.cancel()
            try:
                await self._sensor_probe_task
            except asyncio.CancelledError:
                pass
            self._sensor_probe_task = None
        if self._identity_refresh_task:
            self._identity_refresh_task.cancel()
            try:
                await self._identity_refresh_task
            except asyncio.CancelledError:
                pass
            self._identity_refresh_task = None
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
        if (
            self._entry.data.get("device_id", "").startswith("sleeping_sensor_")
            and self._identity_refresh_task is None
        ):
            self._identity_refresh_task = self.hass.async_create_background_task(
                self._async_promote_sensor_identity(),
                "cozylife sensor identity verification",
            )
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
        """Return whether a report contains at least one usable measurement."""
        if not self.classification.is_environment_sensor:
            return True

        return any(
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
