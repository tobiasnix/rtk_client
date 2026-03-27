# rtk_state.py - Shared state management for the RTK client

import copy
import logging
import threading
from collections import Counter, deque
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from rtk_constants import *  # Import constants

logger = logging.getLogger(__name__)

class GnssState:
    """Thread-safe container for GNSS and NTRIP state."""
    def __init__(self, default_lat: float, default_lon: float, default_alt: float):
        self._lock = threading.RLock()
        # Default position
        self.default_lat: float = default_lat
        self.default_lon: float = default_lon
        self.default_alt: float = default_alt
        # GNSS Data
        self.position: Dict[str, float] = {"lat": 0.0, "lon": 0.0, "alt": 0.0}
        self.status: str = "Initializing"
        self.rtk_status: str = "Unknown"
        self.fix_type: int = FIX_QUALITY_INVALID
        self.hdop: float = DEFAULT_HDOP
        self.num_satellites_used: int = 0
        self.num_satellites_in_view: int = 0
        self.last_fix_time: Optional[datetime] = None
        self.start_time: datetime = datetime.now(timezone.utc)
        self.first_fix_time_sec: Optional[float] = None
        self.last_rtk_fix_time: Optional[datetime] = None
        self.epochs_since_start: int = 0
        self.epochs_since_fix: int = 0
        self.max_satellites_seen: int = 0
        self.fix_type_counter: Counter = Counter()
        self.have_position_lock: bool = False
        self.firmware_version: str = "Unknown"
        # Satellite Tracking
        self.satellites_info: Dict[str, Dict[str, Any]] = {} # Key: Talker-PRN
        self.snr_stats: Dict[str, float] = {"min": 0, "max": 0, "avg": 0, "good_count": 0, "bad_count": 0}
        self.satellite_systems: Counter = Counter()
        # NTRIP Status
        self.ntrip_connected: bool = False
        self.ntrip_total_bytes: int = 0
        self.ntrip_last_data_time: Optional[datetime] = None
        self.ntrip_reconnect_attempts: int = 0
        self.last_ntrip_connect_time_sec: Optional[float] = None
        self.rtcm_message_counter: int = 0
        self.last_rtcm_message_types: deque = deque(maxlen=50)
        self.ntrip_data_rates: deque = deque(maxlen=60)
        self.last_rtcm_data_received: Optional[bytes] = None
        self.ntrip_status_message: str = "Not connected"
        self.ntrip_connection_gave_up: bool = False
        self.ntrip_next_reconnect_time: Optional[datetime] = None # <<< Initialized
        # Diagnostics
        self.gps_error_count: int = 0
        self.ntrip_error_count: int = 0
        self.last_command_response_time_sec: Optional[float] = None
        # UI Message Buffer
        self.ui_log_messages: deque = deque(maxlen=MAX_LOG_MESSAGES)

    def update(self, **kwargs) -> None:
        """Update state variables in a thread-safe manner."""
        with self._lock:
            for key, value in kwargs.items():
                if hasattr(self, key):
                    setattr(self, key, value)
                else:
                    # This warning is now less likely for ntrip_next_reconnect_time
                    logger.warning(f"Attempted to update non-existent state variable: {key}")

    def get_state_snapshot(self) -> Dict[str, Any]:
        """Return a deep copy of the current state in a thread-safe manner."""
        with self._lock:
            snapshot = {}
            for key, value in self.__dict__.items():
                if key == '_lock':
                    continue
                try:
                    snapshot[key] = copy.deepcopy(value)
                except TypeError:
                    snapshot[key] = value
            return snapshot

    def add_ui_log_message(self, message: str):
        """Adds a message to the UI log buffer with extreme formatting to prevent display issues."""
        with self._lock:
            timestamp = datetime.now().strftime("%H:%M:%S")

            # Define a safe maximum message length that won't disturb the display
            # Adjusted based on typical panel width, leaving room for borders/padding
            MAX_MSG_LENGTH = 70 # Slightly increased but still conservative

            # Process message based on content for optimal display
            processed_message = message

            # Special handling for common message types (remains the same)
            if "ERROR - NTRIP connection timed out" in message:
                processed_message = "NTRIP: Connection timeout"
            elif "INFO - NTRIP socket closed" in message:
                processed_message = "NTRIP: Socket closed"
            elif "INFO - NTRIP connection failed" in message:
                if "Retrying" in message and "/5)" in message:
                    retry_num = message.split("(")[1].split("/")[0] if "(" in message else "?"
                    processed_message = f"NTRIP: Conn failed ({retry_num}/5)"
                else:
                    processed_message = "NTRIP: Connection failed"
            elif "INFO - Connecting to" in message:
                processed_message = "NTRIP: Connecting..."
            elif "NTRIP: Retry" in message:
                parts = message.split("Retry")
                if len(parts) > 1:
                    retry_info = parts[1].strip().split(" ")[0]
                    processed_message = f"NTRIP: Retry {retry_info}"
                else:
                    processed_message = "NTRIP: Retrying..."
            elif "Bad file descriptor" in message:
                 processed_message = "NTRIP: Socket Error (Shutdown)"
            elif "did not exit cleanly" in message:
                 processed_message = "NTRIP: Thread Shutdown Issue"

            # Combine timestamp and processed message
            full_ui_msg = f"[{timestamp}] {processed_message}"

            # Ensure the *final combined* message is truncated to safe length for the UI panel
            if len(full_ui_msg) > MAX_MSG_LENGTH:
                full_ui_msg = full_ui_msg[:MAX_MSG_LENGTH-3] + "..." # Truncate combined message

            # Add formatted and truncated message to the buffer
            self.ui_log_messages.append(full_ui_msg)

            # Still log the original, full message to the file logger for complete details
            # Avoid logging the potentially truncated 'UI_LOG' prefix version here
            # logger.info(f"UI_LOG: {message}") # Keep original logging behavior if desired elsewhere

    def increment_error_count(self, error_type: str) -> None:
        """Increment error counters safely and log to UI."""
        with self._lock:
            message = ""
            if error_type == "gps":
                self.gps_error_count += 1
                message = f"GPS Error #{self.gps_error_count}"
            elif error_type == "ntrip":
                self.ntrip_error_count += 1
                message = f"NTRIP Error #{self.ntrip_error_count}"
            else:
                 logger.warning(f"Unknown error type for increment: {error_type}")
                 return
            # Log the error count increase as a warning
            logger.warning(message)
            # Add a simplified message to the UI log
            self.add_ui_log_message(message)


    def increment_ntrip_reconnects(self) -> int:
        """Increments the reconnect counter and returns the new value."""
        with self._lock:
            self.ntrip_reconnect_attempts += 1
            return self.ntrip_reconnect_attempts

    def reset_ntrip_reconnects(self) -> None:
        """Resets the reconnect counter to 0."""
        with self._lock:
            if self.ntrip_reconnect_attempts > 0:
                 logger.debug("Resetting NTRIP reconnect attempts counter.")
                 self.ntrip_reconnect_attempts = 0

    def set_ntrip_gave_up(self, status: bool, message: str = "") -> None:
        """Sets the flag indicating NTRIP connection attempts have ceased."""
        with self._lock:
            if status != self.ntrip_connection_gave_up:
                 self.ntrip_connection_gave_up = status
                 log_msg = f"NTRIP connection attempts {'ceased' if status else 'resumed'}."
                 if message: log_msg += f" Reason: {message}"
                 logger.warning(log_msg)
                 # Use add_ui_log_message for consistent formatting/truncation
                 self.add_ui_log_message(log_msg)
                 if status and message: # Update status message only when giving up
                      self.ntrip_status_message = message

    def set_ntrip_connected(self, status: bool, message: str = "", log_to_ui: bool = True) -> None:
        """Updates NTRIP connection status and related state."""
        with self._lock:
             changed = (self.ntrip_connected != status)
             self.ntrip_connected = status
             if message: self.ntrip_status_message = message

             if status:
                 # Connected successfully
                 self.ntrip_last_data_time = datetime.now(timezone.utc) # Assume data might follow
                 if changed:
                     # Reset counters on successful connection transition
                     self.reset_ntrip_reconnects()
                     # Reset gave up flag if we reconnected
                     # Use internal method which handles logging/UI message
                     self.set_ntrip_gave_up(False)
                     # Always log to UI on successful connection change
                     self.add_ui_log_message("NTRIP Connected.")
             elif changed and log_to_ui:
                  # Just disconnected
                  # Use add_ui_log_message for consistent formatting/truncation
                  self.add_ui_log_message(f"NTRIP Disconnected: {message}")
