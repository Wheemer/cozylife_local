"""The CozyLife (New) integration."""
import logging

from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import CoreState, HomeAssistant, callback

from .background_discovery import (
    DATA_DISCOVERY_SERVICE,
    async_get_background_discovery,
)
from .const import DISCOVERY_CONTROLLER_ENTRY_TYPE, DOMAIN, PLATFORMS
from .coordinator import CozyLifeCoordinator
from .cozylife_api import CozyLifeDevice
from .discovery import async_load_model_catalog

_LOGGER = logging.getLogger(__name__)


async def _async_ensure_discovery_controller(hass: HomeAssistant) -> None:
    """Add the shared discovery controller when upgrading device-only setups.

    Older releases stored one config entry per device. A user who already has
    one of those entries has already elected to use CozyLife Local, so a
    version upgrade must not make them manually add a second, empty entry just
    to enable the new integration-wide discovery service.
    """
    entries = hass.config_entries.async_entries(DOMAIN)
    if not entries or any(
        entry.data.get("entry_type") == DISCOVERY_CONTROLLER_ENTRY_TYPE
        for entry in entries
    ):
        return

    try:
        await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_IMPORT},
            data={"entry_type": DISCOVERY_CONTROLLER_ENTRY_TYPE},
        )
    except Exception:  # pragma: no cover - Home Assistant owns flow errors.
        _LOGGER.exception("Unable to create the CozyLife discovery controller")



@callback
def _async_schedule_discovery_controller(hass: HomeAssistant) -> None:
    """Create the upgrade controller only after Core startup completes."""
    if hass.state is CoreState.running:
        hass.async_create_task(_async_ensure_discovery_controller(hass))
        return


    @callback
    def _async_start_controller_migration(_event) -> None:
        hass.async_create_task(_async_ensure_discovery_controller(hass))

    hass.bus.async_listen_once(
        EVENT_HOMEASSISTANT_STARTED, _async_start_controller_migration
    )


async def async_setup(hass: HomeAssistant, _config: dict) -> bool:
    """Set up the integration-wide UDP listener lifecycle."""

    hass.data.setdefault(DOMAIN, {})
    _async_schedule_discovery_controller(hass)
    try:
        await async_get_background_discovery(hass)
    except OSError as err:
        # The listener is a discovery enhancement. Preserve already configured
        # local entries when the OS cannot currently create a UDP socket; a
        # later config-entry setup will retry it.
        _LOGGER.warning("Unable to start shared CozyLife UDP discovery: %s", err)

    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up CozyLife (New) from a config entry."""

    # Home Assistant calls async_setup before config entries, but retain this
    # harmless guard for direct setup/reload paths and isolated test harnesses.
    hass.data.setdefault(DOMAIN, {})

    # Check for debug logging option
    pkg_logger = logging.getLogger(__package__)
    if entry.options.get("enable_debug", False):
        pkg_logger.setLevel(logging.DEBUG)
        _LOGGER.debug(f"Debug logging enabled via options for {entry.title}")
    else:
        pkg_logger.setLevel(logging.NOTSET)

    discovery = hass.data[DOMAIN].get(DATA_DISCOVERY_SERVICE)
    if discovery is None or not discovery.is_running:
        try:
            discovery = await async_get_background_discovery(hass)
        except OSError as err:
            _LOGGER.warning("Unable to start shared CozyLife UDP discovery: %s", err)
            discovery = None

    # This controller entry is created by the no-fields config flow. It keeps
    # the integration loaded (and therefore its shared discovery listener)
    # across restarts, but owns no device platforms itself.
    if entry.data.get("entry_type") == DISCOVERY_CONTROLLER_ENTRY_TYPE:
        return True

    await async_load_model_catalog(hass)
    ip_address = entry.data["ip_address"]

    # Create a CozyLifeDevice instance for this entry
    device = CozyLifeDevice(ip_address)

    # Restore only facts previously obtained for this config entry. This lets a
    # sleeping device start without a synchronous reply, without turning the
    # catalog's possible DPID set into claimed device state.
    cached_did = entry.data.get("device_id")
    cached_pid = entry.data.get("pid")
    cached_dtp = entry.data.get("device_type_code")
    cached_dpids = entry.data.get("dpids")

    if cached_did and cached_pid and cached_dtp and cached_dpids:
        device.restore_from_cache(
            cached_did,
            cached_pid,
            cached_dtp,
            [str(dpid) for dpid in cached_dpids],
        )
        _LOGGER.debug(f"Restored device info for {ip_address} from cache.")
    else:
        # No cache — must contact device (first-time setup)
        if not await device.async_update_device_info():
            _LOGGER.error(f"Failed to get full device information for {ip_address}")
            return False
        # Save discovered info into config entry for future restarts
        hass.config_entries.async_update_entry(entry, data={
            **entry.data,
            "device_id": device.device_id,
            "pid": device.pid,
            "device_type_code": device.device_type_code,
            "dpids": device.dpid,
        })

    # Create a coordinator for this device
    coordinator = CozyLifeCoordinator(hass, device, entry)
    if discovery is not None and device.device_id:
        discovery.register_device(
            device.device_id,
            coordinator.async_handle_discovered_address,
        )
        if coordinator.is_sensor and device.device_id.startswith("sleeping_sensor_"):
            # Manual sleeping-sensor setup begins with an entry-local ID. A
            # UDP reply can still prove this configured address is reachable;
            # use it to request status, while TCP remains responsible for
            # promoting the real device ID.
            discovery.register_address(
                ip_address,
                coordinator.async_handle_discovered_address,
            )
    hass.async_create_task(coordinator.async_start_discovery())

    if coordinator.is_sensor:
        # Do not block setup while the sensor is unavailable.  The coordinator
        # keeps the configured local address available for wake-window probes;
        # UDP discovery independently refreshes that address when a matching
        # reply arrives.
        coordinator.async_set_updated_data({})
    else:
        await coordinator.async_config_entry_first_refresh()

    hass.data[DOMAIN][entry.entry_id] = coordinator

    # Set up platforms (light, switch, sensor, etc.)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Reload entry when options change
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))

    return True


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload config entry."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    if entry.data.get("entry_type") == DISCOVERY_CONTROLLER_ENTRY_TYPE:
        return True

    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if unload_ok:
        coordinator = hass.data[DOMAIN].pop(entry.entry_id)
        service = hass.data[DOMAIN].get(DATA_DISCOVERY_SERVICE)
        if service is not None and coordinator.device.device_id:
            service.unregister_device(coordinator.device.device_id)
            service.unregister_address(entry.data.get("ip_address", ""))
        await coordinator.async_close()

    return unload_ok
