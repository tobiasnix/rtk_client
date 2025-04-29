# rtk_state.py - Shared state management for the RTK client

import threading
import logging
from datetime import datetime, timezone
from collections import Counter, deque
from typing import Optional, Dict, Any
from rtk_constants import * # Import constants

logger = logging.getLogger(__name__)

class GnssState:
    """Thread-safe container for GNSS and NTRIP state."""
    def __init__(self, default_lat: float, default_lon: float, default_alt: float):
        self._lock = threading.Lock()
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
        self.ntrip_connection_gave_up: bool = False # <-- New state variable
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
                    logger.warning(f"Attempted to update non-existent state variable: {key}")

    def get_state_snapshot(self) -> Dict[str, Any]:
        """Return a copy of the current state in a thread-safe manner."""
        with self._lock:
            # Shallow copy is usually sufficient for display purposes
            # If deeper mutation is a risk, use copy.deepcopy
            return self.__dict__.copy() # Copy the instance dictionary

    def add_ui_log_message(self, message: str):
        """Adds a message to the UI log buffer."""
        with self._lock:
            timestamp = datetime.now().strftime("%H:%M:%S")
            self.ui_log_messages.append(f"[{timestamp}] {message}")
            # Also log important UI messages to main log file
            logger.info(f"UI_LOG: {message}")


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
            # Don't add to UI log here, let the calling code decide based on context
            logger.warning(message) # Log errors as warnings

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
                 self.add_ui_log_message(log_msg)
                 if status and message: # Update status message only when giving up
                      self.ntrip_status_message = message

    def set_ntrip_connected(self, status: bool, message: str = "", log_to_ui: bool = True) -> None: # Hinzugefügter Parameter log_to_ui
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
                     self.set_ntrip_gave_up(False) # Implicitly logs resume
                     # Immer zur UI loggen bei Erfolg
                     self.add_ui_log_message("NTRIP Connected.")
             elif changed and log_to_ui: # Nur zur UI loggen, wenn changed UND log_to_ui True ist
                  # Just disconnected
                  self.add_ui_log_message(f"NTRIP Disconnected: {message}")
                  # Do not reset gave_up flag here

