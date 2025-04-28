# lc29hda_rtk_refactored.py - Refactored version V1.4 spec update
# Uses curses for flicker-free status display.
# Sends GGA continuously, even without GPS fix.
# Adheres to SOLID, DRY, Clean Code principles.

import serial
import time
import socket
import base64
import threading
import pynmea2
from datetime import datetime, timezone
import logging
from collections import Counter, deque
import argparse
import sys
import curses # Import curses library
from typing import Optional, Dict, Any, List, Tuple

# --- Constants ---
DEFAULT_SERIAL_PORT = "/dev/ttyUSB0"
DEFAULT_BAUD_RATE = 115200
DEFAULT_NTRIP_SERVER = "193.137.94.71" # Example server
DEFAULT_NTRIP_PORT = 2101
DEFAULT_NTRIP_MOUNTPOINT = "PNM1" # Example mountpoint
DEFAULT_NTRIP_USERNAME = "user"
DEFAULT_NTRIP_PASSWORD = "password"
DEFAULT_LAT = 40.10939918 # Fallback Latitude
DEFAULT_LON = -7.15450152 # Fallback Longitude
DEFAULT_ALT = 476.68    # Fallback Altitude
DEFAULT_HDOP = 99.99    # Default/Invalid HDOP value according to Spec V1.4

NTRIP_TIMEOUT = 10.0  # seconds
NTRIP_GGA_INTERVAL = 10.0 # seconds
NTRIP_MAX_RECONNECT_TIMEOUT = 60.0 # seconds
NTRIP_INITIAL_RECONNECT_TIMEOUT = 5.0 # seconds
NTRIP_DATA_TIMEOUT = 60.0 # seconds

SERIAL_TIMEOUT = 1.0 # seconds
STATUS_UPDATE_INTERVAL = 1.0 # seconds

# NMEA Fix Quality Indicators (from GGA message)
FIX_QUALITY_INVALID = 0
FIX_QUALITY_GPS = 1
FIX_QUALITY_DGPS = 2
FIX_QUALITY_PPS = 3 # Not typically used by end-user receivers
FIX_QUALITY_RTK_FIXED = 4
FIX_QUALITY_RTK_FLOAT = 5
FIX_QUALITY_ESTIMATED = 6

# RTCM3 MSM Message Types (Examples - check your base station)
RTCM_MSG_TYPE_GPS_MSM7 = 1077
RTCM_MSG_TYPE_GLONASS_MSM7 = 1087
RTCM_MSG_TYPE_GALILEO_MSM7 = 1097
RTCM_MSG_TYPE_BDS_MSM7 = 1127
RTCM_MSG_TYPE_QZSS_MSM7 = 1117 # If using QZSS corrections
RTCM_MSG_TYPE_ARP_1005 = 1005 # Antenna Reference Point

# Check V1.4 Spec Table 8 for relevant input messages
IMPORTANT_RTCM_TYPES = {
    RTCM_MSG_TYPE_GPS_MSM7: "GPS MSM7 (1077)",
    RTCM_MSG_TYPE_GLONASS_MSM7: "GLONASS MSM7 (1087)",
    RTCM_MSG_TYPE_GALILEO_MSM7: "Galileo MSM7 (1097)",
    RTCM_MSG_TYPE_BDS_MSM7: "BDS MSM7 (1127)",
    # RTCM_MSG_TYPE_QZSS_MSM7: "QZSS MSM7 (1117)", # Optional
    RTCM_MSG_TYPE_ARP_1005: "ARP (1005/1006)", # Base station position
}
# Add MSM4/MSM5 types if needed and provided by caster:
# 1074, 1075 (GPS), 1084, 1085 (GLO), 1094, 1095 (GAL), 1124, 1125 (BDS)

LOG_FORMAT = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'

# --- Logging Setup ---
# Note: Logging to console might interfere with curses display.
# Best practice is often to log exclusively to file when using curses.
# We'll keep basic file logging.
logging.basicConfig(level=logging.INFO, format=LOG_FORMAT, filename='lc29hda_rtk_refactored.log', filemode='w')
logger = logging.getLogger("RtkController") # Main logger

# --- Configuration Class ---
class Config:
    """Holds all configuration parameters."""
    def __init__(self, args: argparse.Namespace):
        self.serial_port: str = args.port
        self.baud_rate: int = args.baud
        self.ntrip_server: str = args.ntrip_server or DEFAULT_NTRIP_SERVER
        self.ntrip_port: int = args.ntrip_port or DEFAULT_NTRIP_PORT
        self.ntrip_mountpoint: str = args.ntrip_mountpoint or DEFAULT_NTRIP_MOUNTPOINT
        self.ntrip_username: str = args.ntrip_user or DEFAULT_NTRIP_USERNAME
        self.ntrip_password: str = args.ntrip_pass or DEFAULT_NTRIP_PASSWORD
        self.default_lat: float = args.default_lat or DEFAULT_LAT
        self.default_lon: float = args.default_lon or DEFAULT_LON
        self.default_alt: float = args.default_alt or DEFAULT_ALT
        self.debug: bool = args.debug

        # Adjust log level based on debug flag (only affects file log now)
        log_level = logging.DEBUG if self.debug else logging.INFO
        logging.getLogger().setLevel(log_level)

        logger.info("Configuration loaded.")
        logger.debug(f"Config details: {self.__dict__}")

# --- State Class ---
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
        self.hdop: float = DEFAULT_HDOP # Updated default
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
        self.satellites_info: Dict[str, Dict[str, Any]] = {}
        self.snr_stats: Dict[str, float] = {"min": 0, "max": 0, "avg": 0, "good_count": 0, "bad_count": 0}
        self.satellite_systems: Counter = Counter()
        # NTRIP Status
        self.ntrip_connected: bool = False
        self.ntrip_total_bytes: int = 0
        self.ntrip_last_data_time: Optional[datetime] = None
        self.ntrip_reconnect_attempts: int = 0
        self.last_ntrip_connect_time_sec: Optional[float] = None
        self.rtcm_message_counter: int = 0
        self.last_rtcm_message_types: deque = deque(maxlen=50) # Store recent RTCM types
        self.ntrip_data_rates: deque = deque(maxlen=60) # Bytes per second in the last minute
        self.last_rtcm_data_received: Optional[bytes] = None
        self.ntrip_status_message: str = "Not connected"
        # Diagnostics
        self.gps_error_count: int = 0
        self.ntrip_error_count: int = 0
        self.last_command_response_time_sec: Optional[float] = None

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
            # Return a deep copy to prevent modification of internal state
            state_copy = {}
            for key, value in self.__dict__.items():
                if key == "_lock":
                    continue
                if isinstance(value, (dict, Counter, deque)):
                    state_copy[key] = value.copy()
                elif isinstance(value, list):
                     state_copy[key] = value[:]
                else:
                    state_copy[key] = value
            return state_copy

    def increment_error_count(self, error_type: str) -> None:
        """Increment error counters safely."""
        with self._lock:
            if error_type == "gps":
                self.gps_error_count += 1
            elif error_type == "ntrip":
                self.ntrip_error_count += 1
            else:
                 logger.warning(f"Unknown error type for increment: {error_type}")

    def add_rtcm_type(self, msg_type: int) -> None:
        """Add a received RTCM message type."""
        with self._lock:
            self.last_rtcm_message_types.append(msg_type)

    def add_ntrip_data_rate(self, bytes_received: int) -> None:
        """Add NTRIP data rate measurement."""
        with self._lock:
            self.ntrip_data_rates.append(bytes_received)

    def reset_ntrip_reconnects(self) -> None:
        """Reset NTRIP reconnect counter."""
        with self._lock:
            self.ntrip_reconnect_attempts = 0

    def increment_ntrip_reconnects(self) -> None:
        """Increment NTRIP reconnect counter."""
        with self._lock:
             self.ntrip_reconnect_attempts += 1

    def set_ntrip_connected(self, status: bool, message: str = "") -> None:
         """Set NTRIP connection status."""
         with self._lock:
             self.ntrip_connected = status
             if message:
                 self.ntrip_status_message = message
             if status:
                 # Reset last data time only when connecting successfully
                 self.ntrip_last_data_time = datetime.now(timezone.utc)
                 self.reset_ntrip_reconnects()
             # else: No need to reset last_data_time on disconnect


