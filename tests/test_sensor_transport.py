"""Protocol checks for the APK-derived sleeping-sensor transport."""

from __future__ import annotations

import asyncio
import json
import sys
import time
import types
from pathlib import Path


COMPONENT_DIR = Path(__file__).parents[1] / "custom_components" / "cozylife_local"


def _load_api():
    """Load the API module without importing Home Assistant's package setup."""
    homeassistant = types.ModuleType("homeassistant")
    core = types.ModuleType("homeassistant.core")
    core.HomeAssistant = object
    sys.modules["homeassistant"] = homeassistant
    sys.modules["homeassistant.core"] = core
    package = types.ModuleType("cozylife_local")
    package.__path__ = [str(COMPONENT_DIR)]
    sys.modules["cozylife_local"] = package
    from cozylife_local.cozylife_api import CozyLifeDevice

    return CozyLifeDevice


def _load_coordinator():
    """Load coordinator logic with the minimal Home Assistant interfaces it uses."""
    _load_api()
    config_entries = types.ModuleType("homeassistant.config_entries")
    config_entries.ConfigEntry = object
    helpers = types.ModuleType("homeassistant.helpers")
    update_coordinator = types.ModuleType("homeassistant.helpers.update_coordinator")

    class DataUpdateCoordinator:
        @classmethod
        def __class_getitem__(cls, _item):
            return cls

    update_coordinator.DataUpdateCoordinator = DataUpdateCoordinator
    update_coordinator.UpdateFailed = RuntimeError
    sys.modules["homeassistant.config_entries"] = config_entries
    sys.modules["homeassistant.helpers"] = helpers
    sys.modules["homeassistant.helpers.update_coordinator"] = update_coordinator
    from cozylife_local.coordinator import CozyLifeCoordinator

    return CozyLifeCoordinator


def test_status_write_uses_apk_frame_and_dispatches_unsolicited_report() -> None:
    """The local writer sends attr:[1] and the reader keeps every report."""
    CozyLifeDevice = _load_api()

    async def exercise() -> None:
        reports: list[dict] = []

        async def handler(reader, writer) -> None:
            request = json.loads((await reader.readline()).decode())
            assert request["cmd"] == 2
            assert request["pv"] == 0
            assert request["msg"] == {"attr": [1]}
            writer.write(
                b'{"cmd":2,"sn":"device-event","msg":{"data":{"4":48,"8":291}}}\r\n'
            )
            await writer.drain()
            writer.close()
            await writer.wait_closed()

        server = await asyncio.start_server(handler, "127.0.0.1", 5555)
        device = CozyLifeDevice("127.0.0.1", timeout=1.0)
        device.set_message_listener(reports.append)
        try:
            assert await device.async_send_status()
            await asyncio.sleep(0.05)
            assert reports == [
                {"cmd": 2, "sn": "device-event", "msg": {"data": {"4": 48, "8": 291}}}
            ]
        finally:
            await device.async_close()
            server.close()
            await server.wait_closed()

    asyncio.run(exercise())


def test_status_socket_is_recycled_on_the_configured_cadence() -> None:
    """A sleeping sensor gets a fresh TCP connection after thirty seconds."""
    CozyLifeDevice = _load_api()

    async def exercise() -> None:
        connections = 0

        async def handler(reader, writer) -> None:
            nonlocal connections
            connections += 1
            while await reader.readline():
                writer.write(b'{"cmd":2,"sn":"report","msg":{"data":{"4":50}}}\r\n')
                await writer.drain()
            writer.close()
            await writer.wait_closed()

        server = await asyncio.start_server(handler, "127.0.0.1", 5555)
        device = CozyLifeDevice("127.0.0.1", timeout=1.0)
        try:
            assert await device.async_send_status()
            await asyncio.sleep(0.05)
            device._connected_at = time.monotonic() - 30.1
            assert await device.async_send_status()
            await asyncio.sleep(0.05)
            assert connections == 2
        finally:
            await device.async_close()
            server.close()
            await server.wait_closed()

    asyncio.run(exercise())


