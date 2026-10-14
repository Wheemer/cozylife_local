"""Tests for the vendor-backed sleeping sensor reporting model."""

from __future__ import annotations

import asyncio
import importlib
import sys
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path


COMPONENT_DIR = Path(__file__).parents[1] / "custom_components" / "cozylife_local"


def _module(name: str) -> types.ModuleType:
    module = types.ModuleType(name)
    sys.modules[name] = module
    return module


def _load_modules():
    _module("homeassistant")
    config_entries = _module("homeassistant.config_entries")
    config_entries.ConfigEntry = object
    core = _module("homeassistant.core")
    core.HomeAssistant = object
    _module("homeassistant.util")
    dt_util = _module("homeassistant.util.dt")
    dt_util.utcnow = lambda: None
    _module("homeassistant.helpers")
    update_coordinator = _module("homeassistant.helpers.update_coordinator")

    class Coordinator:
        @classmethod
        def __class_getitem__(cls, _item):
            return cls

    update_coordinator.DataUpdateCoordinator = Coordinator
    update_coordinator.UpdateFailed = RuntimeError
    package = types.ModuleType("cozylife_local")
    package.__path__ = [str(COMPONENT_DIR)]
    sys.modules["cozylife_local"] = package
    discovery = importlib.import_module("cozylife_local.discovery")
    coordinator = importlib.import_module("cozylife_local.coordinator")
    return discovery, coordinator


def test_vendor_catalog_interval_for_felicia_sensor() -> None:
    discovery, _ = _load_modules()

    model = discovery.get_model_info("Z4tRml")

    assert model is not None
    assert model.report_interval_seconds == 3600


def test_dpid_14_is_a_reporting_choice_not_seconds() -> None:
    _, coordinator = _load_modules()

    assert coordinator._decode_sensor_report_interval(0) == 1800
    assert coordinator._decode_sensor_report_interval(1) == 3600
    assert coordinator._decode_sensor_report_interval(15) == 28800
    assert coordinator._decode_sensor_report_interval(600) is None
    assert coordinator._decode_sensor_report_interval(3600) == 3600


def test_placeholder_sensor_response_is_resampled_without_legacy_option(
    monkeypatch,
) -> None:
    """A placeholder response must enter wake sampling without old options."""
    _, coordinator_module = _load_modules()

    class Device:
        ip_address = "192.0.2.1"

        def __init__(self) -> None:
            self.responses = [{"4": 0, "8": 0}, {"4": 48, "8": 215}]

        async def async_get_state(self):
            return self.responses.pop(0)

    async def no_wait(_delay: float) -> None:
        return None

    instance = object.__new__(coordinator_module.CozyLifeCoordinator)
    instance.device = Device()
    instance.classification = types.SimpleNamespace(is_environment_sensor=True)
    instance._uses_sensor_polling = True
    instance._current_sensor_response_at = None
    monkeypatch.setattr(coordinator_module.asyncio, "sleep", no_wait)

    assert asyncio.run(instance._async_get_environment_state()) == {"4": 48, "8": 215}


def test_connect_timeout_is_a_normal_no_response() -> None:
    """A sleeping-device connection timeout must not escape the API client."""
    _load_modules()
    api = importlib.import_module("cozylife_local.cozylife_api")

    async def timeout_connect() -> None:
        raise asyncio.TimeoutError

    instance = object.__new__(api.CozyLifeDevice)
    instance._connect = timeout_connect
    instance._disconnect = lambda: None

    assert asyncio.run(instance._send_receive(2, {"attr": [1]})) is None


def test_timeout_starts_sensor_catch_polling_and_retains_data(monkeypatch) -> None:
    """A missed wake must retry quickly instead of waiting another cycle."""
    _, coordinator_module = _load_modules()

    class Device:
        ip_address = "192.0.2.1"
        device_id = "device"
        dpid = ["4", "8", "9", "14"]

        async def async_get_state(self):
            return None

    instance = object.__new__(coordinator_module.CozyLifeCoordinator)
    instance.device = Device()
    instance.classification = types.SimpleNamespace(
        is_environment_sensor=True,
        supports_switch_entities=False,
        is_light=False,
    )
    instance.data = {"4": 48, "8": 215}
    instance._uses_sensor_polling = True
    instance._current_sensor_response_at = None
    instance._catch_started_at = None
    instance._last_sensor_response_at = datetime(2026, 10, 14, tzinfo=timezone.utc)
    instance._effective_report_interval = 3600
    instance._first_success_wait_logged = False
    instance._missed_expected_wake_logged = False
    instance._fallback_logged = False
    monkeypatch.setattr(
        coordinator_module.dt_util,
        "utcnow",
        lambda: datetime(2026, 10, 14, tzinfo=timezone.utc),
    )

    assert asyncio.run(instance._async_update_data()) == {"4": 48, "8": 215}
    assert instance.update_interval == timedelta(seconds=1)


def test_direct_door_and_water_sensors_expose_vendor_battery_dpid() -> None:
    """DPID 9 is the APK-confirmed battery percentage field for sensors."""
    discovery, _ = _load_modules()

    door = discovery.classify_device("0iDvw9", "03", ["7", "9", "27", "28", "29"])
    water = discovery.classify_device("y9DaDw", "03", ["9", "10"])

    assert door.is_direct_local and door.is_sensor_category
    assert water.is_direct_local and water.is_sensor_category
    assert not door.is_environment_sensor and not water.is_environment_sensor
    assert door.has_battery_sensor and water.has_battery_sensor