# --- GNSS Device Communication ---
class GnssDevice:
    """Handles serial communication with the GNSS module."""
    def __init__(self, port: str, baudrate: int, state: GnssState):
        self._port_name = port
        self._baudrate = baudrate
        self._serial_port: Optional[serial.Serial] = None
        self._state = state
        self._logger = logging.getLogger(self.__class__.__name__)
        # Don't connect automatically here, let controller handle it
        # self.connect()

    def connect(self) -> bool:
        """Establishes the serial connection."""
        if self.is_connected():
            self._logger.debug("Serial port already connected.")
            return True
        try:
            self._serial_port = serial.Serial(self._port_name, self._baudrate, timeout=SERIAL_TIMEOUT)
            self._logger.info(f"Connected to GNSS device on {self._port_name} at {self._baudrate} baud")
            return True
        except serial.SerialException as e:
            self._logger.error(f"Error opening serial port {self._port_name}: {e}")
            self._serial_port = None
            return False
        except Exception as e: # Catch other potential errors like permission denied
            self._logger.error(f"Unexpected error connecting to serial port {self._port_name}: {e}")
            self._serial_port = None
            return False


    def is_connected(self) -> bool:
        """Checks if the serial port is open."""
        return self._serial_port is not None and self._serial_port.is_open

    @staticmethod
    def _calculate_checksum(sentence: str) -> str:
        """Calculates the NMEA checksum for a sentence."""
        checksum = 0
        # Remove leading '$' and trailing '*XX' if present
        if sentence.startswith('$'):
            sentence = sentence[1:]
        if '*' in sentence:
            sentence = sentence.split('*')[0]

        for char in sentence:
            checksum ^= ord(char)
        return f"{checksum:02X}"

    def send_command(self, command: str) -> Optional[str]:
        """Sends a command (e.g., PAIR) to the GNSS module."""
        if not self.is_connected():
            self._logger.error("Cannot send command: Serial port not connected.")
            return None

        if not command.startswith('$'):
            command = '$' + command
        if '*' in command: # Ensure no checksum is included initially
             command = command.split('*')[0]

        checksum = self._calculate_checksum(command)
        full_command = f"{command}*{checksum}\r\n"

        try:
            start_time = time.monotonic()
            # Ensure port is cleared before sending, prevent reading stale data
            self._serial_port.reset_input_buffer()
            bytes_written = self._serial_port.write(full_command.encode('ascii'))
            self._serial_port.flush() # Ensure data is sent
            self._logger.debug(f"Sent command ({bytes_written} bytes): {full_command.strip()}")

            # Read response (optional, adjust timeout as needed)
            response_bytes = self._serial_port.readline()
            end_time = time.monotonic()
            # Use thread-safe update for state modification
            self._state.update(last_command_response_time_sec=(end_time - start_time))

            response = response_bytes.decode('ascii', errors='ignore').strip()
            self._logger.debug(f"Received response: {response}")
            return response
        except serial.SerialTimeoutException:
            self._logger.warning(f"Timeout waiting for response to command: {command}")
            return None # Indicate timeout, not necessarily an error
        except serial.SerialException as e:
            self._logger.error(f"Serial error sending command '{command}': {e}")
            self._state.increment_error_count("gps")
            # Close port on error, let reconnection happen in main loop
            self.close()
            return None
        except Exception as e:
            self._logger.error(f"Unexpected error sending command '{command}': {e}", exc_info=True)
            self._state.increment_error_count("gps")
            return None

    def read_line(self) -> Optional[str]:
        """Reads a line from the serial port."""
        if not self.is_connected():
            return None # Let the caller handle reconnection attempts

        try:
            if self._serial_port.in_waiting > 0:
                line_bytes = self._serial_port.readline()
                # Check for empty byte string which can happen on timeout/disconnect
                if not line_bytes:
                    return "" # Distinguish from error/closed port
                return line_bytes.decode('ascii', errors='ignore').strip()
            else:
                # No data waiting, return empty string (non-blocking behavior)
                return ""
        except serial.SerialException as e:
            self._logger.error(f"Serial error reading line: {e}")
            self._state.increment_error_count("gps")
            self.close() # Close the port on error
            return None # Indicate error/closed port
        except Exception as e:
            self._logger.error(f"Unexpected error reading line: {e}", exc_info=True)
            self._state.increment_error_count("gps")
            return None # Indicate error

    def write_data(self, data: bytes) -> Optional[int]:
        """Writes raw bytes (e.g., RTCM) to the serial port."""
        if not self.is_connected():
            self._logger.error("Cannot write data: Serial port not connected.")
            return None
        try:
            return self._serial_port.write(data)
        except serial.SerialTimeoutException:
             self._logger.warning("Serial write timeout occurred.")
             # May indicate flow control issues or buffer problems
             self._state.increment_error_count("gps")
             return 0 # Indicate potentially 0 bytes written
        except serial.SerialException as e:
            self._logger.error(f"Serial error writing data: {e}")
            self._state.increment_error_count("gps")
            self.close() # Close port on error
            return None # Indicate error/port closed
        except Exception as e:
             self._logger.error(f"Unexpected error writing data: {e}", exc_info=True)
             self._state.increment_error_count("gps")
             return None # Indicate error


    def configure_module(self) -> None:
        """Configures the LC29H(DA) module based on Spec V1.4."""
        self._logger.info("Configuring LC29H (DA) module...")
        time.sleep(1) # Allow module to boot

        # Query Firmware Version first using PQTM command
        version_response = self.send_command("PQTMVERNO")
        if version_response:
            try:
                parts = version_response.split(',')
                if len(parts) > 1 and parts[0] == "$PQTMVERNO":
                    fw = parts[1]
                    self._state.update(firmware_version=fw)
                    self._logger.info(f"Detected Firmware: {fw}")
                elif "ERROR" in version_response:
                     self._logger.warning(f"Failed to get firmware version: {version_response}")
                     self._state.update(firmware_version="Query Error")
                else:
                    self._logger.warning(f"Unexpected firmware response format: {version_response}")
                    self._state.update(firmware_version="Parse Error")
            except Exception as e:
                 self._logger.warning(f"Could not parse firmware version from '{version_response}': {e}")
                 self._state.update(firmware_version="Parse Exception")
        else:
             self._logger.warning("No response received for firmware query.")
             self._state.update(firmware_version="No Response")

        # Enable specific NMEA sentences at 1Hz using PAIR062
        # Spec V1.4 confirms types 0-5 supported for DA
        # Short delay between commands might be needed
        commands = [
            "PAIR062,0,1", # GGA
            "PAIR062,4,1", # RMC
            "PAIR062,2,1", # GSA
            "PAIR062,3,1", # GSV
            "PAIR062,5,1", # VTG
            "PAIR436,1", # Enable RTCM Ephemeris Output
            "PAIR513",   # Save Settings
        ]
        for cmd in commands:
             self.send_command(cmd)
             time.sleep(0.15) # Small delay


        self._logger.info("Module configuration commands sent.")

    def close(self) -> None:
        """Closes the serial connection."""
        if self._serial_port and self._serial_port.is_open:
            try:
                self._serial_port.close()
                self._logger.info("Serial port closed.")
            except Exception as e:
                self._logger.error(f"Error closing serial port: {e}")
        self._serial_port = None