def test_concurrent_status_requests_share_one_persistent_socket() -> None:
    """Discovery and wake probing cannot open competing sensor sockets."""
    CozyLifeDevice = _load_api()

    async def exercise() -> None:
        connections = 0
        requests: list[dict] = []

        async def handler(reader, writer) -> None:
            nonlocal connections
            connections += 1
            for _ in range(2):
                requests.append(json.loads((await reader.readline()).decode()))
            writer.close()
            await writer.wait_closed()

        server = await asyncio.start_server(handler, "127.0.0.1", 5555)
        device = CozyLifeDevice("127.0.0.1", timeout=1.0)
        try:
            assert all(await asyncio.gather(
                device.async_send_status(), device.async_send_status()
            ))
            await asyncio.sleep(0.05)
            assert connections == 1
            assert [request["msg"] for request in requests] == [
                {"attr": [1]},
                {"attr": [1]},
            ]
        finally:
            await device.async_close()
            server.close()
            await server.wait_closed()

    asyncio.run(exercise())


def test_status_send_reconnects_after_the_peer_closes() -> None:
    """A closed socket is not reused for a later sleeping-sensor wake."""
    CozyLifeDevice = _load_api()

    async def exercise() -> None:
        connections = 0

        async def handler(reader, writer) -> None:
            nonlocal connections
            connections += 1
            await reader.readline()
            writer.close()
            await writer.wait_closed()

        server = await asyncio.start_server(handler, "127.0.0.1", 5555)
        device = CozyLifeDevice("127.0.0.1", timeout=1.0)
        try:
            assert await device.async_send_status()
            await asyncio.sleep(0.05)
            assert await device.async_send_status()
            await asyncio.sleep(0.05)
            assert connections == 2
        finally:
            await device.async_close()
            server.close()
            await server.wait_closed()

    asyncio.run(exercise())


def test_discovery_accepts_only_the_matching_device_and_valid_address() -> None:
    """UDP discovery must not route another CozyLife device into this entry."""
    _load_api()
    from cozylife_local.cozylife_api import _CozyLifeDiscoveryProtocol

    async def exercise() -> None:
        found: list[str] = []

        async def remember(ip_address: str) -> None:
            found.append(ip_address)

        protocol = _CozyLifeDiscoveryProtocol("target-device", remember)
        protocol.datagram_received(
            b'{"msg":{"did":"other-device","ip":"10.0.0.99"}}\r\n',
            ("10.0.0.99", 6095),
        )
        protocol.datagram_received(
            b'{"msg":{"did":"target-device","ip":""}}\r\n',
            ("10.0.0.115", 6095),
        )
        protocol.datagram_received(
            b'{"msg":{"did":"target-device","ip":"10.0.0.115"}}\r\n',
            ("10.0.0.115", 6095),
        )
        await asyncio.sleep(0)
        assert found == ["10.0.0.115"]

    asyncio.run(exercise())


def test_apk_udp_discovery_cache_keeps_valid_device_identity_and_address() -> None:
    """The setup scanner accepts the APK's ``did``/``ip`` UDP reply shape."""
    _load_api()
    from cozylife_local.network_discovery import _ApkUdpDiscoveryProtocol

    protocol = _ApkUdpDiscoveryProtocol()
    protocol.datagram_received(
        b'{"msg":{"did":"plug-1","ip":"10.0.0.41","pid":"hjk1jk","dtp":"05"}}\r\n',
        ("10.0.0.41", 6095),
    )
    protocol.datagram_received(
        b'{"msg":{"did":"plug-1","ip":"10.0.0.42"}}\r\n',
        ("10.0.0.42", 6095),
    )
    protocol.datagram_received(
        b'{"msg":{"did":"bad","ip":"not-an-address"}}\r\n',
        ("10.0.0.99", 6095),
    )

    # Mirrors the APK cache: newer replies replace the older IP for the DID.
    assert protocol.devices == {"plug-1": "10.0.0.42"}


def test_persistent_transport_preserves_existing_info_state_and_set_commands() -> None:
    """The reader-based transport still supports ordinary light/switch calls."""
    CozyLifeDevice = _load_api()

    async def exercise() -> None:
        requests: list[dict] = []

        async def handler(reader, writer) -> None:
            while raw_request := await reader.readline():
                request = json.loads(raw_request.decode())
                requests.append(request)
                if request["cmd"] == 0:
                    message = {"did": "device-1", "pid": "unknown", "dtp": "00"}
                elif request["cmd"] == 2 and request["msg"]["attr"] == [0]:
                    message = {"attr": [1, 11, 18, 19, 20]}
                elif request["cmd"] == 2:
                    message = {"data": {"1": True, "11": 42}}
                else:
                    message = {"data": request["msg"]["data"]}
                response = {"sn": request["sn"], "res": 0, "msg": message}
                writer.write((json.dumps(response) + "\r\n").encode())
                await writer.drain()
            writer.close()
            await writer.wait_closed()

        server = await asyncio.start_server(handler, "127.0.0.1", 5555)
        device = CozyLifeDevice("127.0.0.1", timeout=1.0)
        try:
            assert await device.async_update_device_info()
            assert device.device_id == "device-1"
            assert device.dpid == ["1", "11", "18", "19", "20"]
            assert await device.async_get_state(["1", "11"]) == {"1": True, "11": 42}
            assert await device.async_set_state({"1": False})
            assert len(requests) == 4
            assert requests[-1]["cmd"] == 3
            assert requests[-1]["msg"] == {"attr": [1], "data": {"1": False}}
        finally:
            await device.async_close()
            server.close()
            await server.wait_closed()

    asyncio.run(exercise())


