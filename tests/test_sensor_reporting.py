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
        def __init__(self, coordinator=None) -> None:
            self.coordinator = coordinator

        def async_write_ha_state(self) -> None:
            return None

        @classmethod
        def __class_getitem__(cls, _item):
            return cls

    update_coordinator.DataUpdateCoordinator = Coordinator
    update_coordinator.CoordinatorEntity = Coordinator
    update_coordinator.UpdateFailed = RuntimeError
    package = types.ModuleType("cozylife_local")
    package.__path__ = [str(COMPONENT_DIR)]
    sys.modules["cozylife_local"] = package
    discovery = importlib.import_module("cozylife_local.discovery")
    coordinator = importlib.import_module("cozylife_local.coordinator")
    return discovery, coordinator


def _load_number_module():
    """Load the number platform against the focused Home Assistant stubs."""
    _load_modules()
    _module("homeassistant.components")
    number_component = _module("homeassistant.components.number")

    class NumberEntity:
        pass

    class NumberMode:
        BOX = "box"
        SLIDER = "slider"

    number_component.NumberEntity = NumberEntity
    number_component.NumberMode = NumberMode
    const = _module("homeassistant.const")
    const.EntityCategory = types.SimpleNamespace(CONFIG="config")
    const.PERCENTAGE = "%"
    const.UnitOfTemperature = types.SimpleNamespace(CELSIUS="C")
    const.UnitOfTime = types.SimpleNamespace(MINUTES="min", SECONDS="s")
    core = sys.modules["homeassistant.core"]
    core.callback = lambda function: function
    entity = _module("homeassistant.helpers.entity")
    entity.DeviceInfo = lambda **kwargs: kwargs
    return importlib.import_module("cozylife_local.number")


def _load_light_module():
    """Load the light platform against focused Home Assistant stubs."""
    _load_modules()
    _module("homeassistant.components")
    light_component = _module("homeassistant.components.light")

    class LightEntity:
        pass

    class ColorMode:
        ONOFF = "onoff"
        HS = "hs"
        COLOR_TEMP = "color_temp"
        BRIGHTNESS = "brightness"

    light_component.ATTR_BRIGHTNESS = "brightness"
    light_component.ATTR_COLOR_TEMP_KELVIN = "color_temp_kelvin"
    light_component.ATTR_HS_COLOR = "hs_color"
    light_component.ColorMode = ColorMode
    light_component.LightEntity = LightEntity
    core = sys.modules["homeassistant.core"]
    core.callback = lambda function: function
    entity = _module("homeassistant.helpers.entity")
    entity.DeviceInfo = lambda **kwargs: kwargs
    color = _module("homeassistant.util.color")
    color.color_hs_to_RGB = lambda hue, saturation: (hue, saturation, 0)
    color.color_RGB_to_hs = lambda red, green, _blue: (red, green)
    sys.modules.pop("cozylife_local.light", None)
    return importlib.import_module("cozylife_local.light")


def _load_select_module():
    """Load the select platform against the focused Home Assistant stubs."""
    _load_modules()
    _module("homeassistant.components")
    select_component = _module("homeassistant.components.select")

    class SelectEntity:
        pass

    select_component.SelectEntity = SelectEntity
    const = _module("homeassistant.const")
    const.EntityCategory = types.SimpleNamespace(CONFIG="config")
    core = sys.modules["homeassistant.core"]
    core.callback = lambda function: function
    entity = _module("homeassistant.helpers.entity")
    entity.DeviceInfo = lambda **kwargs: kwargs
    schedule = _module("cozylife_local.schedule")
    schedule.ACTION_TURN_OFF = "off"
    schedule.ACTION_TURN_ON = "on"
    schedule.DEFAULT_SCHEDULE_ID = "default"
    schedule.SCHEDULE_MANAGER = "schedule_manager"
    schedule.WEEKDAYS = {}
    schedule.CozyLifeScheduleManager = object
    options = _module("cozylife_local.switch_options")
    options.supported_dpids = lambda coordinator: set()
    options.supports_light_schedule_options = lambda coordinator: False
    options.supports_schedule_options = lambda coordinator: False
    return importlib.import_module("cozylife_local.select")


def _load_button_module():
    """Load the button platform against focused Home Assistant stubs."""
    _load_modules()
    _module("homeassistant.components")
    button_component = _module("homeassistant.components.button")

    class ButtonEntity:
        pass

    button_component.ButtonEntity = ButtonEntity
    const = _module("homeassistant.const")
    const.EntityCategory = types.SimpleNamespace(CONFIG="config")
    entity = _module("homeassistant.helpers.entity")
    entity.DeviceInfo = lambda **kwargs: kwargs
    options = _module("cozylife_local.switch_options")
    options.supported_dpids = lambda coordinator: set()
    return importlib.import_module("cozylife_local.button")


