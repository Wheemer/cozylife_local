"""Helpers for CozyLife switch and plug option DPIDs."""

from __future__ import annotations

from .coordinator import CozyLifeCoordinator
from .discovery import get_model_info


def supported_dpids(coordinator: CozyLifeCoordinator) -> set[str]:
    """Return discovered and catalog-listed DPIDs for this device."""
    dpids = set(coordinator.device.dpid or [])
    model_info = get_model_info(coordinator.device.pid)
    if model_info:
        dpids.update(model_info.dpids)
    return dpids
