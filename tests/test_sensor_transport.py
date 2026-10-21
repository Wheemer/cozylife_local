"""Protocol checks for the APK-derived sleeping-sensor transport."""

from __future__ import annotations

import asyncio
import json
import sys
import time
import types
from pathlib import Path
from unittest.mock import patch

COMPONENT_DIR = Path(__file__).parents[1] / "custom_components" / "cozylife_local"


def _load_api():
    """Load the API module without importing Home Assistant's package setup."""
    homeassistant = types.ModuleType("homeassistant")
    core = types.ModuleType("homeassistant.core")
    core.HomeAssistant = object
    core.callback = lambda func: func
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
    config_entries.SOURCE_INTEGRATION_DISCOVERY = "integration_discovery"
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


def _load_background_discovery():
    """Load the shared UDP service with the minimal HA symbols it uses."""
    _load_api()
    homeassistant_const = types.ModuleType("homeassistant.const")
    homeassistant_const.EVENT_HOMEASSISTANT_STOP = "homeassistant_stop"
    sys.modules["homeassistant.const"] = homeassistant_const
    config_entries = types.ModuleType("homeassistant.config_entries")
    config_entries.SOURCE_INTEGRATION_DISCOVERY = "integration_discovery"
    sys.modules["homeassistant.config_entries"] = config_entries
    helpers = types.ModuleType("homeassistant.helpers")
    discovery_flow = types.ModuleType("homeassistant.helpers.discovery_flow")
    discovery_flow.async_create_flow = lambda *_args, **_kwargs: None
    sys.modules["homeassistant.helpers"] = helpers
    sys.modules["homeassistant.helpers.discovery_flow"] = discovery_flow
    sys.modules.pop("cozylife_local.background_discovery", None)
    from cozylife_local import background_discovery

    return background_discovery


def _load_config_flow():
    """Load the config flow with only its startup interfaces stubbed."""
    _load_api()
    config_entries = types.ModuleType("homeassistant.config_entries")

    class ConfigFlow:
        def __init_subclass__(cls, **_kwargs) -> None:
            return None

    config_entries.ConfigEntry = object
    config_entries.ConfigFlow = ConfigFlow
    config_entries.OptionsFlow = object
    config_entries.CONN_CLASS_LOCAL_POLL = "local_poll"
    sys.modules["homeassistant.config_entries"] = config_entries
    data_entry_flow = types.ModuleType("homeassistant.data_entry_flow")
    data_entry_flow.FlowResult = dict
    sys.modules["homeassistant.data_entry_flow"] = data_entry_flow
    vol = types.ModuleType("voluptuous")
    vol.Schema = lambda value: value
    vol.Optional = lambda value, **_kwargs: value
    vol.Required = lambda value, **_kwargs: value
    vol.All = lambda *values: values
    vol.Range = lambda **_kwargs: object()
    vol.In = lambda value: value
    sys.modules["voluptuous"] = vol
    background_discovery = types.ModuleType("cozylife_local.background_discovery")

    async def async_get_background_discovery(_hass):
        return None

    background_discovery.async_get_background_discovery = async_get_background_discovery
    sys.modules["cozylife_local.background_discovery"] = background_discovery
    discovery = types.ModuleType("cozylife_local.discovery")
    discovery.async_load_model_catalog = lambda _hass: None
    discovery.classify_device = lambda *_args: None
    discovery.get_model_info = lambda _pid: None
    sys.modules["cozylife_local.discovery"] = discovery
    sys.modules.pop("cozylife_local.config_flow", None)
    from cozylife_local import config_flow

    # The config-flow module has captured these references. Remove the
    # temporary modules so later tests import the actual catalog and UDP code.
    sys.modules.pop("cozylife_local.background_discovery", None)
    sys.modules.pop("cozylife_local.discovery", None)
    return config_flow


