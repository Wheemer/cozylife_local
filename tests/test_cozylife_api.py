"""Protocol-level tests for the local CozyLife client."""

from __future__ import annotations

import asyncio
import importlib
import sys
import types
from pathlib import Path
from unittest.mock import AsyncMock

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

def test_discovery_uses_apk_msg_envelope_and_matching_did() -> None:
    payload = {
        "cmd": 0,
        "msg": {"did": "expected-device", "ip": "192.0.2.44", "pid": "Z4tRml"},
    }

    assert api.CozyLifeDevice._discovery_ip(payload, "expected-device") == "192.0.2.44"
    assert api.CozyLifeDevice._discovery_ip(payload, "other-device") is None


def test_discovery_rejects_missing_or_top_level_ip() -> None:
    assert api.CozyLifeDevice._discovery_ip(
        {"msg": {"did": "expected-device"}}, "expected-device"
    ) is None
    assert api.CozyLifeDevice._discovery_ip(
        {"did": "expected-device", "ip": "192.0.2.44"}, "expected-device"
    ) is None