# --- NMEA Parsing ---
class NmeaParser:
    """Parses NMEA sentences and updates the shared state."""
    def __init__(self, state: GnssState):
        self._state = state
        self._logger = logging.getLogger(self.__class__.__name__)
         # Temporary storage for GSV sequence building
        self._current_gsv_sequence_sats = {}
        self._current_gsv_systems = Counter()


    def parse(self, sentence: str) -> None:
        """Parses a single NMEA sentence."""
        if not sentence or not sentence.startswith('$'):
            return

        try:
            msg = pynmea2.parse(sentence)
            # Update epoch count using thread-safe method (though only called by one thread)
            current_epochs = self._state.get_state_snapshot()['epochs_since_start']
            self._state.update(epochs_since_start=current_epochs + 1)

            if isinstance(msg, pynmea2.types.talker.GGA):
                self._parse_gga(msg)
            elif isinstance(msg, pynmea2.types.talker.GSV):
                self._parse_gsv(msg)
            elif isinstance(msg, pynmea2.types.talker.GSA):
                 self._parse_gsa(msg)
            # Add other message types (RMC, VTG, etc.) if needed for state
            # elif isinstance(msg, pynmea2.types.talker.RMC):
            #     self._parse_rmc(msg)

        except pynmea2.ParseError as e:
            # Reduce noise for common parse errors unless debugging
            self._logger.debug(f"Failed to parse NMEA sentence: {sentence} - Error: {e}")
        except Exception as e:
            self._logger.error(f"Error processing NMEA sentence: {sentence} - Error: {e}", exc_info=True)
            self._state.increment_error_count("gps")

    def _get_fix_status_string(self, fix_type: int) -> str:
        """Maps fix type integer to a status string."""
        status_map = {
            FIX_QUALITY_RTK_FIXED: "RTK Fixed",
            FIX_QUALITY_RTK_FLOAT: "RTK Float",
            FIX_QUALITY_DGPS: "DGPS", # Includes SBAS/DGPS
            FIX_QUALITY_GPS: "GPS (SPS)", # Basic GPS fix
            FIX_QUALITY_ESTIMATED: "Estimated (DR)", # Dead Reckoning
            FIX_QUALITY_INVALID: "No Fix / Invalid" # Fix not available
            # FIX_QUALITY_PPS is not listed as output in GGA spec
        }
        return status_map.get(fix_type, "Unknown Fix Type")


    def _parse_gga(self, msg: pynmea2.types.talker.GGA) -> None:
        """Parses GGA message content."""
        current_state = self._state.get_state_snapshot() # Get a consistent snapshot
        old_fix_type = current_state['fix_type']
        # Ensure gps_qual is treated as integer, default to invalid if empty/None
        new_fix_type = int(msg.gps_qual) if msg.gps_qual is not None and msg.gps_qual != '' else FIX_QUALITY_INVALID
        now = datetime.now(timezone.utc)
        updates = {'fix_type': new_fix_type}

        new_lat, new_lon, new_alt = 0.0, 0.0, 0.0
        has_valid_coords = False

        # Use pynmea2's built-in lat/lon conversion
        if msg.latitude is not None and msg.longitude is not None and new_fix_type > FIX_QUALITY_INVALID:
            new_lat = msg.latitude
            new_lon = msg.longitude
            has_valid_coords = True
            updates['have_position_lock'] = True
            updates['last_fix_time'] = now
        else:
             # If fix is lost or invalid, maintain last known position or reset?
             # Current approach: Don't update position, let have_position_lock reflect status
             updates['have_position_lock'] = False

        # Only update position if coordinates are valid this epoch
        if has_valid_coords:
             current_pos = current_state.get('position', {})
             # Use new altitude if available, otherwise keep previous state's altitude
             new_alt_val = float(msg.altitude) if msg.altitude is not None else current_pos.get('alt', self._state.default_alt)
             updates['position'] = {"lat": new_lat, "lon": new_lon, "alt": new_alt_val}


        updates['num_satellites_used'] = int(msg.num_sats) if msg.num_sats is not None and msg.num_sats != '' else 0
        # Use DEFAULT_HDOP (99.99) if value is missing or invalid
        updates['hdop'] = float(msg.horizontal_dil) if msg.horizontal_dil is not None and msg.horizontal_dil != '' else DEFAULT_HDOP

        # Calculate Time to First Fix (TTFF)
        if not current_state.get('first_fix_time_sec') and new_fix_type > FIX_QUALITY_INVALID:
             updates['first_fix_time_sec'] = (now - current_state['start_time']).total_seconds()

        new_rtk_status = self._get_fix_status_string(new_fix_type)
        updates['rtk_status'] = new_rtk_status

        # Log transitions and update RTK fix time/epochs
        old_rtk_status = current_state['rtk_status']
        if new_rtk_status == "RTK Fixed" and old_rtk_status != "RTK Fixed":
            self._logger.info("Achieved RTK Fixed solution.")
            updates['last_rtk_fix_time'] = now
            updates['epochs_since_fix'] = 0 # Reset counter on achieving fix
        elif old_rtk_status == "RTK Fixed" and new_rtk_status != "RTK Fixed":
             self._logger.warning(f"Lost RTK Fixed solution. New status: {new_rtk_status}")

        # Increment epochs since last RTK fix if we previously had one
        if current_state.get('last_rtk_fix_time'):
            updates['epochs_since_fix'] = current_state.get('epochs_since_fix', 0) + 1

        # Update fix history counter (needs state lock for Counter update)
        with self._state._lock:
             self._state.fix_type_counter[new_rtk_status] += 1
        # No need to add 'fix_type_counter' to 'updates' dict

        if old_fix_type != new_fix_type:
            self._logger.info(f"Fix type changed from {old_fix_type} ({old_rtk_status}) to {new_fix_type} ({new_rtk_status})")

        # Apply all updates to the shared state
        self._state.update(**updates)


    def _parse_gsv(self, msg: pynmea2.types.talker.GSV) -> None:
        """Parses GSV message content."""
        current_state = self._state.get_state_snapshot()
        # Ensure num_sv_in_view is treated as integer, default 0
        num_sv_in_view = int(msg.num_sv_in_view) if msg.num_sv_in_view is not None and msg.num_sv_in_view != '' else 0
        sentence_num = int(msg.sentence_num) if msg.sentence_num is not None and msg.sentence_num != '' else 0
        num_sentences = int(msg.num_sentences) if msg.num_sentences is not None and msg.num_sentences != '' else 0

        if sentence_num < 1 or num_sentences < 1:
             self._logger.debug(f"Ignoring malformed GSV sentence: {msg}")
             return

        # GSV messages come in sequences. Reset satellite info at the start of a new sequence.
        is_first_sentence = (sentence_num == 1)
        if is_first_sentence:
            # Keep a temporary dict for this sequence to avoid partial updates on error
            self._current_gsv_sequence_sats = {}
            self._current_gsv_systems = Counter()

        # Determine constellation from talker ID (GP, GL, GA, GB, GQ, GI)
        sat_system = "Unknown"
        talker = msg.talker
        if talker == 'GP': sat_system = "GPS"
        elif talker == 'GL': sat_system = "GLONASS"
        elif talker == 'GA': sat_system = "Galileo"
        elif talker == 'GB': sat_system = "BeiDou"
        elif talker == 'GQ': sat_system = "QZSS"
        elif talker == 'GI': sat_system = "NavIC" # Added in V1.4


        # Process satellites in this specific GSV message
        for i in range(1, 5):
            prn_field = f'sv_prn_num_{i}'
            elev_field = f'elevation_{i}'
            azim_field = f'azimuth_{i}'
            snr_field = f'snr_{i}'

            # Check if fields exist and PRN is not None/empty
            if hasattr(msg, prn_field) and getattr(msg, prn_field):
                prn = getattr(msg, prn_field)
                snr_val = getattr(msg, snr_field)
                # Ensure SNR is integer, default 0 if None/empty/invalid
                try: snr = int(snr_val) if snr_val else 0
                except (ValueError, TypeError): snr = 0
                # Ensure elevation/azimuth are integers, None if missing/invalid
                try: elev = int(getattr(msg, elev_field)) if getattr(msg, elev_field) else None
                except (ValueError, TypeError): elev = None
                try: azim = int(getattr(msg, azim_field)) if getattr(msg, azim_field) else None
                except (ValueError, TypeError): azim = None

                # Use a unique key: TalkerID-PRN (e.g., "GP-15", "GL-70")
                sat_key = f"{talker}-{prn}"
                self._current_gsv_sequence_sats[sat_key] = {
                    'prn': prn,
                    'snr': snr,
                    'elevation': elev,
                    'azimuth': azim,
                    'system': sat_system,
                    'active': False # Default to inactive, GSA message will update this
                }
                # Count satellite systems based on satellites with reported SNR > 0
                if snr > 0:
                     self._current_gsv_systems[sat_system] += 1


        # Update state only after processing the last sentence in the sequence
        is_last_sentence = (sentence_num == num_sentences)
        if is_last_sentence:
            # Calculate SNR stats based on the completed sequence data
            snr_stats = self._calculate_snr_stats(self._current_gsv_sequence_sats)

            # Prepare updates for the shared state
            updates = {
                 'num_satellites_in_view': num_sv_in_view,
                 'max_satellites_seen': max(current_state.get('max_satellites_seen', 0), num_sv_in_view),
                 'satellites_info': self._current_gsv_sequence_sats,
                 'satellite_systems': self._current_gsv_systems,
                 'snr_stats': snr_stats
            }
            self._state.update(**updates)
            # Clear temporary sequence data (important!)
            self._current_gsv_sequence_sats = {}
            self._current_gsv_systems = Counter()


    def _calculate_snr_stats(self, satellites_info: Dict[str, Dict[str, Any]]) -> Dict[str, float]:
        """Calculates SNR statistics based on provided satellite info."""
        # Extract SNRs > 0
        snrs = [sat['snr'] for sat in satellites_info.values() if sat.get('snr', 0) > 0]
        stats = {"min": 0.0, "max": 0.0, "avg": 0.0, "good_count": 0.0, "bad_count": 0.0}
        if not snrs:
            return stats # Return defaults if no valid SNRs

        stats["min"] = float(min(snrs))
        stats["max"] = float(max(snrs))
        stats["avg"] = sum(snrs) / len(snrs)
        stats["good_count"] = float(sum(1 for snr in snrs if snr >= 30)) # SNR >= 30 is good
        stats["bad_count"] = float(sum(1 for snr in snrs if 1 <= snr < 20)) # 0 < SNR < 20 is bad
        return stats

    def _parse_gsa(self, msg: pynmea2.types.talker.GSA) -> None:
        """Parses GSA message to mark active satellites."""
        active_sat_keys = set()
        talker = msg.talker # GP, GL, GA, GB, GN, GI etc.

        # Extract active satellite PRNs from the message
        for i in range(1, 13):
             sat_id_field = f'sv_id{i:02}' # Fields are sv_id01, sv_id02,...
             if hasattr(msg, sat_id_field):
                 prn = getattr(msg, sat_id_field)
                 if prn: # If the field has a value
                     # Construct the unique key used in GSV parsing
                     sat_key = f"{talker}-{prn}"
                     # Handle GN talker (indicates combined solution)
                     if talker == 'GN':
                          # Search for this PRN across all known satellites in the state
                          found = False
                          # Need to lock state briefly to read satellite_info safely
                          with self._state._lock:
                              current_sats = self._state.satellites_info
                              for key, sat_info in current_sats.items():
                                  # Match PRN number directly
                                  if sat_info.get('prn') == prn:
                                      active_sat_keys.add(key)
                                      found = True
                                      # Assume unique PRN across constellations for simplicity here.
                                      # Real-world overlaps might need more complex handling.
                                      break
                          if not found:
                               self._logger.debug(f"GNGSA referenced PRN {prn} which was not found in recent GSV data.")
                     else:
                          # For specific talkers (GP, GL, etc.), directly add the key
                          active_sat_keys.add(sat_key)

        # Update the 'active' status in the shared state's satellite info
        # Lock needed because we are modifying the dictionary within GnssState
        with self._state._lock:
            # Iterate over keys present in the state's satellite info
            for key in list(self._state.satellites_info.keys()):
                 # Check if the key exists before trying to access/modify it
                 if key in self._state.satellites_info:
                    if key in active_sat_keys:
                        # Mark as active if found in this GSA message
                        self._state.satellites_info[key]['active'] = True
                    else:
                        # Deactivate only if the GSA talker matches the satellite's talker
                        if talker != 'GN' and key.startswith(talker + '-'):
                            self._state.satellites_info[key]['active'] = False
                        # Do not deactivate based on GN message alone