def test_config_flow_creates_a_no_fields_discovery_controller() -> None:
    """Adding the integration creates its persistent discovery controller."""
    config_flow = _load_config_flow()

    async def exercise() -> None:
        flow = object.__new__(config_flow.CozyLifeConfigFlow)
        flow.hass = object()
        flow.async_show_form = lambda **kwargs: kwargs
        unique_ids: list[str] = []
        entries: list[dict] = []

        async def set_unique_id(unique_id: str) -> None:
            unique_ids.append(unique_id)

        flow.async_set_unique_id = set_unique_id
        flow._abort_if_unique_id_configured = lambda: None
        flow.async_create_entry = lambda **kwargs: entries.append(kwargs) or kwargs

        form = await flow.async_step_user()
        result = await flow.async_step_user({})

        assert form["step_id"] == "user"
        assert form["data_schema"] == {}
        assert unique_ids == ["cozylife_local_discovery_controller"]
        assert entries == [
            {
                "title": "CozyLife Local",
                "data": {"entry_type": "discovery_controller"},
            }
        ]
        assert result == entries[0]

    asyncio.run(exercise())


def test_config_flow_import_creates_a_discovery_controller() -> None:
    """A legacy device-only setup receives one controller on upgrade."""
    config_flow = _load_config_flow()

    async def exercise() -> None:
        flow = object.__new__(config_flow.CozyLifeConfigFlow)
        unique_ids: list[str] = []
        entries: list[dict] = []

        async def set_unique_id(unique_id: str) -> None:
            unique_ids.append(unique_id)

        flow.async_set_unique_id = set_unique_id
        flow._abort_if_unique_id_configured = lambda: None
        flow.async_create_entry = lambda **kwargs: entries.append(kwargs) or kwargs

        result = await flow.async_step_import({"entry_type": "discovery_controller"})

        assert unique_ids == ["cozylife_local_discovery_controller"]
        assert entries == [
            {
                "title": "CozyLife Local",
                "data": {"entry_type": "discovery_controller"},
            }
        ]
        assert result == entries[0]

    asyncio.run(exercise())


def test_udp_discovery_shows_normal_confirmation_before_adding() -> None:
    """A UDP candidate follows Home Assistant's ordinary Add-device flow."""
    config_flow = _load_config_flow()

    async def exercise() -> None:
        flow = object.__new__(config_flow.CozyLifeConfigFlow)
        unique_ids: list[str] = []
        forms: list[dict] = []

        async def set_unique_id(unique_id: str) -> None:
            unique_ids.append(unique_id)

        flow.async_set_unique_id = set_unique_id
        flow._abort_if_unique_id_configured = lambda: None
        flow.hass = object()
        flow.async_show_form = lambda **kwargs: forms.append(kwargs) or kwargs

        async def load_catalog(_hass) -> None:
            return None

        with (
            patch.object(config_flow, "async_load_model_catalog", load_catalog),
            patch.object(config_flow, "get_model_info", lambda _pid: None),
        ):
            result = await flow.async_step_integration_discovery(
                {"device_id": "plug-1", "ip_address": "10.0.0.44"}
            )

        assert unique_ids == ["plug-1"]
        assert flow._udp_discovery_info == {
            "device_id": "plug-1", "ip_address": "10.0.0.44"
        }
        assert result["step_id"] == "confirm_discovery"
        assert forms == [result]

    asyncio.run(exercise())


