"""Device discovery and classification helpers for CozyLife Local."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from homeassistant.core import HomeAssistant

from .const import (
    BRIGHT,
    HUE,
    KNOWN_SENSOR_PIDS,
    LIGHT_TYPE_CODE,
    PLUG_CURRENT,
    PLUG_ENERGY,
    PLUG_POWER,
    PLUG_VOLTAGE,
    RGB_LIGHT_TYPE_CODE,
    SAT,
    SENSOR_BATTERY,
    SENSOR_HUMIDITY,
    SENSOR_TEMPERATURE,
    SENSOR_TYPE_CODE,
    SWITCH,
    SWITCH_TYPE_CODE,
    TEMP,
)

_LOGGER = logging.getLogger(__name__)

_MODEL_PATH = Path(__file__).with_name("model.json")
_METERING_DPIDS = {PLUG_ENERGY, PLUG_CURRENT, PLUG_POWER, PLUG_VOLTAGE}
_SWITCH_GANG_DPIDS = ("2", "4", "6", "8", "10", "12", "14", "16")
_NAMED_GANG_COUNTS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "一": 1,
    "二": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
}


@dataclass(frozen=True)
class ModelInfo:
    """Known model metadata from the bundled CozyLife catalog."""

    product_id: str
    type_code: str
    network_type: str
    type_name: str
    model_name: str | None
    dpids: frozenset[str]


@dataclass(frozen=True)
class DeviceClassification:
    """Normalized device classification used by all platforms."""

    pid: str | None
    device_type_code: str | None
    effective_type_code: str | None
    source: str
    is_local_transport: bool
    is_environment_sensor: bool
    is_sensor_category: bool
    is_light: bool
    is_switch: bool
    is_outlet: bool
    is_generic_power_device: bool
    is_known_motor: bool
    supports_switch_entities: bool
    supports_plug_metering: bool
    switch_entity_count: int
    model_name: str | None = None


@lru_cache(maxsize=1)
def _load_model_catalog() -> dict[str, ModelInfo]:
    """Load bundled CozyLife model metadata keyed by product id."""
    try:
        with _MODEL_PATH.open(encoding="utf-8") as model_file:
            data: dict[str, Any] = json.load(model_file)
    except (OSError, json.JSONDecodeError) as err:
        _LOGGER.warning("Failed to load CozyLife model catalog: %s", err)
        return {}

    catalog: dict[str, ModelInfo] = {}
    for category in data.get("info", {}).get("list", []):
        type_code = str(category.get("device_type_code") or category.get("c") or "")
        type_name = str(
            category.get("device_type_name_en")
            or category.get("device_type_name")
            or category.get("n")
            or ""
        )
        for model in category.get("device_model") or category.get("m") or []:
            product_id = model.get("device_product_id") or model.get("pid")
            if not product_id:
                continue

            catalog[str(product_id)] = ModelInfo(
                product_id=str(product_id),
                type_code=type_code,
                network_type=str(model.get("net") or ""),
                type_name=type_name,
                model_name=(
                    model.get("device_model_name_en")
                    or model.get("device_model_name")
                    or model.get("n")
                ),
                dpids=frozenset(str(dpid) for dpid in model.get("dpid", [])),
            )

    return catalog


async def async_load_model_catalog(hass: HomeAssistant) -> None:
    """Load bundled model metadata outside the event loop."""
    await hass.async_add_executor_job(_load_model_catalog)


def get_model_info(pid: str | None) -> ModelInfo | None:
    """Return bundled model metadata for a product id if known."""
    if not pid:
        return None
    return _load_model_catalog().get(pid)


def get_wifi_environment_sensor_models() -> tuple[ModelInfo, ...]:
    """Return APK-catalogued Wi-Fi temperature/humidity sensor profiles."""
    required = {SENSOR_HUMIDITY, SENSOR_TEMPERATURE, SENSOR_BATTERY}
    return tuple(
        sorted(
            (
                model
                for model in _load_model_catalog().values()
                if model.network_type == "02" and required <= model.dpids
            ),
            key=lambda model: (model.model_name or "", model.product_id),
        )
    )


def _count_from_model_name(name: str | None) -> int | None:
    if not name:
        return None

    lower_name = name.lower()
    numeric_match = re.search(r"\b([1-8])[- ]?(?:gang|way)\b", lower_name)
    if numeric_match:
        return int(numeric_match.group(1))

    for word, count in _NAMED_GANG_COUNTS.items():
        if re.search(rf"\b{word}[- ]?(?:gang|way)\b", lower_name):
            return count
        if f"{word}路" in name:
            return count

    return None


def detect_switch_entity_count(
    dpids: list[str] | tuple[str, ...] | set[str] | frozenset[str] | None,
    model_name: str | None = None,
) -> int:
    """Return the number of controllable switch bits exposed by DPID 1."""
    dpid_set = {str(dpid) for dpid in (dpids or [])}
    if SWITCH not in dpid_set:
        return 0

    count = 0
    for dpid in _SWITCH_GANG_DPIDS:
        if dpid not in dpid_set:
            break
        count += 1

    if count:
        return count

    return _count_from_model_name(model_name) or 1


def classify_device(
    pid: str | None,
    device_type_code: str | None,
    dpids: list[str] | tuple[str, ...] | set[str] | None,
) -> DeviceClassification:
    """Classify a CozyLife device once so platforms do not disagree."""
    dpid_set = {str(dpid) for dpid in (dpids or [])}
    model_info = get_model_info(pid)
    model_name = model_info.model_name if model_info else None

    # The bundled APK catalog explicitly identifies ``01`` sensor products as
    # Bluetooth and ``04`` products as Zigbee.  Neither can use this local
    # TCP/UDP transport.  Keep unknown catalog values eligible so a catalog
    # metadata gap cannot make an already-working local device disappear.
    is_local_transport = (
        model_info is None or model_info.network_type not in {"01", "04"}
    )

    # Sensor category ``03`` contains Wi-Fi, Bluetooth, and Zigbee products.
    # The APK catalog identifies the Wi-Fi family as network type ``02``.
    # Require that sensor category as well as its complete
    # temperature/humidity/battery profile: electrical products can reuse
    # these DPIDs and must remain relay devices. Retain the known PID as an
    # offline-catalog fallback.
    is_environment_sensor = (
        is_local_transport
        and (
            pid in KNOWN_SENSOR_PIDS
            or (
                model_info is not None
                and model_info.type_code == SENSOR_TYPE_CODE
                and model_info.network_type == "02"
                and {SENSOR_HUMIDITY, SENSOR_TEMPERATURE, SENSOR_BATTERY}
                <= dpid_set
            )
        )
    )

    if model_info:
        effective_type_code = model_info.type_code
        source = "catalog"
    else:
        effective_type_code = device_type_code
        source = "device"

    is_known_motor = bool(model_info and model_info.type_name.lower() == "motor")
    is_sensor_category = bool(model_info and model_info.type_name.lower() == "sensor")
    is_outlet = False
    is_generic_power_device = False
    has_light_capability = (
        TEMP in dpid_set
        or BRIGHT in dpid_set
        or (HUE in dpid_set and SAT in dpid_set)
    )

    # Legacy compatibility: some existing RGB lights report type 02. Only use
    # that fallback when the device also exposes light capability DPIDs.
    is_light = (
        is_local_transport
        and not is_environment_sensor
        and SWITCH in dpid_set
        and (
            effective_type_code == LIGHT_TYPE_CODE
            or (
                not model_info
                and device_type_code == RGB_LIGHT_TYPE_CODE
                and has_light_capability
            )
        )
    )

    is_switch = (
        is_local_transport
        and not is_environment_sensor
        and effective_type_code == SWITCH_TYPE_CODE
    )

    supports_switch_entities = (
        not is_environment_sensor
        and SWITCH in dpid_set
        and not is_known_motor
        and not (is_light and has_light_capability)
        and is_switch
    )
    switch_entity_count = (
        detect_switch_entity_count(dpid_set, model_name)
        if supports_switch_entities
        else 0
    )
    supports_plug_metering = (
        is_switch
        and SWITCH in dpid_set
        and bool(_METERING_DPIDS & dpid_set)
    )

    return DeviceClassification(
        pid=pid,
        device_type_code=device_type_code,
        effective_type_code=effective_type_code,
        source=source,
        is_local_transport=is_local_transport,
        is_environment_sensor=is_environment_sensor,
        is_sensor_category=is_sensor_category,
        is_light=is_light,
        is_switch=is_switch,
        is_outlet=is_outlet,
        is_generic_power_device=is_generic_power_device,
        is_known_motor=is_known_motor,
        supports_switch_entities=supports_switch_entities,
        supports_plug_metering=supports_plug_metering,
        switch_entity_count=switch_entity_count,
        model_name=model_name,
    )