def _load_switch_module():
    """Load the switch platform against the focused Home Assistant stubs."""
    _load_modules()
    _module("homeassistant.components")
    switch_component = _module("homeassistant.components.switch")

    class SwitchEntity:
        pass

    switch_component.SwitchEntity = SwitchEntity
    switch_component.SwitchDeviceClass = types.SimpleNamespace(OUTLET="outlet")
    const = _module("homeassistant.const")
    const.EntityCategory = types.SimpleNamespace(CONFIG="config")
    core = sys.modules["homeassistant.core"]
    core.callback = lambda function: function
    entity = _module("homeassistant.helpers.entity")
    entity.DeviceInfo = lambda **kwargs: kwargs
    schedule = _module("cozylife_local.schedule")
    schedule.DEFAULT_SCHEDULE_ID = "default"
    schedule.SCHEDULE_MANAGER = "schedule_manager"
    schedule.CozyLifeScheduleManager = object
    options = _module("cozylife_local.switch_options")
    options.supported_dpids = lambda coordinator: set()
    options.supports_light_schedule_options = lambda coordinator: False
    options.supports_schedule_options = lambda coordinator: False
    return importlib.import_module("cozylife_local.switch")


def test_dreamcolor_static_color_matches_the_apk_payload() -> None:
    """DPID 16 is the panel's whole-strip static hue/saturation command."""
    light = _load_light_module()
    sent: list[dict[str, int | str]] = []

    class Device:
        device_model_name = "DreamColor"
        device_id = "device"
        pid = "pid"
        ip_address = "192.0.2.1"
        dpid = ("1", "2", "16")

        async def async_set_state(self, payload):
            sent.append(payload)
            return True

    coordinator = types.SimpleNamespace(
        device=Device(), data={"1": 1, "2": 0, "16": "0078029effff"}
    )
    entry = types.SimpleNamespace(data={}, options={})
    entity = light.CozyLifeLight(coordinator, entry)

    assert entity.hs_color == (120.0, 67.0)
    asyncio.run(entity.async_turn_on(hs_color=(120, 67)))

    assert sent == [{"1": 1, "2": 0, "16": "0078029effff"}]


def test_light_power_memory_options_match_the_apk() -> None:
    """DreamColor DPID 9 is a two-value power-memory setting."""
    select = _load_select_module()

    assert select.LIGHT_POWER_MEMORY_OPTIONS == {
        "On": 0,
        "Previous state": 1,
    }



def test_dreamcolor_boot_status_matches_the_apk_payload() -> None:
    """DPID 44 exposes the panel's three power-on behaviors."""
    select = _load_select_module()
    sent: list[dict[str, int]] = []

    class Device:
        device_model_name = "DreamColor"
        device_id = "device"
        pid = "pid"
        ip_address = "192.0.2.1"

        async def async_set_state(self, payload):
            sent.append(payload)
            return True

    coordinator = types.SimpleNamespace(device=Device(), data={"44": 1})
    entity = select.CozyLifeLightBootStatusSelect(coordinator)

    assert entity.current_option == "Previous state"
    asyncio.run(entity.async_select_option("Off"))

    assert sent == [{"44": 2}]
def test_dreamcolor_music_effect_matches_the_apk_payload() -> None:
    """The app enters music mode four and writes the selected index."""
    select = _load_select_module()
    sent: list[dict[str, int]] = []

    class Device:
        device_model_name = "DreamColor"
        device_id = "device"
        pid = "pid"
        ip_address = "192.0.2.1"

        async def async_set_state(self, payload):
            sent.append(payload)
            return True

    coordinator = types.SimpleNamespace(device=Device(), data={"15": 0})
    entity = select.CozyLifeLightMusicSelect(coordinator)
    asyncio.run(entity.async_select_option("Disco"))

    assert sent == [{"2": 4, "15": 3}]


def test_dreamcolor_rgb_order_options_match_the_apk() -> None:
    """The panel maps the six LED color orders directly to zero through five."""
    select = _load_select_module()

    assert select.LIGHT_RGB_TURN_OPTIONS == {
        "GR": 0,
        "RG": 1,
        "GB": 2,
        "RB": 3,
        "BR": 4,
        "BG": 5,
    }