def test_confirming_udp_sleeping_sensor_needs_no_tcp_handshake() -> None:
    """The card's Add action uses saved UDP identity for a sleeping sensor."""
    config_flow = _load_config_flow()

    async def exercise() -> None:
        flow = object.__new__(config_flow.CozyLifeConfigFlow)
        flow.hass = object()
        entries: list[dict] = []
        tcp_handshakes: list[object] = []
        cards: list[dict] = []
        unique_ids: list[str] = []

        async def set_unique_id(unique_id: str) -> None:
            unique_ids.append(unique_id)

        async def load_catalog(_hass) -> None:
            return None

        async def tcp_handshake(*_args) -> dict:
            tcp_handshakes.append(True)
            return {"type": "create_entry"}

        flow.async_set_unique_id = set_unique_id
        flow._abort_if_unique_id_configured = lambda: None
        flow.async_create_entry = lambda **kwargs: entries.append(kwargs) or kwargs
        flow.async_show_form = lambda **kwargs: cards.append(kwargs) or kwargs
        flow._async_create_entry_from_ip = tcp_handshake
        model = types.SimpleNamespace(
            model_name="CozyLife Temp/Humidity",
            type_code="03",
            dpids=frozenset({"4", "8", "9"}),
        )
        classification = types.SimpleNamespace(
            is_environment_sensor=True,
            is_local_transport=True,
        )

        with (
            patch.object(config_flow, "async_load_model_catalog", load_catalog),
            patch.object(config_flow, "get_model_info", lambda _pid: model),
            patch.object(
                config_flow,
                "classify_device",
                lambda *_args: classification,
            ),
        ):
            discovery_card = await flow.async_step_integration_discovery(
                {
                    "device_id": "sensor-1", "ip_address": "10.0.0.115",
                    "pid": "Z4tRml", "device_type_code": "03",
                }
            )
            result = await flow.async_step_confirm_discovery({})

        assert tcp_handshakes == []
        assert unique_ids == ["sensor-1"]
        assert discovery_card["step_id"] == "confirm_discovery"
        assert cards == [discovery_card]
        assert entries == [
            {
                "title": "CozyLife Temp/Humidity",
                "data": {
                    "ip_address": "10.0.0.115",
                    "device_id": "sensor-1",
                    "pid": "Z4tRml",
                    "device_type_code": "03",
                    "dpids": ["4", "8", "9"],
                },
            }
        ]
        assert result == entries[0]

    asyncio.run(exercise())


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


def test_shared_udp_discovery_uses_the_apk_burst_on_one_receive_socket() -> None:
    """The one shared listener preserves the APK's 17 request UDP session."""
    background_discovery = _load_background_discovery()
    assert background_discovery.DISCOVERY_BROADCAST_INTERVAL_SECONDS == 0.5

    class FakeTransport:
        def __init__(self) -> None:
            self.sent: list[tuple[bytes, tuple[str, int]]] = []

        def sendto(self, data: bytes, address: tuple[str, int]) -> None:
            self.sent.append((data, address))

    transport = FakeTransport()
    service = object.__new__(background_discovery.CozyLifeBackgroundDiscovery)
    service._transport = transport
    service._send_broadcasts(
        background_discovery.APK_NETWORK_CHANGE_BROADCAST_COUNT
    )
    assert len(transport.sent) == 17
    for frame, address in transport.sent:
        assert address == ("255.255.255.255", 6095)
        assert frame.endswith(b"\r\n")
        request = json.loads(frame.decode())
        assert request["cmd"] == 0
        assert request["pv"] == 0
        assert isinstance(request["sn"], str)
        assert request["msg"] == {}


def test_shared_apk_udp_cache_keeps_valid_device_identity_and_address() -> None:
    """The shared listener accepts the APK's ``did``/``ip`` reply shape."""
    background_discovery = _load_background_discovery()
    found = []
    protocol = background_discovery._ApkUdpDiscoveryProtocol(found.append)
    protocol.datagram_received(
        b'{"msg":{"did":"plug-1","ip":"10.0.0.41","pid":"hjk1jk","dtp":"05"}}\r\n',
        ("10.0.0.41", 6095),
    )
    protocol.datagram_received(
        b'{"msg":{"did":"plug-1","ip":"10.0.0.42"}}\r\n',
        # The APK caches msg.ip, not the UDP sender address.
        ("10.0.0.99", 6095),
    )
    protocol.datagram_received(
        b'{"msg":{"did":"bad","ip":"not-an-address"}}\r\n',
        ("10.0.0.99", 6095),
    )

    # The callback receives every valid reply; the service itself indexes the
    # cache by DID and replaces stale addresses.
    assert [(item.device_id, item.ip_address) for item in found] == [
        ("plug-1", "10.0.0.41"),
        ("plug-1", "10.0.0.42"),
    ]


def test_shared_udp_listener_routes_a_known_did_without_a_second_socket() -> None:
    """Known entries receive the APK-advertised address from the one cache."""
    background_discovery = _load_background_discovery()

    async def exercise() -> None:
        received: list[str] = []

        async def handle_address(ip_address: str) -> None:
            received.append(ip_address)

        service = object.__new__(background_discovery.CozyLifeBackgroundDiscovery)
        service._devices = {}
        service._entry_callbacks = {"plug-1": handle_address}
        service._address_callbacks = {}
        service.hass = types.SimpleNamespace(async_create_task=asyncio.create_task)

        service._handle_device(
            background_discovery.UdpDiscoveredDevice(
                device_id="plug-1",
                ip_address="10.0.0.42",
                pid="hjk1jk",
                device_type_code="05",
            )
        )
        await asyncio.sleep(0)

        assert received == ["10.0.0.42"]
        assert service._devices["plug-1"][0].ip_address == "10.0.0.42"

    asyncio.run(exercise())


