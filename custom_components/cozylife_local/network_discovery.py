"""Network scanning helpers for CozyLife Local."""

from __future__ import annotations

import asyncio
import ipaddress
import logging
from dataclasses import dataclass

from homeassistant.core import HomeAssistant

from .background_discovery import async_get_background_discovery
from .cozylife_api import COZYLIFE_PORT, CozyLifeDevice
from .discovery import async_load_model_catalog

_LOGGER = logging.getLogger(__name__)

AUTO_NETWORK = "auto"
MAX_SCAN_HOSTS = 512
SCAN_CONNECT_TIMEOUT = 0.75
DEVICE_INFO_TIMEOUT = 1.5
SCAN_CONCURRENCY = 64


@dataclass(frozen=True)
class DiscoveredDevice:
    """A CozyLife device discovered on the local network."""

    ip_address: str
    device_id: str | None
    pid: str | None
    device_type_code: str | None
    device_model_name: str | None
    dpids: tuple[str, ...]

    @property
    def label(self) -> str:
        """Return a human-readable label for the config flow selector."""
        model = self.device_model_name or "CozyLife Device"
        pid = self.pid or "unknown PID"
        type_code = self.device_type_code or "unknown type"
        return f"{model} ({self.ip_address}) - PID {pid}, Type {type_code}"


class NetworkScanTooLarge(ValueError):
    """Raised when the requested network is too large to scan safely."""


class NoNetworkAvailable(ValueError):
    """Raised when no local IPv4 network can be detected."""


async def async_discover_devices(
    hass: HomeAssistant,
    network_cidr: str = AUTO_NETWORK,
) -> list[DiscoveredDevice]:
    """Scan the requested network and return discovered CozyLife devices."""
    await async_load_model_catalog(hass)
    networks = await _async_get_scan_networks(hass, network_cidr)
    hosts = _hosts_for_networks(networks)

    # The APK's first local-discovery operation is a UDP/6095 cmd-0 broadcast,
    # not a TCP port sweep. A device that answers that broadcast is the app's
    # positive proof that TCP/5555 is its local command endpoint, so query it
    # directly without discarding it based on a preliminary TCP connect.
    udp_addresses = await _async_discover_udp_addresses(hass, networks)
    udp_results = await asyncio.gather(
        *(_async_query_discovered_host(ip_address) for ip_address in udp_addresses)
    )

    semaphore = asyncio.Semaphore(SCAN_CONCURRENCY)

    async def scan_host(ip_address: str) -> DiscoveredDevice | None:
        async with semaphore:
            return await _async_discover_host(ip_address)

    # Retain the historical TCP sweep as a fallback for products that do not
    # answer their UDP discovery broadcast, but do not repeat probes for
    # APK-confirmed addresses.
    udp_address_set = set(udp_addresses)
    tcp_results = await asyncio.gather(
        *(scan_host(ip_address) for ip_address in hosts if ip_address not in udp_address_set)
    )
    devices_by_id_or_address: dict[str, DiscoveredDevice] = {}
    for device in (*udp_results, *tcp_results):
        if device is not None:
            devices_by_id_or_address[device.device_id or device.ip_address] = device
    devices = list(devices_by_id_or_address.values())
    devices.sort(key=lambda device: tuple(int(part) for part in device.ip_address.split(".")))
    _LOGGER.info(
        "CozyLife network discovery found %s device(s) on %s (%s APK UDP reply/replies)",
        len(devices),
        ", ".join(str(network) for network in networks),
        len(udp_addresses),
    )
    return devices


async def _async_discover_udp_addresses(
    hass: HomeAssistant,
    networks: list[ipaddress.IPv4Network],
) -> list[str]:
    """Use the integration's APK-style shared UDP listener for a scan."""
    try:
        # Do not eagerly start here: ``async_scan`` must see whether this is
        # the first listener start so it emits the APK's 17-frame Wi-Fi burst
        # rather than that burst plus a separate manual-search frame.
        discovery = await async_get_background_discovery(hass, start=False)
        announcements = await discovery.async_scan()
    except OSError as err:
        _LOGGER.debug("CozyLife UDP discovery was unavailable: %s", err)
        return []
    return sorted(
        {
            announcement.ip_address
            for announcement in announcements
            if any(
                ipaddress.IPv4Address(announcement.ip_address) in network
                for network in networks
            )
        },
        key=lambda address: tuple(int(part) for part in address.split(".")),
    )