# --- NTRIP Client ---
class NtripClient:
    """Handles connection and data exchange with the NTRIP caster."""
    def __init__(self, config: Config, state: GnssState, gnss_device: GnssDevice):
        self._config = config
        self._state = state
        self._gnss_device = gnss_device
        self._logger = logging.getLogger(self.__class__.__name__)
        self._socket: Optional[socket.socket] = None
        self._running = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._last_gga_sent_time = datetime.min.replace(tzinfo=timezone.utc)
        self._reconnect_timeout = NTRIP_INITIAL_RECONNECT_TIMEOUT

    def start(self):
        """Starts the NTRIP client thread."""
        if self._thread is not None and self._thread.is_alive():
            self._logger.warning("NTRIP client thread already running.")
            return
        self._running.set()
        self._thread = threading.Thread(target=self._run, name="NtripThread", daemon=True)
        self._thread.start()
        self._logger.info("NTRIP client thread started.")

    def stop(self):
        """Stops the NTRIP client thread."""
        self._running.clear()
        # Close socket immediately to interrupt blocking calls
        if self._socket:
            try:
                self._socket.shutdown(socket.SHUT_RDWR)
            except OSError: pass
            try: self._socket.close()
            except OSError as e: self._logger.warning(f"Error closing NTRIP socket: {e}")
            finally: self._socket = None

        # Wait for thread to finish
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
            if self._thread.is_alive():
                 self._logger.warning("NTRIP thread did not exit cleanly.")
        self._logger.info("NTRIP client stopped.")

    def _connect(self) -> bool:
        """Establishes connection to the NTRIP caster."""
        if self._socket: # Close existing socket first
             try: self._socket.close();
             except OSError: pass
             self._socket = None

        self._state.set_ntrip_connected(False, "Connecting...")
        self._logger.info(f"Connecting to NTRIP: {self._config.ntrip_server}:{self._config.ntrip_port}/{self._config.ntrip_mountpoint}")

        try:
            start_connect = time.monotonic()
            # Create new socket for each attempt
            self._socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._socket.settimeout(NTRIP_TIMEOUT)
            self._socket.connect((self._config.ntrip_server, self._config.ntrip_port))

            # --- Prepare Ntrip V1 Authentication Request ---
            auth_string = f"{self._config.ntrip_username}:{self._config.ntrip_password}"
            auth_b64 = base64.b64encode(auth_string.encode('ascii')).decode('ascii')

            request_lines = [
                f"GET /{self._config.ntrip_mountpoint} HTTP/1.1", # Use HTTP/1.1 for Host header
                f"Host: {self._config.ntrip_server}:{self._config.ntrip_port}",
                "Ntrip-Version: Ntrip/1.0",
                "User-Agent: Python NtripClient/1.1",
                f"Authorization: Basic {auth_b64}",
                "Accept: */*",
                "Connection: close", # Close connection after response
                "\r\n" # Extra CRLF to end headers
            ]
            request = "\r\n".join(request_lines)
            # -----------------------------------------------

            self._logger.debug(f"Sending NTRIP request:\n{request.strip()}")
            self._socket.sendall(request.encode('ascii'))

            # --- Check Response ---
            response_bytes = bytearray()
            # Read headers first (end with \r\n\r\n)
            self._socket.settimeout(NTRIP_TIMEOUT) # Reset timeout for reading response
            while b"\r\n\r\n" not in response_bytes:
                 chunk = self._socket.recv(1024)
                 if not chunk:
                     raise ConnectionAbortedError("NTRIP server closed connection during header read")
                 response_bytes.extend(chunk)
                 if len(response_bytes) > 8192: # Prevent excessive buffering
                      raise OverflowError("NTRIP header too large")


            headers_part, _, body_part = response_bytes.partition(b"\r\n\r\n")
            response_str = headers_part.decode('ascii', errors='ignore')
            end_connect = time.monotonic()
            self._state.update(last_ntrip_connect_time_sec=(end_connect - start_connect))
            self._logger.debug(f"Received NTRIP response headers:\n{response_str}")

            # Check for "ICY 200 OK" or "HTTP/1.1 200 OK"
            if b"ICY 200 OK" in headers_part or b"HTTP/1.1 200 OK" in headers_part:
                self._logger.info("NTRIP connection successful.")
                self._state.set_ntrip_connected(True, "Connected")
                self._reconnect_timeout = NTRIP_INITIAL_RECONNECT_TIMEOUT # Reset backoff

                # Send initial GGA immediately if required by caster (some require after connect)
                self._send_gga()
                self._last_gga_sent_time = datetime.now(timezone.utc)

                # Process any RTCM data received with the initial response body
                if body_part:
                    self._handle_rtcm_data(body_part)
                return True
            else:
                status_line = response_str.splitlines()[0] if '\n' in response_str else response_str
                self._logger.error(f"NTRIP connection failed. Status: '{status_line}'")
                self._state.set_ntrip_connected(False, f"Failed: {status_line[:30]}")
                self._state.increment_ntrip_reconnects()
                if self._socket: self._socket.close()
                self._socket = None
                return False
            # ---------------------

        except socket.timeout:
            self._logger.error("NTRIP connection timed out.")
            self._state.set_ntrip_connected(False, "Timeout")
            self._state.increment_ntrip_reconnects()
            if self._socket: self._socket.close()
            self._socket = None
            return False
        except (socket.gaierror, ConnectionRefusedError, ConnectionAbortedError, OverflowError, OSError) as e:
             self._logger.error(f"NTRIP socket connection error: {e}")
             self._state.set_ntrip_connected(False, f"Socket Error: {str(e)[:20]}")
             self._state.increment_ntrip_error_count("ntrip")
             self._state.increment_ntrip_reconnects()
             if self._socket: self._socket.close()
             self._socket = None
             return False
        except Exception as e:
            self._logger.error(f"Unexpected NTRIP connection error: {e}", exc_info=True)
            self._state.set_ntrip_connected(False, f"Error: {str(e)[:20]}")
            self._state.increment_ntrip_error_count("ntrip")
            self._state.increment_ntrip_reconnects()
            if self._socket: self._socket.close()
            self._socket = None
            return False


    def _create_gga_sentence(self) -> str:
        """Creates a GGA sentence based on the current state."""
        state = self._state.get_state_snapshot()
        now = datetime.now(timezone.utc)
        # Format time as hhmmss.ss or hhmmss.sss depending on desired precision
        time_str = now.strftime("%H%M%S.%f")[:9] # hhmmss.ss

        lat, lon, alt = self._config.default_lat, self._config.default_lon, self._config.default_alt
        fix_quality = FIX_QUALITY_GPS # Default to basic GPS fix if no lock
        num_sats = 12 # Default number of sats if no lock
        hdop = DEFAULT_HDOP # Default HDOP if no lock

        if state.get('have_position_lock'):
             pos = state.get('position', {})
             lat = pos.get('lat', self._config.default_lat)
             lon = pos.get('lon', self._config.default_lon)
             alt = pos.get('alt', self._config.default_alt)
             # Use actual fix quality if available and valid, else default to GPS
             current_fix = state.get('fix_type', FIX_QUALITY_INVALID)
             fix_quality = current_fix if current_fix > FIX_QUALITY_INVALID else FIX_QUALITY_GPS
             num_sats = state.get('num_satellites_used', 0)
             hdop = state.get('hdop', DEFAULT_HDOP)
        # else: Use defaults defined above if no lock

        # Convert to NMEA DDMM.MMMMMM format (adjust precision as needed)
        lat_deg = int(abs(lat))
        lat_min = (abs(lat) - lat_deg) * 60
        lat_nmea = f"{lat_deg:02d}{lat_min:09.6f}" # DDMM.MMMMMM format
        lat_dir = "N" if lat >= 0 else "S"

        lon_deg = int(abs(lon))
        lon_min = (abs(lon) - lon_deg) * 60
        lon_nmea = f"{lon_deg:03d}{lon_min:09.6f}" # DDDMM.MMMMMM format
        lon_dir = "E" if lon >= 0 else "W"

        # Altitude (meters) and Geoid Separation (meters)
        alt_str = f"{alt:.1f}"
        sep_str = "-0.0" # Geoid separation - use fixed value if unknown, M is unit

        # Format: $GNGGA,time,lat,N/S,lon,E/W,quality,num_sats,hdop,alt,M,sep,M,diff_age,diff_station*CS
        gga_data = f"GNGGA,{time_str},{lat_nmea},{lat_dir},{lon_nmea},{lon_dir},{fix_quality},{num_sats:02d},{hdop:.2f},{alt_str},M,{sep_str},M,,"
        checksum = GnssDevice._calculate_checksum(gga_data)
        return f"${gga_data}*{checksum}\r\n"


    def _send_gga(self) -> None:
        """Sends the generated GGA sentence to the NTRIP caster."""
        if not self._socket or not self._state.ntrip_connected:
            return

        gga_sentence = self._create_gga_sentence()
        if not gga_sentence:
            self._logger.error("Failed to create GGA sentence.")
            return

        try:
            self._socket.sendall(gga_sentence.encode('ascii'))
            self._logger.debug("Sent GGA to NTRIP server.")
        except (OSError, socket.timeout, BrokenPipeError) as e:
            self._logger.error(f"Error sending GGA to NTRIP: {e}. Disconnecting.")
            self._state.increment_ntrip_error_count("ntrip")
            if self._socket: self._socket.close()
            self._socket = None
            self._state.set_ntrip_connected(False, "GGA Send Error")
        except Exception as e:
             self._logger.error(f"Unexpected error sending GGA: {e}", exc_info=True)
             self._state.increment_ntrip_error_count("ntrip")
             if self._socket: self._socket.close()
             self._socket = None
             self._state.set_ntrip_connected(False, "GGA Send Error")

    @staticmethod
    def _extract_rtcm_message_types(data: bytes) -> List[int]:
        """Extracts message types from a block of RTCM3 data."""
        types_found = []
        i = 0
        data_len = len(data)
        while i < data_len - 5: # Need at least 6 bytes for preamble, length, type start
            if data[i] == 0xD3 and (data[i+1] & 0xC0) == 0:
                try:
                    payload_length = ((data[i+1] & 0x03) << 8) | data[i+2]
                    total_length = 3 + payload_length + 3 # Header(3) + Payload + CRC(3)
                    if i + total_length <= data_len:
                         message_type = (data[i+3] << 4) | (data[i+4] >> 4)
                         types_found.append(message_type)
                         i += total_length
                    else: break # Not enough data for the full message
                except IndexError: break
            else: i += 1
        return types_found

    def _handle_rtcm_data(self, data: bytes) -> None:
        """Processes received RTCM data and sends it to the GNSS device."""
        if not data: return

        first_preamble = data.find(0xD3)
        if first_preamble == -1:
             self._logger.warning(f"Received block without RTCM preamble. Discarding {len(data)} bytes.")
             return
        elif first_preamble > 0:
             self._logger.warning(f"Discarding {first_preamble} non-RTCM bytes before first preamble.")
             data = data[first_preamble:]

        bytes_sent = self._gnss_device.write_data(data)

        if bytes_sent is not None and bytes_sent > 0:
            now = datetime.now(timezone.utc)
            rtcm_types = self._extract_rtcm_message_types(data[:bytes_sent])
            with self._state._lock:
                self._state.ntrip_total_bytes += bytes_sent
                self._state.ntrip_last_data_time = now
                self._state.last_rtcm_data_received = data[:20]
                self._state.rtcm_message_counter += 1
                self._state.ntrip_data_rates.append(bytes_sent)
                if rtcm_types:
                    self._state.last_rtcm_message_types.extend(rtcm_types)
            self._logger.debug(f"Sent {bytes_sent} bytes of RTCM data to GNSS module. Types: {rtcm_types if rtcm_types else 'None Parsed'}")
        elif bytes_sent is None:
             self._logger.error("Failed to send RTCM data to GNSS device (serial error).")


    def _run(self) -> None:
        """Main loop for the NTRIP client thread."""
        self._logger.info("NTRIP run loop started.")
        while self._running.is_set():
            is_connected = self._state.get_state_snapshot()['ntrip_connected']

            if is_connected and self._socket is not None:
                try:
                    self._socket.settimeout(1.0)
                    rtcm_data = self._socket.recv(2048)
                    if rtcm_data:
                        self._handle_rtcm_data(rtcm_data)
                        self._state.update(ntrip_last_data_time=datetime.now(timezone.utc))
                    else:
                        self._logger.info("NTRIP connection closed by server. Reconnecting...")
                        self._state.set_ntrip_connected(False, "Closed by server")
                        if self._socket: self._socket.close()
                        self._socket = None
                        time.sleep(self._reconnect_timeout)
                        continue
                except socket.timeout:
                    now = datetime.now(timezone.utc)
                    if (now - self._last_gga_sent_time).total_seconds() >= NTRIP_GGA_INTERVAL:
                        self._send_gga()
                        self._last_gga_sent_time = now
                    last_data_time = self._state.get_state_snapshot().get('ntrip_last_data_time')
                    if last_data_time and (now - last_data_time).total_seconds() > NTRIP_DATA_TIMEOUT:
                        self._logger.warning(f"No RTCM data received for {NTRIP_DATA_TIMEOUT} seconds. Reconnecting...")
                        self._state.set_ntrip_connected(False, "No data received")
                        if self._socket: self._socket.close()
                        self._socket = None
                        continue
                except (OSError, ConnectionResetError, BrokenPipeError) as e:
                    self._logger.error(f"NTRIP socket error during receive: {e}. Reconnecting...")
                    self._state.increment_ntrip_error_count("ntrip")
                    self._state.set_ntrip_connected(False, f"Receive Error: {str(e)[:20]}")
                    if self._socket: self._socket.close()
                    self._socket = None
                    time.sleep(self._reconnect_timeout)
                    continue
                except Exception as e:
                    self._logger.error(f"Unexpected error in NTRIP receive loop: {e}", exc_info=True)
                    self._state.increment_ntrip_error_count("ntrip")
                    self._state.set_ntrip_connected(False, f"Runtime Error: {str(e)[:20]}")
                    if self._socket: self._socket.close()
                    self._socket = None
                    time.sleep(self._reconnect_timeout)
                    continue
            else:
                # Not connected, attempt to connect (or reconnect)
                # self._logger.debug(f"NTRIP disconnected, attempting connection... Timeout: {self._reconnect_timeout:.1f}s")
                if self._connect():
                     self._reconnect_timeout = NTRIP_INITIAL_RECONNECT_TIMEOUT
                     self._logger.info("NTRIP (re)connected successfully.")
                else:
                    self._reconnect_timeout = min(self._reconnect_timeout * 1.5, NTRIP_MAX_RECONNECT_TIMEOUT)
                    self._logger.info(f"NTRIP connection failed. Retrying in {self._reconnect_timeout:.1f} seconds.")
                    wait_start = time.monotonic()
                    while self._running.is_set() and (time.monotonic() - wait_start) < self._reconnect_timeout:
                         time.sleep(0.5) # Sleep in short intervals to check running flag

        # --- Cleanup after loop exits ---
        if self._socket:
             try: self._socket.close()
             except OSError: pass
             self._socket = None
        self._logger.info("NTRIP run loop finished.")


