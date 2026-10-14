"""Tests for the vendor-backed sleeping sensor reporting model."""

from __future__ import annotations

import importlib
import sys
import types
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


def test_x7_wifi_speakers_are_classified_for_local_media_support() -> None:
    discovery, _ = _load_modules()

    classification = discovery.classify_device(
        "GriFPh", "23", [str(dpid) for dpid in range(1, 26)]
    )

    assert classification.is_direct_local
    assert classification.is_smart_speaker
