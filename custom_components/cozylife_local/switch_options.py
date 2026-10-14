"""Helpers for CozyLife switch and plug option DPIDs."""

from __future__ import annotations

from .const import LIGHT_COUNTDOWN, LIGHT_NORMAL_TIMER, PLUG_TIMER_SCHEDULE
from .coordinator import CozyLifeCoordinator


def supported_dpids(coordinator: CozyLifeCoordinator) -> set[str]:
    """Return DPIDs actually advertised by this device.

    Product-catalog data is a locator, not an authority for writable controls.
    """
    return {str(dpid) for dpid in (coordinator.device.dpid or [])}

def supports_switch_options(coordinator: CozyLifeCoordinator) -> bool:
    """Return whether the device can expose shared switch option entities."""
    return coordinator.classification.supports_switch_entities


def supports_schedule_options(coordinator: CozyLifeCoordinator) -> bool:
    """Return whether the device can expose HA-backed schedule controls."""
    return (
        supports_switch_options(coordinator)
        and PLUG_TIMER_SCHEDULE in supported_dpids(coordinator)
    )


def supports_light_schedule_options(coordinator: CozyLifeCoordinator) -> bool:
    """Return whether the light can expose HA-backed schedule controls."""
    return (
        coordinator.classification.is_light
        and bool({LIGHT_COUNTDOWN, LIGHT_NORMAL_TIMER} & supported_dpids(coordinator))
    )