# --- Status Display (curses based) ---
class StatusDisplay:
    """Formats and prints the system status to the console using curses."""
    def __init__(self, state: GnssState, config: Config):
        self._state = state
        self._config = config
        self._logger = logging.getLogger(self.__class__.__name__)
        # Color pairs (ID, foreground, background)
        curses.start_color()
        curses.init_pair(1, curses.COLOR_GREEN, curses.COLOR_BLACK)
        curses.init_pair(2, curses.COLOR_YELLOW, curses.COLOR_BLACK)
        curses.init_pair(3, curses.COLOR_RED, curses.COLOR_BLACK)
        self.COLOR_GREEN = curses.color_pair(1)
        self.COLOR_YELLOW = curses.color_pair(2)
        self.COLOR_RED = curses.color_pair(3)
        self.COLOR_NORMAL = curses.A_NORMAL
        self.ATTR_BOLD = curses.A_BOLD


    def _check_rtcm_types(self, stdscr, y: int, received_types: deque) -> int:
        """Checks important RTCM types and prints status to curses window."""
        x = 2 # Indentation
        if not received_types:
            stdscr.addstr(y, x, "No RTCM messages received recently.")
            return y + 1

        present_types = set(received_types)
        missing = []
        # Check against the defined important types
        for type_code, type_name in IMPORTANT_RTCM_TYPES.items():
            if type_code not in present_types:
                missing.append(type_name)

        if missing:
            stdscr.addstr(y, x, "*** WARNING: Important RTCM types potentially missing! ***", self.COLOR_RED | self.ATTR_BOLD)
            y += 1
            for m in missing:
                stdscr.addstr(y, x, f"Missing/Not Seen Recently: {m}", self.COLOR_YELLOW)
                y += 1
            stdscr.addstr(y, x, "(Ensure NTRIP mountpoint provides necessary MSM messages for RTK)", self.COLOR_YELLOW)
            y+=1
        else:
             stdscr.addstr(y, x, "All checked important RTCM types seen recently.", self.COLOR_GREEN)
             y+=1
        return y # Return the next line number


    def print_status(self, stdscr, state: Dict[str, Any]) -> None:
        """Prints the formatted status to the provided curses window."""
        stdscr.clear() # Clear screen before drawing
        max_y, max_x = stdscr.getmaxyx() # Get terminal dimensions
        y = 0 # Current line number

        try:
            # --- Header ---
            header = f" LC29HDA RTK Status - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} "
            stdscr.addstr(y, 0, "=" * max_x)
            y += 1
            stdscr.addstr(y, 0, header.center(max_x), self.ATTR_BOLD)
            y += 1
            stdscr.addstr(y, 0, "=" * max_x)
            y += 2 # Add empty line

            # --- Helper Function for Lines ---
            label_width = 25
            value_start_x = label_width + 4
            def line(current_y, label, value, indent=2, attr=self.COLOR_NORMAL):
                if current_y >= max_y -1: return current_y # Prevent writing past screen bottom
                label_str = f"{' ' * indent}{label:<{label_width}}:"
                stdscr.addstr(current_y, 0, label_str)
                # Truncate value if too long for screen width
                value_str = str(value)
                available_width = max_x - value_start_x -1 # -1 for safety
                truncated_value = value_str[:available_width]
                stdscr.addstr(current_y, value_start_x, truncated_value, attr)
                return current_y + 1


            # Runtime
            runtime = datetime.now(timezone.utc) - state['start_time']
            runtime_str = str(runtime).split('.')[0] # Format as HH:MM:SS

            # --- GNSS Information ---
            stdscr.addstr(y, 0, "[GNSS Information]", self.ATTR_BOLD); y += 1
            y = line(y, "Firmware Version", state['firmware_version'])
            y = line(y, "Runtime", runtime_str)
            y = line(y, "Latitude", f"{state['position']['lat']:.8f}\N{DEGREE SIGN}")
            y = line(y, "Longitude", f"{state['position']['lon']:.8f}\N{DEGREE SIGN}")
            y = line(y, "Altitude", f"{state['position']['alt']:.3f} m")

            if state['last_fix_time']:
                fix_age = (datetime.now(timezone.utc) - state['last_fix_time']).total_seconds()
                age_attr = self.COLOR_YELLOW if fix_age > 10 else self.COLOR_NORMAL
                y = line(y, "Age of GNSS Fix", f"{fix_age:.1f} sec", attr=age_attr)
            else:
                y = line(y, "Age of GNSS Fix", "N/A")

            if state.get('first_fix_time_sec') is not None:
                y = line(y, "Time to First Fix", f"{state['first_fix_time_sec']:.1f} sec")
            else:
                y = line(y, "Time to First Fix", "Pending...")

            # RTK Status with color
            rtk_status = state['rtk_status']
            rtk_attr = self.ATTR_BOLD
            if rtk_status == "RTK Fixed": rtk_attr |= self.COLOR_GREEN
            elif rtk_status == "RTK Float": rtk_attr |= self.COLOR_YELLOW
            elif rtk_status == "No Fix / Invalid": rtk_attr |= self.COLOR_RED
            y = line(y, "RTK Status", rtk_status, attr=rtk_attr)

            y = line(y, "Fix Type Code", state['fix_type'])
            y = line(y, "Satellites Used", state['num_satellites_used'])
            y = line(y, "Satellites in View", state['num_satellites_in_view'])
            y = line(y, "Max Satellites Seen", state['max_satellites_seen'])
            y = line(y, "HDOP", f"{state['hdop']:.2f}")

            # Satellite Systems
            if state['satellite_systems']:
                systems_str = ", ".join(f"{sys}: {count}" for sys, count in sorted(state['satellite_systems'].items()))
                y = line(y, "Satellites by System", systems_str)
            else:
                y = line(y, "Satellites by System", "N/A")

            # SNR Statistics
            snr_stats = state['snr_stats']
            if snr_stats and snr_stats.get('avg', 0) > 0:
                y = line(y, "SNR Stats (dB-Hz)", f"Min: {snr_stats['min']:.0f}, Max: {snr_stats['max']:.0f}, Avg: {snr_stats['avg']:.1f}")
                y = line(y, "Signal Quality Counts", f"Good(>=30): {int(snr_stats['good_count'])}, Bad(<20): {int(snr_stats['bad_count'])}")
            else:
                 y = line(y, "SNR Stats (dB-Hz)", "N/A")

            # Fix History
            fix_counter = state['fix_type_counter']
            if fix_counter:
                total_fixes = sum(fix_counter.values())
                if total_fixes > 0:
                    sorted_history = sorted(fix_counter.items(), key=lambda item: item[1], reverse=True)
                    history_str = ", ".join(f"{k}: {v*100/total_fixes:.1f}%" for k, v in sorted_history)
                    y = line(y, "Fix History (%)", history_str)
                else:
                     y = line(y, "Fix History (%)", "No fixes yet")
            y += 1 # Add empty line

            # --- NTRIP Connection ---
            stdscr.addstr(y, 0, "[NTRIP Connection]", self.ATTR_BOLD); y += 1
            y = line(y, "NTRIP Server", f"{self._config.ntrip_server}:{self._config.ntrip_port}/{self._config.ntrip_mountpoint}")

            ntrip_status_msg = state['ntrip_status_message']
            ntrip_conn_status = 'Connected' if state['ntrip_connected'] else 'Disconnected'
            ntrip_attr = self.COLOR_GREEN if state['ntrip_connected'] else self.COLOR_RED
            y = line(y, "NTRIP Status", f"{ntrip_conn_status} - {ntrip_status_msg}", attr=ntrip_attr)

            if state.get('last_ntrip_connect_time_sec') is not None:
                y = line(y, "NTRIP Connect Time", f"{state['last_ntrip_connect_time_sec']:.2f} sec")
            y = line(y, "NTRIP Reconnects", state['ntrip_reconnect_attempts'])
            y = line(y, "Total RTCM Bytes Rx", f"{state['ntrip_total_bytes']:,}")

            if state['ntrip_last_data_time']:
                rtcm_age = (datetime.now(timezone.utc) - state['ntrip_last_data_time']).total_seconds()
                rtcm_age_attr = self.COLOR_RED if rtcm_age > NTRIP_DATA_TIMEOUT else self.COLOR_NORMAL
                y = line(y, "RTCM Data Age", f"{rtcm_age:.1f} sec", attr=rtcm_age_attr)
            else:
                y = line(y, "RTCM Data Age", "N/A")

            # Calculate average data rate
            rates_deque = state['ntrip_data_rates']
            avg_rate_bps = sum(rates_deque) / len(rates_deque) if rates_deque else 0
            y = line(y, "Avg RTCM Rate (last min)", f"{avg_rate_bps:.1f} bytes/sec")

            # RTCM Message Info
            y = line(y, "RTCM Blocks Received", state['rtcm_message_counter'])
            rtcm_types_list = list(state['last_rtcm_message_types'])
            # Truncate long list for display
            max_types_display = (max_x - value_start_x - 5) // 4 # Approx chars per type
            types_str = str(rtcm_types_list) if not rtcm_types_list else str(rtcm_types_list[:max_types_display]) + ('...' if len(rtcm_types_list)>max_types_display else '')
            y = line(y, "Last RTCM Types Seen", types_str if rtcm_types_list else 'None')

            # Check important types (prints directly)
            y = self._check_rtcm_types(stdscr, y, state['last_rtcm_message_types'])
            y += 1

            # --- Diagnostics & Fallback ---
            stdscr.addstr(y, 0, "[Diagnostics]", self.ATTR_BOLD); y += 1
            y = line(y, "GPS Serial/Parse Errors", state['gps_error_count'])
            y = line(y, "NTRIP Connection Errors", state['ntrip_error_count'])
            if state.get('last_command_response_time_sec') is not None:
                 resp_time_ms = state['last_command_response_time_sec'] * 1000
                 y = line(y, "Last GNSS Cmd Resp Time", f"{resp_time_ms:.1f} ms")

            if not state['have_position_lock']:
                y+=1 # Empty line before fallback info
                stdscr.addstr(y, 2, "[INFO: Using Fallback Position for GGA]", self.COLOR_YELLOW); y += 1
                y = line(y, "Default Latitude", f"{self._config.default_lat:.8f}\N{DEGREE SIGN}", indent=4)
                y = line(y, "Default Longitude", f"{self._config.default_lon:.8f}\N{DEGREE SIGN}", indent=4)
                y = line(y, "Default Altitude", f"{self._config.default_alt:.2f} m", indent=4)


            # Add final dividing line
            if y < max_y -1:
                 stdscr.addstr(y+1, 0, "=" * max_x)


        except curses.error as e:
            # Handle potential error if terminal size is too small
            self._logger.error(f"Curses error during printing: {e}. Terminal might be too small.")
            # Try to print a minimal message
            try:
                stdscr.clear()
                stdscr.addstr(0, 0, f"Error: {e}. Terminal too small?")
            except curses.error:
                pass # Avoid recursive error

        finally:
            # Refresh the screen to show updates
            stdscr.refresh()