def test_catalog_classification_requires_explicit_local_capabilities() -> None:
    """Do not expose catalog-only lights or non-Wi-Fi sensors as local devices."""
    _load_api()
    from cozylife_local.discovery import classify_device

    known_light = classify_device("asdlks", "01", ["1", "2", "3", "4"])
    catalog_only_light = classify_device("kpfzBm", "01", [])
    bluetooth_light = classify_device(
        "y7274m", "01", ["1", "2", "4", "5", "6", "7", "8", "9"]
    )
    wifi_environment_sensor = classify_device("Z4tRml", "03", ["4", "8", "9"])
    another_wifi_environment_sensor = classify_device("s1AxFq", "03", ["4", "8", "9"])
    zigbee_environment_sensor = classify_device("v2kv9f", "03", ["8", "9"])
    bluetooth_environment_sensor = classify_device("bQ2cGF", "03", ["4", "8", "9"])
    appliance = classify_device("CJjAvl", "05", ["1"])

    assert known_light.is_light
    assert not catalog_only_light.is_light
    assert not bluetooth_light.is_local_transport
    assert not bluetooth_light.is_light
    assert wifi_environment_sensor.is_environment_sensor
    assert wifi_environment_sensor.is_local_transport
    assert another_wifi_environment_sensor.is_environment_sensor
    assert not zigbee_environment_sensor.is_environment_sensor
    assert not zigbee_environment_sensor.is_local_transport
    assert not bluetooth_environment_sensor.is_environment_sensor
    assert not bluetooth_environment_sensor.is_local_transport
    assert not appliance.supports_switch_entities


def test_apk_catalog_profiles_cover_each_supported_local_family() -> None:
    """Keep entity capability assumptions anchored to bundled APK catalog data."""
    _load_api()
    from cozylife_local.discovery import get_model_info

    expected_dpids = {
        # APK light panels: relay, work mode, temperature/brightness controls,
        # countdown, and normal timer.
        "asdlks": {"1", "2", "3", "4", "13", "14"},
        # APK RGB light panel: relay, mode, brightness, hue, and saturation.
        "kilulk": {"1", "2", "4", "5", "6", "13", "14"},
        # APK multi-gang electrical panel: local relay and gang controls.
        "hjhjkk": {"1", "2", "3", "4", "5", "6", "7"},
        # APK plug panel: relay, countdown/timer, plug options, and metering.
        "hjk1jk": {"1", "2", "3", "18", "19"},
        # Explicitly supported Wi-Fi temperature/humidity sensor.
        "Z4tRml": {"4", "8", "9", "14", "24", "25"},
    }

    for pid, required in expected_dpids.items():
        model = get_model_info(pid)
        assert model is not None
        assert required <= model.dpids, pid


def test_manual_sensor_choices_include_only_apk_wifi_profiles() -> None:
    """Manual sleeping-sensor setup must not offer Bluetooth or Zigbee PIDs."""
    _load_api()
    from cozylife_local.discovery import get_wifi_environment_sensor_models

    pids = {model.product_id for model in get_wifi_environment_sensor_models()}
    assert {"Z4tRml", "s1AxFq", "nrQdBz"} <= pids
    assert "bQ2cGF" not in pids
    assert "v2kv9f" not in pids


def test_platforms_exclude_home_assistant_schedule_emulation() -> None:
    """Keep scheduling in Home Assistant automations, not this integration."""
    _load_api()
    from cozylife_local.const import PLATFORMS

    assert "time" not in PLATFORMS


