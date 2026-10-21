import logging
import asyncio
import ipaddress
from typing import Any, Dict, Optional

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult

from .const import (
    DOMAIN,
    DEFAULT_MAX_KELVIN,
    DEFAULT_MIN_KELVIN,
    LIGHT_KELVIN_RANGES,
    SENSOR_TYPE_CODE,
)
from .cozylife_api import CozyLifeDevice
from .discovery import (
    async_load_model_catalog,
    classify_device,
    get_model_info,
    get_wifi_environment_sensor_models,
)
from .network_discovery import (
    AUTO_NETWORK,
    DiscoveredDevice,
    NetworkScanTooLarge,
    NoNetworkAvailable,
    async_discover_devices,
)

_LOGGER = logging.getLogger(__name__)

DATA_SCHEMA = vol.Schema({
    vol.Optional("ip_address", default="", description={"suggested_value": "192.168.1.100"}): str,
    vol.Optional("network_cidr", default=AUTO_NETWORK): str,
    vol.Optional("sleeping_sensor", default=False): bool,
})

SLEEPING_SENSOR_PID = "Z4tRml"
def _kelvin_range_for_pid(pid: str | None) -> tuple[int, int]:
    """Return the default Kelvin range for a light model."""
    return LIGHT_KELVIN_RANGES.get(
        pid or "",
        (DEFAULT_MIN_KELVIN, DEFAULT_MAX_KELVIN),
    )


def _device_schema_light(ip_address: str, pid: str | None = None) -> vol.Schema:
    """Return a light setup schema with the selected IP carried forward."""
    min_kelvin, max_kelvin = _kelvin_range_for_pid(pid)
    return vol.Schema({
        vol.Required("ip_address", default=ip_address): str,
        vol.Optional("min_kelvin", default=min_kelvin): vol.All(int, vol.Range(min=1000, max=10000)),
        vol.Optional("max_kelvin", default=max_kelvin): vol.All(int, vol.Range(min=1000, max=10000)),
    })


def _sleeping_sensor_schema(ip_address: str) -> vol.Schema:
    """Return sleeping environment sensor setup schema."""
    sensor_options = {
        model.product_id: f"{model.model_name or 'Temperature/humidity sensor'} ({model.product_id})"
        for model in get_wifi_environment_sensor_models()
    }
    return vol.Schema({
        vol.Required("ip_address", default=ip_address): str,
        vol.Optional("sleeping_sensor", default=True): bool,
        vol.Required("sensor_pid", default=SLEEPING_SENSOR_PID): vol.In(sensor_options),
    })

class CozyLifeConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for CozyLife integration."""

    VERSION = 1
    CONNECTION_CLASS = config_entries.CONN_CLASS_LOCAL_POLL

    async def async_step_user(self, user_input: Optional[Dict[str, Any]] = None) -> FlowResult:
        """Handle the initial step."""
        errors: Dict[str, str] = {}

        if user_input is not None:
            ip_address = user_input.get("ip_address", "").strip()
            network_cidr = user_input.get("network_cidr", AUTO_NETWORK)
            sleeping_sensor = user_input.get("sleeping_sensor", False)

            if not ip_address:
                if sleeping_sensor:
                    errors["base"] = "ip_required_for_sleeping_sensor"
                    return self.async_show_form(
                        step_id="user",
                        data_schema=DATA_SCHEMA,
                        errors=errors,
                    )
                return await self._async_discover_devices(network_cidr)

            try:
                ipaddress.ip_address(ip_address)
            except ValueError:
                errors["ip_address"] = "invalid_ip"
                return self.async_show_form(
                    step_id="user",
                    data_schema=DATA_SCHEMA,
                    errors=errors,
                )

            if self._is_device_configured(ip_address):
                return self.async_abort(reason="already_configured")

            if sleeping_sensor:
                await async_load_model_catalog(self.hass)
                if "sensor_pid" not in user_input:
                    return self.async_show_form(
                        step_id="user",
                        data_schema=_sleeping_sensor_schema(ip_address),
                        errors={},
                    )
                return await self._async_create_sleeping_sensor_entry(
                    ip_address,
                    user_input,
                )

            return await self._async_create_entry_from_ip(
                ip_address,
                user_input.get("min_kelvin", DEFAULT_MIN_KELVIN),
                user_input.get("max_kelvin", DEFAULT_MAX_KELVIN),
                user_input,
            )

        return self.async_show_form(
            step_id="user", data_schema=DATA_SCHEMA, errors=errors
        )

    async def async_step_integration_discovery(
        self,
        discovery_info: Dict[str, Any],
    ) -> FlowResult:
        """Offer an APK-announced device that is not configured yet.

        UDP only identifies a candidate. Confirmation still performs the
        device's normal local TCP/5555 handshake before an entry is created.
        """
        device_id = discovery_info.get("device_id")
        ip_address = discovery_info.get("ip_address")
        if not isinstance(device_id, str) or not isinstance(ip_address, str):
            return self.async_abort(reason="invalid_discovery")

        try:
            ipaddress.IPv4Address(ip_address)
        except ipaddress.AddressValueError:
            return self.async_abort(reason="invalid_discovery")

        await self.async_set_unique_id(device_id)
        self._abort_if_unique_id_configured()
        self._udp_discovery_info = discovery_info
        return await self.async_step_confirm_discovery()

    async def async_step_confirm_discovery(
        self,
        user_input: Optional[Dict[str, Any]] = None,
    ) -> FlowResult:
        """Confirm adding a device discovered by the shared UDP listener."""
        discovery_info = getattr(self, "_udp_discovery_info", None)
        if not isinstance(discovery_info, dict):
            return self.async_abort(reason="invalid_discovery")

        ip_address = discovery_info["ip_address"]
        if user_input is not None:
            return await self._async_create_entry_from_ip(
                ip_address,
                DEFAULT_MIN_KELVIN,
                DEFAULT_MAX_KELVIN,
                {"ip_address": ip_address},
            )

        await async_load_model_catalog(self.hass)
        model = get_model_info(discovery_info.get("pid"))
        device_name = (
            model.model_name
            if model is not None and model.model_name
            else "CozyLife device"
        )
        return self.async_show_form(
            step_id="confirm_discovery",
            description_placeholders={
                "device": device_name,
                "ip_address": ip_address,
            },
        )

    async def _async_create_sleeping_sensor_entry(
        self,
        ip_address: str,
        user_input: Dict[str, Any],
    ) -> FlowResult:
        """Create a temp/humidity sensor entry without waking the device."""
        pid = str(user_input.get("sensor_pid", SLEEPING_SENSOR_PID))
        model = get_model_info(pid)
        if model is None or model not in get_wifi_environment_sensor_models():
            raise ValueError(f"Unsupported Wi-Fi environment sensor PID: {pid}")
        device_id = f"sleeping_sensor_{pid}_{ip_address}"
        await self.async_set_unique_id(device_id)
        self._abort_if_unique_id_configured()

        return self.async_create_entry(
            title=f"{model.model_name or 'CozyLife Temp/Humidity'} @ {ip_address}",
            data={
                "ip_address": ip_address,
                "device_id": device_id,
                "pid": pid,
                "device_type_code": SENSOR_TYPE_CODE,
                "dpids": sorted(model.dpids),
            },
        )

    async def async_step_select_device(
        self,
        user_input: Optional[Dict[str, Any]] = None,
    ) -> FlowResult:
        """Let the user choose one of the discovered CozyLife devices."""
        discovered_devices = getattr(self, "_discovered_devices", {})
        if not discovered_devices:
            return self.async_show_form(
                step_id="user",
                data_schema=DATA_SCHEMA,
                errors={"base": "no_devices_found"},
            )

        if user_input is not None:
            ip_address = user_input["device"]
            device = discovered_devices.get(ip_address)
            if self._is_device_configured(
                ip_address,
                device.device_id if device else None,
            ):
                return self.async_abort(reason="already_configured")

            return await self._async_create_entry_from_ip(
                ip_address,
                DEFAULT_MIN_KELVIN,
                DEFAULT_MAX_KELVIN,
                {"ip_address": ip_address},
            )

        return self._show_discovered_devices_form(discovered_devices)
    
    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        """Get the options flow for this handler."""
        return CozyLifeOptionsFlow(config_entry)

    async def _async_discover_devices(self, network_cidr: str) -> FlowResult:
        """Scan the network and show discovered CozyLife devices."""
        errors: Dict[str, str] = {}
        try:
            devices = await async_discover_devices(self.hass, network_cidr)
        except NetworkScanTooLarge:
            errors["base"] = "network_too_large"
        except NoNetworkAvailable:
            errors["base"] = "no_network"
        except ValueError:
            errors["base"] = "invalid_network"
        except Exception:
            _LOGGER.exception("Unexpected error during CozyLife network discovery")
            errors["base"] = "unknown"

        if errors:
            return self.async_show_form(
                step_id="user",
                data_schema=DATA_SCHEMA,
                errors=errors,
            )

        discovered_devices = {
            device.ip_address: device for device in devices
        }
        self._add_configured_devices_to_discovery(discovered_devices)

        if not discovered_devices:
            return self.async_show_form(
                step_id="user",
                data_schema=DATA_SCHEMA,
                errors={"base": "no_devices_found"},
            )

        self._discovered_devices = discovered_devices
        return self._show_discovered_devices_form(self._discovered_devices)

    def _show_discovered_devices_form(
        self,
        discovered_devices: Dict[str, Any],
    ) -> FlowResult:
        """Show a selector for devices found by network discovery."""
        configured_ips, configured_device_ids = self._configured_device_keys()
        device_options = {
            ip_address: (
                f"{device.label} - already added"
                if (
                    ip_address in configured_ips
                    or (
                        device.device_id is not None
                        and device.device_id in configured_device_ids
                    )
                )
                else device.label
            )
            for ip_address, device in discovered_devices.items()
        }
        return self.async_show_form(
            step_id="select_device",
            data_schema=vol.Schema({vol.Required("device"): vol.In(device_options)}),
            errors={},
            description_placeholders={
                "count": str(len(discovered_devices)),
            },
        )

    def _add_configured_devices_to_discovery(
        self,
        discovered_devices: Dict[str, DiscoveredDevice],
    ) -> None:
        """Include existing CozyLife entries in the discovery selector."""
        for entry in self.hass.config_entries.async_entries(DOMAIN):
            ip_address = entry.data.get("ip_address")
            if not ip_address or ip_address in discovered_devices:
                continue

            dpids = entry.data.get("dpids") or ()
            discovered_devices[ip_address] = DiscoveredDevice(
                ip_address=ip_address,
                device_id=entry.data.get("device_id"),
                pid=entry.data.get("pid"),
                device_type_code=entry.data.get("device_type_code"),
                device_model_name=entry.title or "CozyLife Device",
                dpids=tuple(str(dpid) for dpid in dpids),
            )

    def _configured_device_keys(self) -> tuple[set[str], set[str]]:
        """Return configured IP addresses and device IDs for this integration."""
        configured_ips: set[str] = set()
        configured_device_ids: set[str] = set()

        for entry in self.hass.config_entries.async_entries(DOMAIN):
            if ip_address := entry.data.get("ip_address"):
                configured_ips.add(ip_address)
            if device_id := entry.data.get("device_id"):
                configured_device_ids.add(device_id)
            if entry.unique_id:
                configured_device_ids.add(entry.unique_id)

        return configured_ips, configured_device_ids

    def _is_device_configured(
        self,
        ip_address: str | None,
        device_id: str | None = None,
    ) -> bool:
        """Return true if an IP address or device ID is already configured."""
        configured_ips, configured_device_ids = self._configured_device_keys()
        return (
            ip_address in configured_ips
            or (
                device_id is not None
                and device_id in configured_device_ids
            )
        )

    async def _async_create_entry_from_ip(
        self,
        ip_address: str,
        min_kelvin: int,
        max_kelvin: int,
        user_input: Dict[str, Any],
    ) -> FlowResult:
        """Helper to create a config entry from a single IP address."""
        errors: Dict[str, str] = {}
        try:
            await async_load_model_catalog(self.hass)
            device = CozyLifeDevice(ip_address)
            if not await device.async_update_device_info():
                 errors["base"] = "cannot_connect"
            else:
                await self.async_set_unique_id(device.device_id)
                self._abort_if_unique_id_configured()

                dpids = device.dpid or []
                classification = classify_device(device.pid, device.device_type_code, dpids)
                if not classification.is_local_transport:
                    return self.async_show_form(
                        step_id="user",
                        data_schema=DATA_SCHEMA,
                        errors={"base": "unsupported_transport"},
                    )
                is_light = classification.is_light
                if is_light:
                    model_min_kelvin, model_max_kelvin = _kelvin_range_for_pid(device.pid)
                    if (
                        "min_kelvin" not in user_input
                        or min_kelvin == DEFAULT_MIN_KELVIN
                    ):
                        min_kelvin = model_min_kelvin
                    if (
                        "max_kelvin" not in user_input
                        or max_kelvin == DEFAULT_MAX_KELVIN
                    ):
                        max_kelvin = model_max_kelvin

                # If this is a light and kelvin fields weren't provided yet, re-show with light schema
                if is_light and "min_kelvin" not in user_input:
                    return self.async_show_form(
                        step_id="user",
                        data_schema=_device_schema_light(ip_address, device.pid),
                        errors={},
                        description_placeholders={"ip_address": ip_address},
                    )

                return self.async_create_entry(
                    title=device.device_model_name or ip_address,
                    data={
                        "ip_address": ip_address,
                        "device_id": device.device_id,
                        "pid": device.pid,
                        "device_type_code": device.device_type_code,
                        "dpids": dpids,
                        "min_kelvin": (
                            min_kelvin if is_light else DEFAULT_MIN_KELVIN
                        ),
                        "max_kelvin": (
                            max_kelvin if is_light else DEFAULT_MAX_KELVIN
                        ),
                    }
                )
        except asyncio.TimeoutError:
            errors["base"] = "timeout_connect"
        except ConnectionRefusedError:
            errors["base"] = "cannot_connect"
        except Exception:
            _LOGGER.exception("Unexpected error during CozyLife device setup")
            errors["base"] = "unknown"

        return self.async_show_form(
            step_id="user", data_schema=DATA_SCHEMA, errors=errors
        )

class CozyLifeOptionsFlow(config_entries.OptionsFlow):
    """Handle CozyLife options."""

    def __init__(self, config_entry: config_entries.ConfigEntry):
        """Initialize CozyLife options flow."""
        self._config_entry = config_entry

    async def async_step_init(self, user_input: Optional[Dict[str, Any]] = None) -> FlowResult:
        """Manage the options."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        return self.async_show_form(
            step_id="init",
            data_schema=self._options_schema(),
        )

    def _options_schema(self) -> vol.Schema:
        """Return the options schema for this config entry."""
        dpids = self._config_entry.data.get("dpids") or []
        pid = self._config_entry.data.get("pid", "")
        classification = classify_device(
            pid,
            self._config_entry.data.get("device_type_code"),
            dpids,
        )
        is_light = classification.is_light

        schema_fields: Dict[Any, Any] = {}
        if is_light:
            model_min_kelvin, model_max_kelvin = _kelvin_range_for_pid(pid)
            stored_min_kelvin = self._config_entry.data.get("min_kelvin", DEFAULT_MIN_KELVIN)
            stored_max_kelvin = self._config_entry.data.get("max_kelvin", DEFAULT_MAX_KELVIN)
            schema_fields[
                vol.Optional(
                    "min_kelvin",
                    default=(
                        model_min_kelvin
                        if stored_min_kelvin == DEFAULT_MIN_KELVIN
                        else stored_min_kelvin
                    ),
                )
            ] = vol.All(int, vol.Range(min=1000, max=10000))
            schema_fields[
                vol.Optional(
                    "max_kelvin",
                    default=(
                        model_max_kelvin
                        if stored_max_kelvin == DEFAULT_MAX_KELVIN
                        else stored_max_kelvin
                    ),
                )
            ] = vol.All(int, vol.Range(min=1000, max=10000))
        schema_fields[vol.Optional("enable_debug", default=self._config_entry.options.get("enable_debug", False))] = bool

        return vol.Schema(schema_fields)
