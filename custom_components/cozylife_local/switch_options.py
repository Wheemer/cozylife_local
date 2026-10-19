"""Helpers for CozyLife switch and plug option DPIDs."""

from __future__ import annotations

from .coordinator import CozyLifeCoordinator


def supported_dpids(coordinator: CozyLifeCoordinator) -> set[str]:
    """Return the DPIDs reported by this individual local device."""
    return set(coordinator.device.dpid or [])
