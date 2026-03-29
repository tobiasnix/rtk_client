# demo_device.py - Replays an NMEA log file as a simulated GNSS device

import logging
import os
import time
from typing import Optional

from rtk_state import GnssState

logger = logging.getLogger(__name__)

# Default demo file path relative to this module
_DEFAULT_DEMO_FILE = os.path.join(os.path.dirname(__file__), "data", "demo.nmea")


class DemoGnssDevice:
    """Replays an NMEA log file as if it were a real GNSS device.

    Implements the same interface as GnssDevice so the controller
    can use it as a drop-in replacement when --demo is active.
    """

    def __init__(self, nmea_file: str = _DEFAULT_DEMO_FILE, state: Optional[GnssState] = None):
        self._nmea_file = nmea_file
        self._state = state
        self._file = None
        self._connected = False
        self._line_delay = 0.1  # seconds between lines

    def connect(self) -> bool:
        """Opens the NMEA log file for reading."""
        if self._connected:
            return True
        try:
            self._file = open(self._nmea_file)  # noqa: SIM115
            self._connected = True
            logger.info(f"Demo device connected: {self._nmea_file}")
            if self._state:
                self._state.update(firmware_version="Demo v1.0")
            return True
        except FileNotFoundError:
            logger.error(f"Demo NMEA file not found: {self._nmea_file}")
            return False

    def read_line(self) -> Optional[str]:
        """Returns the next NMEA line from the file, looping at EOF."""
        if not self._connected or not self._file:
            return None

        time.sleep(self._line_delay)

        line = self._file.readline()
        if not line:
            # End of file — loop back to start
            logger.info("Demo NMEA file reached end, looping.")
            if self._state:
                self._state.add_ui_log_message("Demo: restarting sequence")
            self._file.seek(0)
            line = self._file.readline()
            if not line:
                return ""  # Empty file

        return line.strip()

    def write_data(self, data: bytes) -> Optional[int]:
        """No-op for demo — pretends to accept RTCM data."""
        if not self._connected:
            return None
        return len(data)

    def close(self) -> None:
        """Closes the NMEA file."""
        if self._file:
            self._file.close()
            self._file = None
        self._connected = False
        logger.info("Demo device closed.")

    def is_connected(self) -> bool:
        return self._connected

    def configure_module(self) -> None:
        """No-op for demo device."""
        logger.info("Demo device: configure_module (no-op)")
        if self._state:
            self._state.add_ui_log_message("Demo: module configured")

    @staticmethod
    def _calculate_checksum(sentence: str) -> str:
        """NMEA checksum calculation (for interface compatibility)."""
        checksum = 0
        if sentence.startswith('$'):
            sentence = sentence[1:]
        if '*' in sentence:
            sentence = sentence.split('*')[0]
        for char in sentence:
            checksum ^= ord(char)
        return f"{checksum:02X}"
