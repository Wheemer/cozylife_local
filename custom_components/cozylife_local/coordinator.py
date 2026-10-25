import asyncio
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .background_discovery import async_get_background_discovery
from .const import SENSOR_HUMIDITY, SENSOR_TEMPERATURE
from .cozylife_api import CozyLifeDevice
from .discovery import classify_device

_LOGGER = logging.getLogger(__name__)
_UPDATE_LOGGER = logging.getLogger(f"{__name__}.updates")
_UPDATE_LOGGER.setLevel(logging.INFO)

# These are local attempt bounds, not a claimed sensor wake or report schedule.
SENSOR_CONNECT_TIMEOUT_SECONDS = 0.2
SENSOR_WAKE_PROBE_INTERVAL_SECONDS = 0.25
ENVIRONMENT_SENSOR_MEASUREMENTS = {
    SENSOR_TEMPERATURE: "temperature",
    SENSOR_HUMIDITY: "humidity",
}


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
        # The APK keeps a socket reader alive; local reports arrive on that
        # stream rather than from an assumed report schedule.  Keep wake-window
        # probing outside DataUpdateCoordinator: it uses the configured local
        # address while UDP discovery independently refreshes that address.
        #
        # Powered device requests are gated by a matching UDP announcement,
        # as the APK's Wi-Fi sender only queues messages for devices in its
        # UDP presence cache.
        interval = None

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
        if self._uses_sensor_polling:
            # Bound a failed local attempt so a sleeping device cannot consume
            # the next half-second wake-window attempt.
            self.device.set_timeout(SENSOR_CONNECT_TIMEOUT_SECONDS)

        # The APK routes every valid local TCP frame to the device listener by
        # the socket's local address.  Do the same for powered devices as
        # well as sleeping sensors: some valid state reports are asynchronous
        # and do not carry the request sequence number we sent.
        self.device.set_message_listener(self._async_handle_device_message)
        self._entry = entry

    async def async_start_discovery(self) -> None:
        """Start local wake-window attempts for a configured sensor entry.

        The sensor firmware controls when it wakes and reports. This task only
        keeps a lightweight local status request available at a fixed host-side
        cadence; it does not infer any sensor reporting interval.
        """
        if self._uses_sensor_polling and self._sensor_probe_task is None:
            self._sensor_probe_task = self.hass.async_create_background_task(
                self._async_probe_sensor_wake_window(),
                "cozylife sensor wake-window probing",
            )

    async def _async_probe_sensor_wake_window(self) -> None:
        """Issue one serialized local status request every half second."""
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
            # The APK gates a separately queued local status action on its UDP
            # cache. Home Assistant has no app UI refresh event, so issue one
            # serialized cmd-2 status write only after a matching configured
            # device answers discovery. This preserves the local gated path
            # without a synthetic high-frequency polling loop.
            await self.device.async_send_status()
            return
        # The APK's Wi-Fi writer queues a local cmd-2 request only after the
        # device is present in its UDP cache. A matching announcement is that
        # presence signal here; refresh while it is current rather than on an
        # unrelated host-side timer.
        await self.async_request_refresh()

    @callback
    def _async_migrate_placeholder_entity_unique_ids(
        self, old_device_id: str, new_device_id: str
    ) -> None:
        """Keep entity IDs when a sleeping sensor gains its verified device ID."""
        prefix = f"{old_device_id}_"
        registry = er.async_get(self.hass)
        for registry_entry in er.async_entries_for_config_entry(
            registry, self._entry.entry_id
        ):
            old_unique_id = registry_entry.unique_id
            if not old_unique_id.startswith(prefix):
                continue

            new_unique_id = f"{new_device_id}_{old_unique_id.removeprefix(prefix)}"
            existing_entity_id = registry.async_get_entity_id(
                registry_entry.domain,
                registry_entry.platform,
                new_unique_id,
            )
            if existing_entity_id not in (None, registry_entry.entity_id):
                _LOGGER.warning(
                    "Keeping CozyLife entity %s on placeholder identity because "
                    "the verified identity is already used by %s",
                    registry_entry.entity_id,
                    existing_entity_id,
                )
                continue

            registry.async_update_entity(
                registry_entry.entity_id,
                new_unique_id=new_unique_id,
            )

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
            if old_device_id:
                self._async_migrate_placeholder_entity_unique_ids(
                    old_device_id, device_id
                )
            self.hass.config_entries.async_update_entry(self._entry, data=data)

            discovery = await async_get_background_discovery(self.hass)
            if old_device_id:
                discovery.unregister_device(old_device_id, self._entry.entry_id)
            if old_ip_address:
                discovery.unregister_address(old_ip_address)
            discovery.register_device(
                device_id,
                self._entry.entry_id,
                self.async_handle_discovered_address,
            )
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
        message = frame.get("msg", frame)
        state_data = message.get("data") if isinstance(message, dict) else None
        if not isinstance(state_data, dict):
            return

        received_state = {str(dpid): value for dpid, value in state_data.items()}
        if self._uses_sensor_polling and not self._has_valid_environment_measurements(
            received_state
        ):
            return

        # Device state reports can be partial.  The APK delivers them to the
        # device listener as updates, rather than discarding the prior state.
        state_data = {**(self.data or {}), **received_state}
        if self._uses_sensor_polling:
            state_data = self._preserve_environment_measurements(state_data)

        self.async_set_updated_data(state_data)
        if (
            self._uses_sensor_polling
            and self._entry.data.get("device_id", "").startswith("sleeping_sensor_")
            and self._identity_refresh_task is None
        ):
            self._identity_refresh_task = self.hass.async_create_background_task(
                self._async_promote_sensor_identity(),
                "cozylife sensor identity verification",
            )
        _LOGGER.debug(
            "[COZYLIFE] Received local state report from persistent socket %s: %s",
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

    async def _async_get_local_state(self) -> dict[str, Any]:
        """Request the APK state payload appropriate for this device family."""
        if self._uses_sensor_polling:
            state_data = await self.device.async_get_status()
            if state_data is None:
                raise UpdateFailed(f"Failed to query state from device {self.device.ip_address}")
            return state_data

        if not await self.device.async_request_properties():
            raise UpdateFailed(f"Failed to query state from device {self.device.ip_address}")
        # Powered-device frames are delivered asynchronously to the listener.
        # Preserve the last complete state until the next frame arrives.
        return dict(self.data or {})

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

            state_data = await self._async_get_local_state()
            if (
                getattr(self, "_entry", None)
                and self._entry.data.get("ip_address") != self.device.ip_address
            ):
                self.hass.config_entries.async_update_entry(
                    self._entry,
                    data={**self._entry.data, "ip_address": self.device.ip_address},
                )

            # The APK uses attr:[1] for sensor status and attr:[0] for powered
            # product state. Both are delivered as listener frames and may be
            # partial.
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
