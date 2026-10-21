"""APK-derived, integration-wide Cozy Life UDP discovery."""

from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from homeassistant import config_entries
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .cozylife_api import COZYLIFE_UDP_DISCOVERY_PORT, _get_sn

_LOGGER = logging.getLogger(__name__)

DATA_DISCOVERY_SERVICE = "background_udp_discovery"
APK_DAEMON_INTERVAL_SECONDS = 10.0
APK_NETWORK_CHANGE_BROADCAST_COUNT = 17
CONFIG_FLOW_DISCOVERY_WINDOW_SECONDS = 5.0


@dataclass(frozen=True)
class UdpDiscoveredDevice:
    """Identity advertised by a Cozy Life UDP/6095 reply."""

    device_id: str
    ip_address: str
    pid: str | None
    device_type_code: str | None


class _ApkUdpDiscoveryProtocol(asyncio.DatagramProtocol):
    """Parse the UDP reply shape cached by the Cozy Life APK."""

    def __init__(self, found: Callable[[UdpDiscoveredDevice], None]) -> None:
        self._found = found

    def datagram_received(self, data: bytes, _addr: tuple[str, int]) -> None:
        """Pass a valid DID/IP announcement to the shared cache."""
        try:
            frame = json.loads(data.decode("utf-8").split("\r\n", 1)[0])
            message = frame["msg"]
            device_id = message["did"]
            ip_address = message["ip"]
            if not isinstance(device_id, str) or not device_id:
                return
            if not isinstance(ip_address, str):
                return
            ipaddress.IPv4Address(ip_address)
        except (KeyError, TypeError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
            return

        pid = message.get("pid")
        device_type_code = message.get("dtp")
        self._found(
            UdpDiscoveredDevice(
                device_id=device_id,
                ip_address=ip_address,
                pid=pid if isinstance(pid, str) else None,
                device_type_code=(
                    device_type_code if isinstance(device_type_code, str) else None
                ),
            )
        )


class CozyLifeBackgroundDiscovery:
    """One APK-style UDP socket shared by all Cozy Life config entries."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self._transport: asyncio.DatagramTransport | None = None
        self._broadcast_task: asyncio.Task[None] | None = None
        self._devices: dict[str, tuple[UdpDiscoveredDevice, float]] = {}
        self._entry_callbacks: dict[str, Callable[[str], Awaitable[None]]] = {}
        self._flow_started_for: set[str] = set()
        self._scan_in_progress = 0

    async def async_start(self) -> None:
        """Open the APK-style receive socket and begin its Wi-Fi cadence."""
        if self._transport is not None:
            return

        loop = asyncio.get_running_loop()
        transport, _protocol = await loop.create_datagram_endpoint(
            lambda: _ApkUdpDiscoveryProtocol(self._handle_device),
            local_addr=("0.0.0.0", 0),
            allow_broadcast=True,
        )
        self._transport = transport

        # The APK emits this burst when its active network becomes Wi-Fi. Home
        # Assistant startup is the corresponding point at which this service
        # first has an active local network.
        self._send_broadcasts(APK_NETWORK_CHANGE_BROADCAST_COUNT)
        self._broadcast_task = self.hass.async_create_background_task(
            self._async_broadcast_loop(),
            "cozylife UDP discovery broadcaster",
        )
        _LOGGER.info(
            "CozyLife UDP discovery is listening on one shared socket; sent %s "
            "APK network-start broadcasts",
            APK_NETWORK_CHANGE_BROADCAST_COUNT,
        )

    async def async_stop(self) -> None:
        """Stop the shared socket when Home Assistant stops."""
        if self._broadcast_task is not None:
            self._broadcast_task.cancel()
            try:
                await self._broadcast_task
            except asyncio.CancelledError:
                pass
            self._broadcast_task = None
        if self._transport is not None:
            self._transport.close()
            self._transport = None

    def register_device(
        self,
        device_id: str,
        callback: Callable[[str], Awaitable[None]],
    ) -> None:
        """Route later APK UDP replies for one configured DID to its entry."""
        self._entry_callbacks[device_id] = callback

    def unregister_device(self, device_id: str) -> None:
        """Stop routing replies to an unloaded config entry."""
        self._entry_callbacks.pop(device_id, None)

    async def async_scan(self) -> list[UdpDiscoveredDevice]:
        """Use the shared socket for an interactive config-flow scan."""
        scan_started = time.monotonic()
        self._scan_in_progress += 1
        try:
            was_started = self._transport is not None
            if not was_started:
                await self.async_start()
            else:
                # The APK's ordinary daemon cycle sends one message every ten
                # seconds. Its manual Wi-Fi setup path calls startSearchDevices,
                # which sends one command-0 frame, so do the same here.
                self._send_broadcasts(1)

            await asyncio.sleep(CONFIG_FLOW_DISCOVERY_WINDOW_SECONDS)
            return [
                device
                for device, received_at in self._devices.values()
                if received_at >= scan_started
            ]
        finally:
            self._scan_in_progress -= 1

    async def _async_broadcast_loop(self) -> None:
        """Mirror the APK daemon's one discovery broadcast every ten seconds."""
        while True:
            await asyncio.sleep(APK_DAEMON_INTERVAL_SECONDS)
            self._send_broadcasts(1)

    def _send_broadcasts(self, count: int) -> None:
        """Send APK command-0 frames through the listener's own socket."""
        transport = self._transport
        if transport is None:
            return
        for _ in range(count):
            request = {"cmd": 0, "pv": 0, "sn": _get_sn(), "msg": {}}
            transport.sendto(
                (json.dumps(request, separators=(",", ":")) + "\r\n").encode(),
                ("255.255.255.255", COZYLIFE_UDP_DISCOVERY_PORT),
            )

    def _handle_device(self, device: UdpDiscoveredDevice) -> None:
        """Cache an APK reply, refresh known entries, or start one discovery flow."""
        self._devices[device.device_id] = (device, time.monotonic())
        callback = self._entry_callbacks.get(device.device_id)
        if callback is not None:
            self.hass.async_create_task(callback(device.ip_address))
            return

        # The user-initiated config flow owns announcements received during
        # its scan. Starting a second flow here would make two competing setup
        # cards for the same packet.
        if self._scan_in_progress:
            return
        if self._is_configured(device.device_id, device.ip_address) or (
            device.device_id in self._flow_started_for
        ):
            return
        self._flow_started_for.add(device.device_id)
        self.hass.async_create_task(self._async_start_discovery_flow(device))

    def _is_configured(self, device_id: str, ip_address: str) -> bool:
        """Avoid cards for configured DIDs, including provisional sensor IDs."""
        return any(
            entry.data.get("device_id") == device_id
            or entry.unique_id == device_id
            # A sleeping sensor can be configured before it has supplied its
            # real DID. Its static configured address still identifies the
            # announcement as belonging to an existing entry.
            or entry.data.get("ip_address") == ip_address
            for entry in self.hass.config_entries.async_entries(DOMAIN)
        )

    async def _async_start_discovery_flow(self, device: UdpDiscoveredDevice) -> None:
        """Offer an unconfigured DID through Home Assistant's discovery UI."""
        try:
            await self.hass.config_entries.flow.async_init(
                DOMAIN,
                context={"source": config_entries.SOURCE_INTEGRATION_DISCOVERY},
                data={
                    "device_id": device.device_id,
                    "ip_address": device.ip_address,
                    "pid": device.pid,
                    "device_type_code": device.device_type_code,
                },
            )
        except Exception:  # pragma: no cover - Home Assistant owns flow errors.
            _LOGGER.exception(
                "Unable to start a discovery flow for CozyLife device %s",
                device.device_id,
            )


async def async_get_background_discovery(
    hass: HomeAssistant,
) -> CozyLifeBackgroundDiscovery:
    """Return the domain's one shared APK-style discovery service."""
    domain_data = hass.data.setdefault(DOMAIN, {})
    service = domain_data.get(DATA_DISCOVERY_SERVICE)
    if service is None:
        service = CozyLifeBackgroundDiscovery(hass)
        domain_data[DATA_DISCOVERY_SERVICE] = service
        await service.async_start()
    return service
