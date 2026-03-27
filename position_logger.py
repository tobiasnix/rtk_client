import csv
import logging
import threading
from datetime import datetime, timezone
from typing import Optional

from rtk_state import GnssState

logger = logging.getLogger(__name__)


class PositionLogger:
    """Logs GNSS positions to a CSV file at regular intervals."""

    def __init__(self, state: GnssState, filename: str, interval: float = 5.0):
        self._state = state
        self._filename = filename
        self._interval = interval
        self._running = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._write_header()

    def _write_header(self) -> None:
        with open(self._filename, 'w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow([
                'timestamp', 'lat', 'lon', 'alt',
                'fix_type', 'rtk_status', 'num_sats', 'hdop'
            ])
        logger.info(f"Position log created: {self._filename}")

    def _log_loop(self) -> None:
        while self._running.is_set():
            snapshot = self._state.get_state_snapshot()
            if snapshot.get('have_position_lock'):
                pos = snapshot.get('position', {})
                with open(self._filename, 'a', newline='') as f:
                    writer = csv.writer(f)
                    writer.writerow([
                        datetime.now(timezone.utc).isoformat(),
                        pos.get('lat', 0.0),
                        pos.get('lon', 0.0),
                        pos.get('alt', 0.0),
                        snapshot.get('fix_type', 0),
                        snapshot.get('rtk_status', 'Unknown'),
                        snapshot.get('num_satellites_used', 0),
                        snapshot.get('hdop', 99.99),
                    ])
            self._running.wait(timeout=self._interval)

    def start(self) -> None:
        self._running.set()
        self._thread = threading.Thread(target=self._log_loop, name="PositionLogThread", daemon=True)
        self._thread.start()
        logger.info(f"Position logging started (interval: {self._interval}s)")

    def stop(self) -> None:
        self._running.clear()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=self._interval + 1)
        logger.info("Position logging stopped.")
