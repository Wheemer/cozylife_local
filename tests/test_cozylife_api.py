"""Protocol-level tests for the local CozyLife client."""

from __future__ import annotations

import asyncio
import importlib
import sys
import types
from pathlib import Path
from unittest.mock import AsyncMock, Mock

COMPONENT_DIR = Path(__file__).parents[1] / "custom_components" / "cozylife_local"


def _load_api_module():
    homeassistant = types.ModuleType("homeassistant")
    homeassistant_core = types.ModuleType("homeassistant.core")
    homeassistant_core.HomeAssistant = object
    sys.modules.setdefault("homeassistant", homeassistant)
    sys.modules.setdefault("homeassistant.core", homeassistant_core)
    package = types.ModuleType("cozylife_local")
    package.__path__ = [str(COMPONENT_DIR)]
    sys.modules.setdefault("cozylife_local", package)
    return importlib.import_module("cozylife_local.cozylife_api")


api = _load_api_module()


def test_state_query_uses_vendor_status_selector() -> None:
    device = api.CozyLifeDevice("192.0.2.1")
    device._send_receive = AsyncMock(return_value={"data": {"8": 213}})

    result = asyncio.run(device.async_get_state())

    assert result == {"8": 213}
    device._send_receive.assert_awaited_once_with(
        api.CMD_QUERY, {"attr": [api.QUERY_STATUS]}
    )


def test_explicit_dpid_query_is_preserved() -> None:
    device = api.CozyLifeDevice("192.0.2.1")
    device._send_receive = AsyncMock(return_value={"data": {"14": 3600}})

    result = asyncio.run(device.async_get_state(["14"]))

    assert result == {"14": 3600}

    device._send_receive.assert_awaited_once_with(api.CMD_QUERY, {"attr": [14]})


def test_udp_discovery_refreshes_the_cached_ip(monkeypatch) -> None:
    """A matching UDP reply must replace the stale TCP endpoint."""

    async def check_discovery() -> None:
        device = api.CozyLifeDevice("192.0.2.1")
        device.restore_from_cache("device-id", "pid", "type", [])
        loop = asyncio.get_running_loop()
        transport = Mock()

        async def create_endpoint(protocol_factory, **_kwargs):
            protocol = protocol_factory()

            def sendto(message, address):
                assert address == (
                    api.COZYLIFE_DISCOVERY_ADDRESS,
                    api.COZYLIFE_DISCOVERY_PORT,
                )
                assert b'"cmd":0' in message
                assert message.endswith(b"\r\n")
                protocol.datagram_received(
                    b'{"did":"device-id","ip":"192.0.2.42"}',
                    ("192.0.2.42", api.COZYLIFE_DISCOVERY_PORT),
                )

            transport.sendto.side_effect = sendto
            return transport, protocol

        monkeypatch.setattr(loop, "create_datagram_endpoint", create_endpoint)
        assert await device.async_refresh_ip_from_discovery()
        assert device.ip_address == "192.0.2.42"
        transport.close.assert_called_once()

    asyncio.run(check_discovery())