# --- Main Controller ---
class RtkController:
    """Orchestrates the GNSS device, NMEA parser, NTRIP client, and status display."""
    def __init__(self, config: Config):
        self._config = config
        self._state = GnssState(config.default_lat, config.default_lon, config.default_alt)
        self._gnss_device = GnssDevice(config.serial_port, config.baud_rate, self._state)
        self._nmea_parser = NmeaParser(self._state)
        self._ntrip_client = NtripClient(config, self._state, self._gnss_device)
        # Status display is handled by the main curses loop now
        # self._status_display = StatusDisplay(self._state, config)
        self._running = threading.Event()
        self._gnss_read_thread: Optional[threading.Thread] = None
        self._logger = logger # Use main logger

    def _read_gnss_data_loop(self):
        """Thread loop to continuously read and parse data from GNSS device."""
        self._logger.info("GNSS data reading loop started.")
        while self._running.is_set():
            if not self._gnss_device.is_connected():
                 self._logger.warning("GNSS device disconnected. Attempting reconnect in 5s...")
                 time.sleep(5)
                 if not self._gnss_device.connect():
                      continue # Try again after next loop iteration wait
                 else:
                      self._logger.info("Reconnected to GNSS device.")

            line = self._gnss_device.read_line()
            if line: # Process non-empty lines
                self._logger.debug(f"Received from GNSS: {line}")
                self._nmea_parser.parse(line)
            elif line is None: # Indicates serial error or closed port
                 self._logger.warning("GNSS read loop detected closed/error state. Will attempt reconnect.")
                 time.sleep(2) # Wait before next connection attempt

            # Small sleep to prevent busy-waiting and yield CPU time
            time.sleep(0.005)

        self._logger.info("GNSS data reading loop finished.")

    def start(self) -> bool:
        """Initializes components and starts worker threads."""
        self._logger.info("Starting RTK Controller components...")

        if not self._gnss_device.connect(): # Try connecting here
             self._logger.critical("Failed to connect to GNSS device on startup. Please check port and permissions.")
             return False # Indicate failure

        # Configure the module
        self._gnss_device.configure_module()

        self._running.set() # Set running flag before starting threads

        # Start GNSS reading thread
        self._gnss_read_thread = threading.Thread(target=self._read_gnss_data_loop, name="GnssReadThread", daemon=True)
        self._gnss_read_thread.start()

        # Start NTRIP client thread
        self._ntrip_client.start()

        # Status display thread is replaced by the main curses loop

        self._logger.info("Worker threads started.")
        return True # Indicate success

    def stop(self):
        """Stops all components and threads gracefully."""
        if not self._running.is_set():
             self._logger.info("RTK Controller already stopped.")
             return

        self._logger.info("Stopping RTK Controller components...")
        self._running.clear() # Signal all loops to stop

        # Stop NTRIP client first (it might be writing to GNSS device)
        self._ntrip_client.stop()

        # Stop GNSS reading thread (no need to join daemon thread explicitly)

        # Close serial port (this should help unblock the read thread if it's stuck)
        self._gnss_device.close()

        self._logger.info("RTK Controller components stopped.")

    # Method to get state for the main display loop
    def get_current_state(self) -> Dict[str, Any]:
        return self._state.get_state_snapshot()

    # Property to check if controller should keep running
    @property
    def is_running(self) -> bool:
        return self._running.is_set()


