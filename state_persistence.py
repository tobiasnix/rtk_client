"""Saves and restores GNSS state across restarts."""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

STATE_FILE = ".rtk_state.json"


def save_state(state_snapshot: dict[str, Any], filename: str = STATE_FILE) -> bool:
    """Save relevant state fields to JSON on shutdown."""
    try:
        data = {
            "saved_at": datetime.now(timezone.utc).isoformat(),
            "position": state_snapshot.get("position"),
            "fix_type": state_snapshot.get("fix_type", 0),
            "rtk_status": state_snapshot.get("rtk_status", "Unknown"),
            "num_satellites_used": state_snapshot.get("num_satellites_used", 0),
            "hdop": state_snapshot.get("hdop", 99.99),
            "firmware_version": state_snapshot.get("firmware_version", "Unknown"),
            "module_name": state_snapshot.get("module_name", ""),
            "ntrip_total_bytes": state_snapshot.get("ntrip_total_bytes", 0),
        }
        with open(filename, "w") as f:
            json.dump(data, f, indent=2)
        logger.info(f"State saved to {filename}")
        return True
    except Exception as e:
        logger.warning(f"Failed to save state: {e}")
        return False


def load_state(filename: str = STATE_FILE) -> Optional[dict[str, Any]]:
    """Load previously saved state from JSON. Returns None if not available."""
    path = Path(filename)
    if not path.exists():
        logger.debug(f"No saved state file found: {filename}")
        return None
    try:
        with open(path) as f:
            data = json.load(f)
        logger.info(
            f"Loaded saved state from {filename} (saved: {data.get('saved_at', 'unknown')})"
        )
        return data
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"Failed to load saved state: {e}")
        return None