def test_writable_options_use_only_device_advertised_dpids() -> None:
    """Catalog metadata must not create a writable control."""
    _load_modules()
    sys.modules.pop("cozylife_local.switch_options", None)
    options = importlib.import_module("cozylife_local.switch_options")
    coordinator = types.SimpleNamespace(
        device=types.SimpleNamespace(dpid=["1", "9"])
    )

    assert options.supported_dpids(coordinator) == {"1", "9"}


def test_dreamcolor_static_fade_matches_the_apk_values() -> None:
    """Static-effect fade writes the panel's exact zero/one values."""
    switch = _load_switch_module()
    sent: list[dict[str, int]] = []

    class Device:
        device_model_name = "DreamColor"
        device_id = "device"
        pid = "pid"
        ip_address = "192.0.2.1"

        async def async_set_state(self, payload):
            sent.append(payload)
            return True

    class Coordinator:
        def __init__(self):
            self.device = Device()
            self.data = {"20": 0}

        def async_set_updated_data(self, data):
            self.data = data

    entity = switch.CozyLifeLightStaticFadeSwitch(Coordinator())
    assert not entity.is_on
    asyncio.run(entity.async_turn_on())
    assert entity.is_on
    asyncio.run(entity.async_turn_off())

    assert sent == [{"20": 1}, {"20": 0}]


def test_dreamcolor_effect_numbers_match_the_apk_transforms() -> None:
    """Speed is percent ×10; microphone sensitivity is sent unchanged."""
    number = _load_number_module()
    speed, sensitivity = number.LIGHT_CONFIGURATION_NUMBERS
    sent: list[dict[str, int]] = []

    class Device:
        device_model_name = "DreamColor"
        device_id = "device"
        pid = "pid"
        ip_address = "192.0.2.1"

        async def async_set_state(self, payload):
            sent.append(payload)
            return True

    class Coordinator:
        def __init__(self):
            self.device = Device()
            self.data = {"8": 550, "10": 7}

        def async_set_updated_data(self, data):
            self.data = data

    coordinator = Coordinator()
    speed_entity = number.CozyLifeLightConfigurationNumber(coordinator, speed)
    sensitivity_entity = number.CozyLifeLightConfigurationNumber(
        coordinator, sensitivity
    )

    assert speed_entity.native_value == 55
    assert sensitivity_entity.native_value == 7
    asyncio.run(speed_entity.async_set_native_value(25))
    asyncio.run(sensitivity_entity.async_set_native_value(4))

    assert sent == [{"8": 250}, {"10": 4}]

def test_default_dreamcolor_led_count_matches_the_apk_bounds_and_payload() -> None:
    """The default DreamColor panel exposes DPID 12 from 10 through 300."""
    discovery, _ = _load_modules()
    number = _load_number_module()
    sent: list[dict[str, int]] = []

    assert discovery.dreamcolor_led_count_bounds("d8oaf1") == (10, 300)
    assert discovery.dreamcolor_led_count_bounds("i5ehdz") is None

    class Device:
        device_model_name = "DreamColor LED strip"
        device_id = "device"
        pid = "d8oaf1"
        ip_address = "192.0.2.1"

        async def async_set_state(self, payload):
            sent.append(payload)
            return True

    class Coordinator:
        def __init__(self):
            self.device = Device()
            self.data = {"12": 30}

        def async_set_updated_data(self, data):
            self.data = data

    entity = number.CozyLifeLightLedCountNumber(Coordinator(), 10, 300)
    assert entity.native_value == 30
    assert entity._attr_native_min_value == 10
    assert entity._attr_native_max_value == 300
    asyncio.run(entity.async_set_native_value(150))

    assert sent == [{"12": 150}]


def test_clear_device_log_button_matches_the_apk_command() -> None:
    """The plug panel clears its log by writing DPID 43 with value one."""
    button = _load_button_module()
    sent: list[dict[str, int]] = []

    class Device:
        device_model_name = "Plug"
        device_id = "device"
        pid = "pid"
        ip_address = "192.0.2.1"

        async def async_set_state(self, payload):
            sent.append(payload)
            return True

    entity = button.CozyLifeClearDeviceLogButton(
        types.SimpleNamespace(device=Device())
    )
    asyncio.run(entity.async_press())

    assert sent == [{"43": 1}]


def test_total_energy_dpid_enables_plug_metering() -> None:
    """The APK's total_ele DPID is a valid cumulative-metering field."""
    discovery, _ = _load_modules()

    classification = discovery.classify_device("unknown", "00", ["1", "42"])

    assert classification.supports_plug_metering


def test_metering_panel_boolean_options_match_the_apk() -> None:
    """The electrical panel names DPID 20, 21, and 32 as boolean controls."""
    switch = _load_switch_module()

    assert [(item.dpid, item.key) for item in switch.PLUG_BOOLEAN_SWITCHES] == [
        ("32", "overcurrent_protection"),
        ("20", "backlight"),
        ("21", "child_lock"),
    ]