def test_shared_udp_listener_creation_is_serialized() -> None:
    """Concurrent entry setup must not create two UDP receive sockets."""
    background_discovery = _load_background_discovery()

    async def exercise() -> None:
        stop_callbacks: list[object] = []
        hass = types.SimpleNamespace(
            data={},
            bus=types.SimpleNamespace(
                async_listen_once=lambda _event, callback: stop_callbacks.append(callback)
            ),
        )
        starts: list[object] = []

        async def start_once(service) -> None:
            starts.append(service)
            await asyncio.sleep(0)
            service._transport = object()

        with patch.object(
            background_discovery.CozyLifeBackgroundDiscovery,
            "async_start",
            start_once,
        ):
            first, second = await asyncio.gather(
                background_discovery.async_get_background_discovery(hass),
                background_discovery.async_get_background_discovery(hass),
            )

        assert first is second
        assert starts == [first]
        assert len(stop_callbacks) == 1

    asyncio.run(exercise())


def test_shared_udp_listener_stop_hook_closes_config_flow_listener() -> None:
    """A listener started before the first entry still shuts down cleanly."""
    background_discovery = _load_background_discovery()

    async def exercise() -> None:
        stop_callbacks: list[object] = []
        scheduled: list[object] = []
        hass = types.SimpleNamespace(
            data={},
            bus=types.SimpleNamespace(
                async_listen_once=lambda _event, callback: stop_callbacks.append(callback)
            ),
            async_create_task=scheduled.append,
        )

        await background_discovery.async_get_background_discovery(hass, start=False)
        assert len(stop_callbacks) == 1

        stop_callbacks[0](None)
        assert len(scheduled) == 1
        await scheduled[0]

    asyncio.run(exercise())


def test_shared_udp_listener_retries_after_socket_start_failure() -> None:
    """A transient UDP bind failure must not poison later entry setup."""
    background_discovery = _load_background_discovery()

    async def exercise() -> None:
        hass = types.SimpleNamespace(
            data={},
            bus=types.SimpleNamespace(async_listen_once=lambda *_args: None),
        )
        attempts = 0

        async def start_with_one_failure(service) -> None:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise OSError("temporary socket failure")
            service._transport = object()

        with patch.object(
            background_discovery.CozyLifeBackgroundDiscovery,
            "async_start",
            start_with_one_failure,
        ):
            try:
                await background_discovery.async_get_background_discovery(hass)
            except OSError:
                pass
            else:
                raise AssertionError("first socket attempt should fail")

            service = await background_discovery.async_get_background_discovery(hass)

        assert attempts == 2
        assert service.is_running

    asyncio.run(exercise())


def test_shared_udp_listener_uses_home_assistant_discovery_flow_helper() -> None:
    """An unconfigured announcement becomes one native discovery flow."""
    background_discovery = _load_background_discovery()
    calls: list[tuple[object, str, dict, dict]] = []
    hass = types.SimpleNamespace(
        config_entries=types.SimpleNamespace(async_entries=lambda _domain: [])
    )
    service = object.__new__(background_discovery.CozyLifeBackgroundDiscovery)
    service.hass = hass
    service._devices = {}
    service._entry_callbacks = {}
    service._address_callbacks = {}

    def create_flow(flow_hass, domain, *, context, data) -> None:
        calls.append((flow_hass, domain, context, data))

    with patch.object(
        background_discovery.discovery_flow,
        "async_create_flow",
        create_flow,
    ):
        service._handle_device(
            background_discovery.UdpDiscoveredDevice(
                device_id="new-device",
                ip_address="10.0.0.44",
                pid="hjk1jk",
                device_type_code="05",
            )
        )

    assert calls == [
        (
            hass,
            "cozylife_local",
            {"source": "integration_discovery"},
            {
                "device_id": "new-device",
                "ip_address": "10.0.0.44",
                "pid": "hjk1jk",
                "device_type_code": "05",
            },
        )
    ]