# --- Main Execution with Curses ---
def main_curses(stdscr, args: argparse.Namespace):
    """Main function wrapped by curses."""
    # Curses setup
    curses.curs_set(0) # Hide cursor
    stdscr.nodelay(True) # Make getch non-blocking
    stdscr.timeout(int(STATUS_UPDATE_INTERVAL * 1000)) # Timeout for getch in ms

    # Initialize components
    config = Config(args)
    controller = RtkController(config)
    status_display = StatusDisplay(controller._state, config) # Init display with state/config

    if not controller.start():
        # Need to display error message even within curses
        stdscr.clear()
        stdscr.addstr(0, 0, "Error: Failed to start RTK Controller. Check logs. Press any key to exit.")
        stdscr.refresh()
        stdscr.nodelay(False) # Make getch blocking
        stdscr.getch()
        return # Exit main_curses

    try:
        while controller.is_running:
            # Check for keyboard input (e.g., 'q' to quit)
            try:
                key = stdscr.getch()
                if key == ord('q') or key == ord('Q'):
                    logger.info("Quit key pressed. Shutting down...")
                    break # Exit the main loop
                # Add other key handlers if needed
            except curses.error:
                 # Ignore errors from getch (like timeout)
                 pass


            # Get current state and print status
            current_state = controller.get_current_state()
            status_display.print_status(stdscr, current_state)

            # Optional: Check if worker threads are still alive
            # if not controller._gnss_read_thread.is_alive() or \
            #    not controller._ntrip_client._thread.is_alive():
            #      logger.error("A worker thread has died. Shutting down.")
            #      # Display error in curses window
            #      try:
            #          max_y, max_x = stdscr.getmaxyx()
            #          error_msg = "ERROR: Worker thread died. Check log. Exiting."
            #          stdscr.addstr(max_y - 1, 0, error_msg[:max_x-1], status_display.COLOR_RED | status_display.ATTR_BOLD)
            #          stdscr.refresh()
            #          time.sleep(5) # Show error briefly
            #      except curses.error: pass
            #      break # Exit main loop


            # Wait handled by stdscr.timeout()

    except KeyboardInterrupt:
        logger.info("Ctrl+C received in curses loop. Shutting down...")
    except Exception as e:
         logger.critical(f"Unhandled exception in curses loop: {e}", exc_info=True)
         # Try to display error in curses before exiting wrapper
         try:
             stdscr.clear()
             stdscr.addstr(0, 0, f"FATAL ERROR: {e}. Check log. Press key.")
             stdscr.refresh()
             stdscr.nodelay(False)
             stdscr.getch()
         except: pass # Ignore errors during emergency display
    finally:
        logger.info("Exiting curses loop, stopping controller...")
        controller.stop()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description='LC29HDA RTK GNSS Client (Refactored - Spec V1.4 - Curses UI)',
        formatter_class=argparse.ArgumentDefaultsHelpFormatter # Show defaults in help
    )
    # --- Add Arguments (same as before) ---
    parser.add_argument('--port', default=DEFAULT_SERIAL_PORT, help='Serial port of GNSS receiver')
    parser.add_argument('--baud', type=int, default=DEFAULT_BAUD_RATE, help='Baud rate for serial connection')
    ntrip_group = parser.add_argument_group('NTRIP Caster Configuration')
    ntrip_group.add_argument('--ntrip-server', default=DEFAULT_NTRIP_SERVER, help='NTRIP caster server address')
    ntrip_group.add_argument('--ntrip-port', type=int, default=DEFAULT_NTRIP_PORT, help='NTRIP caster server port')
    ntrip_group.add_argument('--ntrip-mountpoint', default=DEFAULT_NTRIP_MOUNTPOINT, help='NTRIP caster mountpoint')
    ntrip_group.add_argument('--ntrip-user', default=DEFAULT_NTRIP_USERNAME, help='NTRIP username')
    ntrip_group.add_argument('--ntrip-pass', default=DEFAULT_NTRIP_PASSWORD, help='NTRIP password')
    pos_group = parser.add_argument_group('Fallback Position (Used for GGA when no fix)')
    pos_group.add_argument('--default-lat', type=float, default=DEFAULT_LAT, help='Default latitude')
    pos_group.add_argument('--default-lon', type=float, default=DEFAULT_LON, help='Default longitude')
    pos_group.add_argument('--default-alt', type=float, default=DEFAULT_ALT, help='Default altitude (meters)')
    log_group = parser.add_argument_group('Logging Configuration')
    log_group.add_argument('--log-file', default='lc29hda_rtk.log', help='Log file name (curses UI disables console logging)')
    log_group.add_argument('--debug', action='store_true', help='Enable debug level logging to file')
    # --- End Arguments ---

    args = parser.parse_args()

    # --- Setup File Logging ONLY ---
    log_level = logging.DEBUG if args.debug else logging.INFO
    log_formatter = logging.Formatter(LOG_FORMAT)
    try:
        # Ensure logger is clean before adding handler
        root_logger = logging.getLogger()
        root_logger.handlers.clear()
        root_logger.setLevel(log_level)

        file_handler = logging.FileHandler(args.log_file, mode='w') # Overwrite log each run
        file_handler.setFormatter(log_formatter)
        file_handler.setLevel(logging.DEBUG) # Log everything to file
        root_logger.addHandler(file_handler)
        logger.info(f"File logging setup ({args.log_file}) at level {logging.getLevelName(log_level)}")
        if args.debug: logger.debug("Debug logging is ON.")
    except Exception as e:
         print(f"Error setting up file logger ({args.log_file}): {e}", file=sys.stderr)
         sys.exit(1)
    # --- End Logging Setup ---


    # Run the main application within the curses wrapper
    try:
        curses.wrapper(main_curses, args)
        print("Application finished normally.")
    except curses.error as e:
         print(f"Curses initialization failed: {e}", file=sys.stderr)
         print("Ensure your terminal supports curses (e.g., not basic Windows cmd, use WSL, Linux terminal, macOS terminal).", file=sys.stderr)
         sys.exit(1)
    except Exception as e:
         print(f"An unexpected error occurred: {e}", file=sys.stderr)
         logger.critical(f"Unhandled exception preventing curses wrapper: {e}", exc_info=True)
         sys.exit(1)
    finally:
        # Ensure logging is shutdown cleanly
        logging.shutdown()