def _load_binary_sensor_module():
    """Load the binary sensor platform against the focused HA stubs."""
    _load_modules()
    _module("homeassistant.components")
    binary_component = _module("homeassistant.components.binary_sensor")

    class BinarySensorEntity:
        pass

    binary_component.BinarySensorEntity = BinarySensorEntity
    binary_component.BinarySensorDeviceClass = types.SimpleNamespace(
        DOOR="door",
        MOTION="motion",
        MOISTURE="moisture",
        SMOKE="smoke",
        OCCUPANCY="occupancy",
        PROBLEM="problem",
    )
    core = sys.modules["homeassistant.core"]
    core.callback = lambda function: function
    entity = _module("homeassistant.helpers.entity")
    entity.DeviceInfo = lambda **kwargs: kwargs
    return importlib.import_module("cozylife_local.binary_sensor")


def test_water_and_smoke_alarm_values_match_the_apk() -> None:
    """The shared sensor panel defines only enum value 1 as an alarm."""
    binary_sensor = _load_binary_sensor_module()

    water = binary_sensor._binary_sensor_descriptions("Water sensor", ["10"])
    smoke = binary_sensor._binary_sensor_descriptions("Smoke sensor", ["11"])

    assert [(item.key, item.active_values) for item in water] == [
        ("moisture", frozenset({1}))
    ]
    assert [(item.key, item.active_values) for item in smoke] == [
        ("smoke", frozenset({1}))
    ]


def test_video_intercom_dpid_101_is_a_call_request() -> None:
    """The bundled intercom panel names DPID 101 device_request_call."""
    binary_sensor = _load_binary_sensor_module()

    descriptions = binary_sensor._binary_sensor_descriptions(
        "Video intercom", ["101"]
    )
    assert [(item.key, item.dpid, item.device_class) for item in descriptions] == [
        ("call_request", "101", None)
    ]



def test_door_magnet_contact_and_battery_dpids_match_the_apk() -> None:
    """Door-magnet panel maps DPID 7 to contact state and DPID 9 to battery."""
    binary_sensor = _load_binary_sensor_module()

    descriptions = binary_sensor._binary_sensor_descriptions(
        "Door magnet", ["7", "9"]
    )

    assert [(item.key, item.dpid, item.device_class) for item in descriptions] == [
        ("contact", "7", "door")
    ]
    assert binary_sensor._coerce_binary_state(1) is True
    assert binary_sensor._coerce_binary_state(0) is False



def test_sensor_without_reported_interval_does_not_use_catalog_cadence() -> None:
    """A response without DPID 14 keeps catch polling, regardless of its PID."""
    _, coordinator_module = _load_modules()

    class Device:
        device_id = "device"
        dpid = ("4", "8", "14")
        ip_address = "192.0.2.1"


        async def async_discover_local(self):
            return True
        async def async_get_state(self, _dpids=None):
            return {"4": 48, "8": 215}

    instance = object.__new__(coordinator_module.CozyLifeCoordinator)
    instance.device = Device()
    instance.classification = types.SimpleNamespace(
        is_environment_sensor=True,
        supports_switch_entities=False,
        is_light=False,
    )
    instance.data = {}
    instance._uses_sensor_polling = True
    instance._current_sensor_response_at = None
    # A previous implementation populated this from model metadata. A response
    # that omits DPID 14 must clear it rather than reusing that assumption.
    instance._effective_report_interval = 3600
    instance._observed_wake_intervals = []

    assert asyncio.run(instance._async_update_data()) == {"4": 48, "8": 215}
    assert instance._effective_report_interval is None
    assert instance.update_interval == timedelta(
        seconds=coordinator_module.SENSOR_CATCH_POLL_INTERVAL
    )

def test_sensor_queries_its_report_interval_when_status_omits_it() -> None:
    """DPID 14 is queried directly while the sensor is awake."""
    _, coordinator_module = _load_modules()

    class Device:
        device_id = "device"
        dpid = ("4", "8", "14")
        ip_address = "192.0.2.1"

        async def async_discover_local(self):
            return True

        async def async_get_state(self, dpids=None):
            if dpids == ["14"]:
                return {"14": 1800}
            return {"4": 48, "8": 215}

    instance = object.__new__(coordinator_module.CozyLifeCoordinator)
    instance.device = Device()
    instance.classification = types.SimpleNamespace(
        is_environment_sensor=True,
        supports_switch_entities=False,
        is_light=False,
    )
    instance.data = {}
    instance._uses_sensor_polling = False
    instance._current_sensor_response_at = None
    instance._effective_report_interval = None
    instance._observed_wake_intervals = []
    instance._schedule_next_sensor_wake = lambda: None

    assert asyncio.run(instance._async_update_data()) == {"4": 48, "8": 215, "14": 1800}
    assert instance._effective_report_interval == 1800