def test_udp_discovery_shutdown_cancels_its_task_and_closes_the_socket() -> None:
    """Stopping Home Assistant leaves no discovery socket or task behind."""
    background_discovery = _load_background_discovery()

    async def exercise() -> None:
        class FakeTransport:
            closed = False

            def close(self) -> None:
                self.closed = True

        async def wait_forever() -> None:
            await asyncio.Event().wait()

        transport = FakeTransport()
        service = object.__new__(background_discovery.CozyLifeBackgroundDiscovery)
        service._transport = transport
        service._broadcast_task = asyncio.create_task(wait_forever())

        await service.async_stop()

        assert transport.closed
        assert service._transport is None
        assert service._broadcast_task is None
        assert not service.is_running

    asyncio.run(exercise())


def test_udp_announcement_routes_to_provisional_sensor_address() -> None:
    """A sleeping sensor's reply wakes its entry without making another card."""
    background_discovery = _load_background_discovery()
    async def exercise() -> None:
        received: list[str] = []
        flow_calls: list[object] = []

        async def handle_address(ip_address: str) -> None:
            received.append(ip_address)

        service = object.__new__(background_discovery.CozyLifeBackgroundDiscovery)
        service.hass = types.SimpleNamespace(
            async_create_task=asyncio.create_task,
            config_entries=types.SimpleNamespace(async_entries=lambda _domain: []),
        )
        service._devices = {}
        service._entry_callbacks = {}
        service._address_callbacks = {"10.0.0.115": handle_address}

        with patch.object(
            background_discovery.discovery_flow,
            "async_create_flow",
            lambda *_args, **_kwargs: flow_calls.append(True),
        ):
            service._handle_device(
                background_discovery.UdpDiscoveredDevice(
                    device_id="verified-sensor-did",
                    ip_address="10.0.0.115",
                    pid="Z4tRml",
                    device_type_code="03",
                )
            )

        await asyncio.sleep(0)
        assert received == ["10.0.0.115"]
        assert flow_calls == []
        assert service._devices["verified-sensor-did"][0].ip_address == "10.0.0.115"

    asyncio.run(exercise())


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


def test_all_catalogued_wifi_electrical_relays_and_meters_get_local_entities() -> None:
    """Keep every APK Wi-Fi electrical relay on the supported local path."""
    _load_api()
    from cozylife_local.discovery import _load_model_catalog, classify_device

    electrical_models = [
        model
        for model in _load_model_catalog().values()
        if model.type_code == "00" and model.network_type == "02"
    ]
    assert electrical_models

    meter_dpids = {"26", "27", "28", "29"}
    for model in electrical_models:
        classification = classify_device(
            model.product_id,
            model.type_code,
            model.dpids,
        )
        if "1" in model.dpids:
            assert classification.supports_switch_entities, model.product_id
        if meter_dpids & model.dpids:
            assert classification.supports_plug_metering, model.product_id


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
                "ip_address": "10.0.0.115",
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
        unregistered: list[str] = []
        unregistered_addresses: list[str] = []
        registered: list[tuple[str, object]] = []
        discovery = types.SimpleNamespace(
            unregister_device=unregistered.append,
            unregister_address=unregistered_addresses.append,
            register_device=lambda device_id, callback: registered.append(
                (device_id, callback)
            ),
        )

        async def get_discovery(_hass):
            return discovery

        coordinator._identity_refresh_task = object()

        with patch("cozylife_local.coordinator.async_get_background_discovery", get_discovery):
            await coordinator._async_promote_sensor_identity()

        assert updates == [
            {
                "device_id": "76608860c4d7fdd94485",
                "ip_address": "10.0.0.115",
                "pid": "Z4tRml",
                "device_type_code": "02",
                "dpids": ["4", "8", "9", "14"],
            }
        ]
        assert unregistered == ["sleeping_sensor_Z4tRml_10.0.0.115"]
        assert unregistered_addresses == ["10.0.0.115"]
        assert registered == [
            ("76608860c4d7fdd94485", coordinator.async_handle_discovered_address)
        ]
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
