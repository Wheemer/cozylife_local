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

        async def handler(reader, _writer) -> None:
            nonlocal connections
            connections += 1
            for _ in range(2):
                requests.append(json.loads((await reader.readline()).decode()))

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
    wifi_environment_sensor = classify_device("Z4tRml", "03", ["4", "8", "9"])
    zigbee_environment_sensor = classify_device("v2kv9f", "03", ["8", "9"])
    appliance = classify_device("CJjAvl", "05", ["1"])

    assert known_light.is_light
    assert not catalog_only_light.is_light
    assert wifi_environment_sensor.is_environment_sensor
    assert not zigbee_environment_sensor.is_environment_sensor
    assert not appliance.supports_switch_entities