def test_dpid_14_is_vendor_raw_seconds() -> None:
    _, coordinator = _load_modules()

    # The bundled CozyLife panel reads DPID 14 in seconds and writes choices as
    # 1800..28800. Keep declared longer sensor cadences intact too.
    assert coordinator._decode_sensor_report_interval(0) is None
    assert coordinator._decode_sensor_report_interval(600) == 600
    assert coordinator._decode_sensor_report_interval(1800) == 1800
    assert coordinator._decode_sensor_report_interval(3600) == 3600
    assert coordinator._decode_sensor_report_interval(28800) == 28800
    assert coordinator._decode_sensor_report_interval(86400) == 86400


def test_sensor_wake_schedule_learns_the_observed_period() -> None:
    """A stable sensor clock drift must not make every later wake window miss."""
    _, coordinator = _load_modules()

    # Upstream issue #9 records this model waking 3518--3524 seconds after a
    # DPID 14 value of 3600. Median keeps an occasional late/missed sample out.
    assert coordinator._scheduled_sensor_wake_interval(
        3600, [3521, 3522, 3518, 3524]
    ) == 3522
    assert coordinator._scheduled_sensor_wake_interval(3600, [7200]) == 3600

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
    instance._timeout = 1.0
    instance._connect = timeout_connect
    instance._disconnect = lambda: None

    assert asyncio.run(instance._send_receive(2, {"attr": [1]})) is None


def test_timeout_starts_sensor_catch_polling_and_retains_data(monkeypatch) -> None:
    """A missed wake must retry quickly instead of waiting another cycle."""
    _, coordinator_module = _load_modules()

    class Device:
        ip_address = "192.0.2.1"
        device_id = "device"
        dpid = ("4", "8", "9", "14")

        async def async_get_state(self):
            return None

        async def async_discover_local(self, *, force=False):
            return True

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
    instance._observed_wake_intervals = []
    instance._first_success_wait_logged = False
    instance._missed_expected_wake_logged = False
    instance._fallback_logged = False
    monkeypatch.setattr(
        coordinator_module.dt_util,
        "utcnow",
        lambda: datetime(2026, 10, 14, tzinfo=timezone.utc),
    )

    assert asyncio.run(instance._async_update_data()) == {"4": 48, "8": 215}
    assert instance.update_interval == timedelta(
        seconds=coordinator_module.SENSOR_CATCH_POLL_INTERVAL
    )


def test_direct_home_appliances_with_dpid_1_get_one_power_switch() -> None:
    """Catalogued appliance DPID 1 is the local power control; doorbells are not."""
    discovery, _ = _load_modules()

    for pid in ("CJjAvl", "IRxsfm", "6jR5If", "e5Riuv"):
        model = discovery.get_model_info(pid)
        assert model is not None
        classified = discovery.classify_device(pid, model.type_code, model.dpids)
        assert classified.is_direct_local
        assert classified.supports_switch_entities
        assert classified.switch_entity_count == 1

    doorbell = discovery.get_model_info("byf5sk")
    assert doorbell is not None
    assert not discovery.classify_device(
        "byf5sk", doorbell.type_code, doorbell.dpids
    ).supports_switch_entities


def test_direct_door_and_water_sensors_expose_vendor_battery_dpid() -> None:
    """DPID 9 is the APK-confirmed battery percentage field for sensors."""
    discovery, _ = _load_modules()

    door = discovery.classify_device("0iDvw9", "03", ["7", "9", "27", "28", "29"])
    water = discovery.classify_device("y9DaDw", "03", ["9", "10"])

    assert door.is_direct_local and door.is_sensor_category
    assert water.is_direct_local and water.is_sensor_category
    assert not door.is_environment_sensor and not water.is_environment_sensor
    assert door.has_battery_sensor and water.has_battery_sensor

def test_temperature_humidity_profile_keeps_its_full_entity_set() -> None:
    """Sleeping-profile cache gaps must not hide known sensor entities."""
    discovery, _ = _load_modules()
    classified = discovery.classify_device("Z4tRml", "03", ["8"])

    assert classified.is_environment_sensor
    assert classified.has_temperature_sensor
    assert classified.has_humidity_sensor
    assert classified.has_battery_sensor