async def _async_get_scan_networks(
    hass: HomeAssistant,
    network_cidr: str,
) -> list[ipaddress.IPv4Network]:
    """Return IPv4 networks to scan."""
    requested = (network_cidr or AUTO_NETWORK).strip().lower()
    if requested != AUTO_NETWORK:
        try:
            network = ipaddress.ip_network(requested, strict=False)
        except ValueError as err:
            raise ValueError("invalid_network") from err

        if network.version != 4:
            raise ValueError("invalid_network")

        return [_validate_network_size(network)]

    networks = await _async_get_auto_networks(hass)
    if not networks:
        raise NoNetworkAvailable("no_network")
    return networks


async def _async_get_auto_networks(hass: HomeAssistant) -> list[ipaddress.IPv4Network]:
    """Build safe scan networks from Home Assistant's enabled adapters."""
    try:
        from homeassistant.components import network as ha_network
    except ImportError:
        try:
            from homeassistant.helpers import network as ha_network
        except ImportError:
            return []

    async_get_adapters = getattr(ha_network, "async_get_adapters", None)
    if async_get_adapters is None:
        return []

    adapters = await async_get_adapters(hass)
    networks: list[ipaddress.IPv4Network] = []
    seen: set[str] = set()

    for adapter in adapters:
        if not adapter.get("enabled", True):
            continue

        for ipv4 in adapter.get("ipv4", []):
            address = ipv4.get("address")
            prefix = ipv4.get("network_prefix")
            if not address or prefix is None:
                continue

            try:
                ip_addr = ipaddress.ip_address(address)
                if ip_addr.is_loopback or ip_addr.is_link_local:
                    continue

                interface = ipaddress.ip_interface(f"{address}/{prefix}")
                network = interface.network
                if network.version != 4:
                    continue

                # A /24 keeps auto-discovery responsive even on broad LAN masks.
                if network.prefixlen < 24:
                    network = ipaddress.ip_network(f"{address}/24", strict=False)

                network = _validate_network_size(network)
            except ValueError:
                continue

            key = str(network)
            if key not in seen:
                seen.add(key)
                networks.append(network)

    return networks


def _validate_network_size(
    network: ipaddress.IPv4Network,
) -> ipaddress.IPv4Network:
    """Reject networks that are too large for an interactive config flow."""
    if network.num_addresses > MAX_SCAN_HOSTS + 2:
        raise NetworkScanTooLarge(str(network))
    return network


def _hosts_for_networks(networks: list[ipaddress.IPv4Network]) -> list[str]:
    """Return de-duplicated host addresses for all networks."""
    hosts: list[str] = []
    seen: set[str] = set()

    for network in networks:
        for host in network.hosts():
            ip_address = str(host)
            if ip_address not in seen:
                seen.add(ip_address)
                hosts.append(ip_address)

    if len(hosts) > MAX_SCAN_HOSTS:
        raise NetworkScanTooLarge("combined networks")

    return hosts


async def _async_discover_host(ip_address: str) -> DiscoveredDevice | None:
    """Return device details if the host speaks the CozyLife local protocol."""
    if not await _async_is_port_open(ip_address):
        return None

    return await _async_query_discovered_host(ip_address)


async def _async_query_discovered_host(ip_address: str) -> DiscoveredDevice | None:
    """Read device details after the APK's discovery path identified its address."""

    device = CozyLifeDevice(ip_address, timeout=DEVICE_INFO_TIMEOUT)
    if not await device.async_update_device_info():
        return None

    return DiscoveredDevice(
        ip_address=ip_address,
        device_id=device.device_id,
        pid=device.pid,
        device_type_code=device.device_type_code,
        device_model_name=device.device_model_name,
        dpids=tuple(device.dpid or ()),
    )


async def _async_is_port_open(ip_address: str) -> bool:
    """Return true if the CozyLife TCP port accepts a connection."""
    writer: asyncio.StreamWriter | None = None
    try:
        _reader, writer = await asyncio.wait_for(
            asyncio.open_connection(ip_address, COZYLIFE_PORT),
            timeout=SCAN_CONNECT_TIMEOUT,
        )
        return True
    except (asyncio.TimeoutError, ConnectionRefusedError, OSError):
        return False
    finally:
        if writer is not None:
            writer.close()
            try:
                await writer.wait_closed()
            except OSError:
                pass
