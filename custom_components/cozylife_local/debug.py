"""Safe-to-share summaries for CozyLife local diagnostics."""

from __future__ import annotations

import json
from typing import Any


def summarize_frame(raw_frame: bytes) -> dict[str, Any]:
    """Return protocol shape without local identity or values."""
    try:
        frame = json.loads(raw_frame.decode("utf8").strip())
    except (json.JSONDecodeError, UnicodeDecodeError):
        return {"invalid_frame_bytes": len(raw_frame)}

    summary: dict[str, Any] = {key: frame[key] for key in ("cmd", "res") if key in frame}
    message = frame.get("msg")
    if isinstance(message, dict):
        message_summary: dict[str, Any] = {}
        attributes = message.get("attr")
        if isinstance(attributes, list):
            message_summary["attr"] = attributes
        data = message.get("data")
        if isinstance(data, dict):
            message_summary["data_dpids"] = sorted(str(dpid) for dpid in data)
        summary["msg"] = message_summary
    return summary


def summarize_dpids(state_data: dict[str, Any]) -> list[str]:
    """Return a state payload's DPID keys without its values."""
    return sorted(str(dpid) for dpid in state_data)