def test_environment_report_accepts_a_valid_partial_measurement() -> None:
    """A valid reading must not be discarded because its paired DPID is absent."""
    CozyLifeCoordinator = _load_coordinator()
    coordinator = object.__new__(CozyLifeCoordinator)
    coordinator.classification = types.SimpleNamespace(is_environment_sensor=True)

    assert coordinator._has_valid_environment_measurements({"8": 221})
    assert coordinator._has_valid_environment_measurements({"4": 48})
    assert not coordinator._has_valid_environment_measurements({"8": 0})
    assert not coordinator._has_valid_environment_measurements({"4": 0})


def test_sleeping_sensor_promotes_verified_local_device_id() -> None:
    """A provisional setup ID is replaced only by a matching local handshake."""
    CozyLifeCoordinator = _load_coordinator()

    async def exercise() -> None:
        coordinator = object.__new__(CozyLifeCoordinator)
        entry = types.SimpleNamespace(
            data={
                "device_id": "sleeping_sensor_Z4tRml_10.0.0.115",
                "pid": "Z4tRml",
                "device_type_code": "03",
                "dpids": ["4", "8", "9"],
            }
        )
        updates: list[dict] = []

        def update_entry(updated_entry, *, data) -> None:
            updated_entry.data = data
            updates.append(data)

        coordinator.hass = types.SimpleNamespace(
            config_entries=types.SimpleNamespace(async_update_entry=update_entry)
        )
        coordinator._entry = entry
        coordinator.device = types.SimpleNamespace(
            ip_address="10.0.0.115",
            pid="Z4tRml",
            device_id="76608860c4d7fdd94485",
            device_type_code="02",
            dpid=["4", "8", "9", "14"],
        )

        async def update_device_info() -> bool:
            return True

        coordinator.device.async_update_device_info = update_device_info
        stopped: list[bool] = []

        async def stop() -> None:
            stopped.append(True)

        coordinator._udp_discovery = types.SimpleNamespace(async_stop=stop)
        restarted: list[bool] = []

        async def start_discovery() -> None:
            restarted.append(True)

        coordinator.async_start_discovery = start_discovery
        coordinator._identity_refresh_task = object()

        await coordinator._async_promote_sensor_identity()

        assert updates == [
            {
                "device_id": "76608860c4d7fdd94485",
                "pid": "Z4tRml",
                "device_type_code": "02",
                "dpids": ["4", "8", "9", "14"],
            }
        ]
        assert stopped == [True]
        assert restarted == [True]
        assert coordinator._identity_refresh_task is None

    asyncio.run(exercise())


def test_sleeping_sensor_rejects_a_mismatched_local_identity() -> None:
    """A response from a different product must not replace the setup identity."""
    CozyLifeCoordinator = _load_coordinator()

    async def exercise() -> None:
        coordinator = object.__new__(CozyLifeCoordinator)
        entry = types.SimpleNamespace(
            data={
                "device_id": "sleeping_sensor_Z4tRml_10.0.0.115",
                "pid": "Z4tRml",
                "device_type_code": "03",
                "dpids": ["4", "8", "9"],
            }
        )
        updates: list[dict] = []
        coordinator.hass = types.SimpleNamespace(
            config_entries=types.SimpleNamespace(
                async_update_entry=lambda *_args, **kwargs: updates.append(kwargs)
            )
        )
        coordinator._entry = entry
        coordinator.device = types.SimpleNamespace(
            ip_address="10.0.0.115",
            pid="other-product",
            device_id="other-device-id",
            device_type_code="00",
            dpid=["1"],
        )

        async def update_device_info() -> bool:
            return True

        coordinator.device.async_update_device_info = update_device_info
        closed: list[bool] = []

        async def close() -> None:
            closed.append(True)

        def restore_from_cache(device_id, pid, device_type_code, dpids) -> None:
            coordinator.device.device_id = device_id
            coordinator.device.pid = pid
            coordinator.device.device_type_code = device_type_code
            coordinator.device.dpid = dpids

        coordinator.device.async_close = close
        coordinator.device.restore_from_cache = restore_from_cache
        coordinator._udp_discovery = None
        coordinator._identity_refresh_task = object()

        await coordinator._async_promote_sensor_identity()

        assert updates == []
        assert closed == [True]
        assert entry.data["device_id"] == "sleeping_sensor_Z4tRml_10.0.0.115"
        assert coordinator.device.pid == "Z4tRml"
        assert coordinator.device.dpid == ["4", "8", "9"]
        assert coordinator._identity_refresh_task is None

    asyncio.run(exercise())
