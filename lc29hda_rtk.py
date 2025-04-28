# lc29hda_rtk.py - Refactored version V1.4 spec update
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
DEFAULT_HDOP = 99.99    # Default/Invalid HDOP value according to Spec V1.4 [cite: 103]

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

# Check V1.4 Spec Table 8 for relevant input messages [cite: 693]
IMPORTANT_RTCM_TYPES = {
    RTCM_MSG_TYPE_GPS_MSM7: "GPS MSM7 (1077)",
    RTCM_MSG_TYPE_GLONASS_MSM7: "GLONASS MSM7 (1087)",
    RTCM_MSG_TYPE_GALILEO_MSM7: "Galileo MSM7 (1097)",
    RTCM_MSG_TYPE_BDS_MSM7: "BDS MSM7 (1127)",
    # RTCM_MSG_TYPE_QZSS_MSM7: "QZSS MSM7 (1117)", # Optional
    RTCM_MSG_TYPE_ARP_1005: "ARP (1005/1006)", # Base station position
}
# Add MSM4/MSM5 types if needed and provided by caster:
# 1074, 1075 (GPS), 1084, 1085 (GLO), 1094, 1095 (GAL), 1124, 1125 (BDS) [cite: 696, 697, 698, 699]

LOG_FORMAT = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'

# --- Logging Setup ---
logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
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

        if self.debug:
            logging.getLogger().setLevel(logging.DEBUG)
            for handler in logging.getLogger().handlers:
                handler.setLevel(logging.DEBUG)
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
        self.hdop: float = DEFAULT_HDOP # Updated default [cite: 103]
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
        self.connect()

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
            # Attempt to reconnect or handle error
            self.close()
            # self.connect() # Avoid immediate reconnect here, let main loop handle it
            return None
        except Exception as e:
            self._logger.error(f"Unexpected error sending command '{command}': {e}", exc_info=True)
            self._state.increment_error_count("gps")
            return None

    def read_line(self) -> Optional[str]:
        """Reads a line from the serial port."""
        if not self.is_connected():
            # self._logger.warning("Cannot read line: Serial port not connected.")
            # Don't log repeatedly if connection is down
            return None # Let the caller handle reconnection attempts

        try:
            if self._serial_port.in_waiting > 0:
                line_bytes = self._serial_port.readline()
                return line_bytes.decode('ascii', errors='ignore').strip()
            else:
                # No data waiting, return None immediately (makes loop responsive)
                return "" # Return empty string to distinguish from error/closed port
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

        # Query Firmware Version first using PQTM command [cite: 197]
        version_response = self.send_command("PQTMVERNO")
        if version_response:
            # Example response: $PQTMVERNO,LC29HDANR01A04S,2021/12/28,16:11:48*41
            try:
                # Simple parsing, assuming format consistency
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

        # Set baud rate using PAIR command [cite: 676]
        # PortType 0=UART, PortIndex 0=UART1 [cite: 677]
        # self.send_command(f"PAIR864,0,0,{self._baudrate}")
        # time.sleep(0.5) # Allow time to process, though spec says reboot required [cite: 678]

        # Enable specific NMEA sentences at 1Hz using PAIR062 [cite: 508]
        # <OutputRate>=1 means output once per fix (default fix rate is 1Hz)
        # Supported for DA: 0(GGA), 1(GLL), 2(GSA), 3(GSV), 4(RMC), 5(VTG) [cite: 513]
        self.send_command("PAIR062,0,1"); time.sleep(0.15)  # GGA
        self.send_command("PAIR062,4,1"); time.sleep(0.15)  # RMC
        self.send_command("PAIR062,2,1"); time.sleep(0.15)  # GSA
        self.send_command("PAIR062,3,1"); time.sleep(0.15)  # GSV
        self.send_command("PAIR062,5,1"); time.sleep(0.15)  # VTG
        # self.send_command("PAIR062,1,1"); time.sleep(0.15) # GLL (Optional)
        # Disable unsupported messages explicitly? Not strictly necessary if default is off
        # self.send_command("PAIR062,6,0"); time.sleep(0.15) # ZDA (Not supported anyway [cite: 158])
        # self.send_command("PAIR062,7,0"); time.sleep(0.15) # GRS (Not supported anyway [cite: 166])
        # self.send_command("PAIR062,8,0"); time.sleep(0.15) # GST (Not supported anyway [cite: 180])
        # self.send_command("PAIR062,9,0"); time.sleep(0.15) # GNS (Not supported anyway [cite: 193])


        # Enable RTCM Ephemeris Output (if needed by NTRIP/RTK setup)
        # PAIR436 is available on LC29H series [cite: 648]
        self.send_command("PAIR436,1") # Enable Ephemeris output
        time.sleep(0.2)

        # Save settings to NVM using PAIR513 [cite: 658]
        self.send_command("PAIR513")
        time.sleep(0.5) # Allow time for save operation

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
            FIX_QUALITY_DGPS: "DGPS", # Includes SBAS/DGPS [cite: 103]
            FIX_QUALITY_GPS: "GPS (SPS)", # Basic GPS fix [cite: 103]
            FIX_QUALITY_ESTIMATED: "Estimated (DR)", # Dead Reckoning [cite: 103]
            FIX_QUALITY_INVALID: "No Fix / Invalid" # Fix not available [cite: 103]
            # FIX_QUALITY_PPS is not listed as output in GGA spec [cite: 103]
        }
        return status_map.get(fix_type, "Unknown Fix Type")


    def _parse_gga(self, msg: pynmea2.types.talker.GGA) -> None:
        """Parses GGA message content."""
        current_state = self._state.get_state_snapshot() # Get a consistent snapshot
        old_fix_type = current_state['fix_type']
        # Ensure gps_qual is treated as integer, default to invalid if empty/None
        new_fix_type = int(msg.gps_qual) if msg.gps_qual is not None else FIX_QUALITY_INVALID
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


        updates['num_satellites_used'] = int(msg.num_sats) if msg.num_sats is not None else 0
        # Use DEFAULT_HDOP (99.99) if value is missing or invalid [cite: 103]
        updates['hdop'] = float(msg.horizontal_dil) if msg.horizontal_dil is not None else DEFAULT_HDOP

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
        # Note: epochs_since_fix is only meaningful if we *had* an RTK fix previously.
        # This counter continues even if fix degrades, indicating time since *last* RTK fix.
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
        num_sv_in_view = int(msg.num_sv_in_view) if msg.num_sv_in_view is not None else 0

        # GSV messages come in sequences. Reset satellite info at the start of a new sequence.
        is_first_sentence = (int(msg.sentence_num) == 1)
        if is_first_sentence:
            # Keep a temporary dict for this sequence to avoid partial updates on error
            self._current_gsv_sequence_sats = {}
            self._current_gsv_systems = Counter()

        # Determine constellation from talker ID (GP, GL, GA, GB, GQ, GI) [cite: 82]
        sat_system = "Unknown"
        talker = msg.talker
        if talker == 'GP': sat_system = "GPS"
        elif talker == 'GL': sat_system = "GLONASS"
        elif talker == 'GA': sat_system = "Galileo"
        elif talker == 'GB': sat_system = "BeiDou"
        elif talker == 'GQ': sat_system = "QZSS"
        elif talker == 'GI': sat_system = "NavIC" # Added in V1.4 [cite: 82]


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
                # Ensure SNR is integer, default 0 if None/empty
                snr = int(snr_val) if snr_val else 0
                # Ensure elevation/azimuth are integers, None if missing
                elev = int(getattr(msg, elev_field)) if getattr(msg, elev_field) else None
                azim = int(getattr(msg, azim_field)) if getattr(msg, azim_field) else None

                # Use a unique key: TalkerID-PRN (e.g., "GP-15", "GL-70")
                # This handles potential PRN overlaps between constellations.
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
        is_last_sentence = (int(msg.sentence_num) == int(msg.num_sentences))
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
            # Clear temporary sequence data
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
        """Parses GSA message to mark active satellites and update DOP."""
        active_sat_keys = set()
        talker = msg.talker # GP, GL, GA, GB, GN, GI etc. [cite: 82]

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
                          current_sats = self._state.get_state_snapshot()['satellites_info']
                          for key, sat_info in current_sats.items():
                              # Match PRN number directly (could be ambiguous if PRNs overlap and are used simultaneously)
                              if sat_info.get('prn') == prn:
                                   active_sat_keys.add(key)
                                   found = True
                                   # Don't break, could potentially be listed under multiple talkers if PRNs overlap? Unlikely.
                          if not found:
                               self._logger.debug(f"GNGSA referenced PRN {prn} which was not found in recent GSV data.")
                     else:
                          # For specific talkers (GP, GL, etc.), directly add the key
                          active_sat_keys.add(sat_key)

        # Update the 'active' status in the shared state's satellite info
        # Lock needed because we are modifying the dictionary within GnssState
        with self._state._lock:
            # Create a temporary copy to iterate over while modifying the original
            current_sat_info = self._state.satellites_info.copy()
            for key, sat_info in current_sat_info.items():
                 if key in active_sat_keys:
                     # Mark as active if found in this GSA message
                     self._state.satellites_info[key]['active'] = True
                 else:
                     # If not in this GSA msg, mark as inactive *only if* it belongs to the same talker
                     # This prevents a GPGSA message from deactivating GLONASS sats etc.
                     # It also handles the case where a satellite is active in GN GSA but not specific GSA.
                     if talker != 'GN' and key.startswith(talker + '-'):
                          self._state.satellites_info[key]['active'] = False
                     # If talker is GN, we don't deactivate satellites based on this message alone,
                     # as it only lists a subset. Let specific talker GSAs handle deactivation.


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
                # Shutdown may fail if socket is already closed or in weird state
                self._socket.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass # Ignore errors on shutdown
            try:
                 self._socket.close()
            except OSError as e:
                 self._logger.warning(f"Error closing NTRIP socket: {e}")
            finally:
                 self._socket = None

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

            # Include NMEA-GGA header if caster requires it for location
            # gga_header_line = f"Ntrip-GGA: {self._create_gga_sentence().strip()}" # Create GGA first
            gga_header_line = "" # Some casters don't want/need it in initial GET

            request_lines = [
                f"GET /{self._config.ntrip_mountpoint} HTTP/1.1", # Use HTTP/1.1 for Host header
                f"Host: {self._config.ntrip_server}:{self._config.ntrip_port}",
                "Ntrip-Version: Ntrip/1.0",
                "User-Agent: Python NtripClient/1.1",
                f"Authorization: Basic {auth_b64}",
                "Accept: */*",
                "Connection: close", # Close connection after response
            ]
            # if gga_header_line:
            #     request_lines.append(gga_header_line)
            request_lines.append("\r\n") # Extra CRLF to end headers

            request = "\r\n".join(request_lines)
            # -----------------------------------------------

            self._logger.debug(f"Sending NTRIP request:\n{request.strip()}")
            self._socket.sendall(request.encode('ascii'))

            # --- Check Response ---
            response_bytes = bytearray()
            # Read headers first (end with \r\n\r\n)
            while b"\r\n\r\n" not in response_bytes:
                 chunk = self._socket.recv(1024)
                 if not chunk:
                     raise ConnectionAbortedError("NTRIP server closed connection during header read")
                 response_bytes.extend(chunk)

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
        except (socket.gaierror, ConnectionRefusedError, ConnectionAbortedError, OSError) as e:
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
        fix_quality = FIX_QUALITY_GPS # Default to basic GPS fix if no lock [cite: 103]
        num_sats = 12 # Default number of sats if no lock
        hdop = DEFAULT_HDOP # Default HDOP if no lock [cite: 103]

        if state.get('have_position_lock'):
             pos = state.get('position', {})
             lat = pos.get('lat', self._config.default_lat)
             lon = pos.get('lon', self._config.default_lon)
             alt = pos.get('alt', self._config.default_alt)
             # Use actual fix quality if available and valid, else default to GPS [cite: 103]
             current_fix = state.get('fix_type', FIX_QUALITY_INVALID)
             fix_quality = current_fix if current_fix > FIX_QUALITY_INVALID else FIX_QUALITY_GPS
             num_sats = state.get('num_satellites_used', 0)
             hdop = state.get('hdop', DEFAULT_HDOP)
        # else: Use defaults defined above if no lock

        # Convert to NMEA DDMM.MMMMMM format (adjust precision as needed)
        # NMEA V4.1 requires variable length, up to mmmmmm [cite: 100, 103]
        lat_deg = int(abs(lat))
        lat_min = (abs(lat) - lat_deg) * 60
        lat_nmea = f"{lat_deg:02d}{lat_min:09.6f}" # DDMM.MMMMMM format
        lat_dir = "N" if lat >= 0 else "S"

        lon_deg = int(abs(lon))
        lon_min = (abs(lon) - lon_deg) * 60
        lon_nmea = f"{lon_deg:03d}{lon_min:09.6f}" # DDDMM.MMMMMM format
        lon_dir = "E" if lon >= 0 else "W"

        # Altitude (meters) and Geoid Separation (meters)
        # Ensure minimum precision (e.g., .1 for alt, .1 for sep) [cite: 103, 106]
        alt_str = f"{alt:.1f}"
        sep_str = "-0.0" # Geoid separation - use fixed value if unknown, M is unit [cite: 106]

        # Format: $GPGGA,time,lat,N/S,lon,E/W,quality,num_sats,hdop,alt,M,sep,M,diff_age,diff_station*CS
        # Use GNGGA for multi-constellation talker ID
        gga_data = f"GNGGA,{time_str},{lat_nmea},{lat_dir},{lon_nmea},{lon_dir},{fix_quality},{num_sats:02d},{hdop:.2f},{alt_str},M,{sep_str},M,,"
        checksum = GnssDevice._calculate_checksum(gga_data)
        return f"${gga_data}*{checksum}\r\n"


    def _send_gga(self) -> None:
        """Sends the generated GGA sentence to the NTRIP caster."""
        if not self._socket or not self._state.ntrip_connected:
            # Don't log warning every time if disconnected, handled by _run loop
            # self._logger.warning("Cannot send GGA: NTRIP socket not connected.")
            return

        gga_sentence = self._create_gga_sentence()
        if not gga_sentence:
            self._logger.error("Failed to create GGA sentence.")
            return

        try:
            sent_bytes = self._socket.sendall(gga_sentence.encode('ascii'))
            # sendall returns None on success, raises exception on error
            self._logger.debug("Sent GGA to NTRIP server.")
        except (OSError, socket.timeout, BrokenPipeError) as e:
            self._logger.error(f"Error sending GGA to NTRIP: {e}. Disconnecting.")
            self._state.increment_ntrip_error_count("ntrip")
            # Connection likely broken, trigger reconnect by closing socket
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
            # Find RTCM3 preamble (0xD3)
            if data[i] == 0xD3 and (data[i+1] & 0xC0) == 0: # Check reserved bits (bits 6,7 of byte 1) are 0
                try:
                    # --- RTCM3 Header Parsing ---
                    # Message Length (10 bits in bytes i+1, i+2)
                    payload_length = ((data[i+1] & 0x03) << 8) | data[i+2]
                    total_length = 3 + payload_length + 3 # Header(3) + Payload + CRC(3)

                    # Check if full message is likely present in the buffer
                    if i + total_length <= data_len:
                         # Message Type (12 bits in bytes i+3, i+4)
                         # Shift byte 3 left by 4 bits, OR with high 4 bits of byte 4
                         message_type = (data[i+3] << 4) | (data[i+4] >> 4)
                         types_found.append(message_type)

                         # TODO: Add CRC check here for robustness if needed
                         # crc_calculated = calculate_rtcm_crc(data[i : i + 3 + payload_length])
                         # crc_received = int.from_bytes(data[i + 3 + payload_length : i + total_length], 'big')
                         # if crc_calculated != crc_received:
                         #     self._logger.warning(f"RTCM CRC mismatch for type {message_type}")
                         #     # Decide whether to skip this message or the rest of the buffer

                         # Move index past this complete message
                         i += total_length
                    else:
                        # Not enough data for the full message indicated by the length field
                        # Stop parsing this buffer chunk, wait for more data
                        break
                    # --------------------------
                except IndexError:
                     # Should not happen with length check, but guard anyway
                     break
            else:
                # Preamble not found, move to the next byte
                i += 1
        return types_found

    def _handle_rtcm_data(self, data: bytes) -> None:
        """Processes received RTCM data and sends it to the GNSS device."""
        if not data:
            return

        # Basic validation: Check for RTCM preamble at the start
        if data[0] != 0xD3:
             # It's possible multiple messages are in 'data', find first preamble
             first_preamble = data.find(0xD3)
             if first_preamble == -1:
                 self._logger.warning(f"Received block without RTCM preamble. Discarding {len(data)} bytes.")
                 return
             elif first_preamble > 0:
                 self._logger.warning(f"Discarding {first_preamble} non-RTCM bytes before first preamble.")
                 data = data[first_preamble:] # Process data starting from the first found preamble


        # Attempt to send data to the GNSS device
        bytes_sent = self._gnss_device.write_data(data)

        if bytes_sent is not None and bytes_sent > 0:
            now = datetime.now(timezone.utc)
            # Extract message types before locking state
            rtcm_types = self._extract_rtcm_message_types(data[:bytes_sent]) # Parse only what was potentially sent

            # Update state safely (only update if write succeeded)
            with self._state._lock:
                self._state.ntrip_total_bytes += bytes_sent
                self._state.ntrip_last_data_time = now
                self._state.last_rtcm_data_received = data[:20] # Store snippet of received block
                self._state.rtcm_message_counter += 1 # Count blocks received/processed
                self._state.ntrip_data_rates.append(bytes_sent) # Store bytes for rate calc
                if rtcm_types:
                    self._state.last_rtcm_message_types.extend(rtcm_types)

            self._logger.debug(f"Sent {bytes_sent} bytes of RTCM data to GNSS module. Types: {rtcm_types if rtcm_types else 'None Parsed'}")

        elif bytes_sent is None:
             self._logger.error("Failed to send RTCM data to GNSS device (serial error).")
             # Error already counted by GnssDevice, port likely closed.
        # else: bytes_sent == 0, logged by GnssDevice if it was a timeout


    def _run(self) -> None:
        """Main loop for the NTRIP client thread."""
        self._logger.info("NTRIP run loop started.")
        while self._running.is_set():
            # Check connection status at the start of each loop iteration
            is_connected = self._state.get_state_snapshot()['ntrip_connected']

            if is_connected and self._socket is not None:
                try:
                    # Set short timeout for reading data to keep loop responsive
                    self._socket.settimeout(1.0)
                    rtcm_data = self._socket.recv(2048) # Read up to 2KB

                    if rtcm_data:
                        # Data received, process it
                        self._handle_rtcm_data(rtcm_data)
                        # Update last data time only after successful processing/sending
                        self._state.update(ntrip_last_data_time=datetime.now(timezone.utc))
                    else:
                        # Socket closed by server (recv returned empty bytes)
                        self._logger.info("NTRIP connection closed by server. Reconnecting...")
                        self._state.set_ntrip_connected(False, "Closed by server")
                        if self._socket: self._socket.close()
                        self._socket = None
                        # No immediate reconnect here, let the 'else' block handle it after a delay
                        time.sleep(self._reconnect_timeout) # Wait before trying to reconnect
                        continue # Go to next loop iteration to attempt reconnect

                except socket.timeout:
                    # No data received in the timeout period, this is normal
                    # Check if it's time to send GGA
                    now = datetime.now(timezone.utc)
                    if (now - self._last_gga_sent_time).total_seconds() >= NTRIP_GGA_INTERVAL:
                        self._send_gga() # This handles its own errors and potential disconnect
                        self._last_gga_sent_time = now

                    # Check for data timeout (no data received for a longer period)
                    last_data_time = self._state.get_state_snapshot().get('ntrip_last_data_time')
                    if last_data_time and (now - last_data_time).total_seconds() > NTRIP_DATA_TIMEOUT:
                        self._logger.warning(f"No RTCM data received for {NTRIP_DATA_TIMEOUT} seconds. Reconnecting...")
                        self._state.set_ntrip_connected(False, "No data received")
                        if self._socket: self._socket.close()
                        self._socket = None
                        # No wait here, let 'else' block handle reconnect attempt immediately
                        continue # Trigger reconnect logic in the next iteration

                except (OSError, ConnectionResetError, BrokenPipeError) as e:
                    # Handle socket errors during receive
                    self._logger.error(f"NTRIP socket error during receive: {e}. Reconnecting...")
                    self._state.increment_ntrip_error_count("ntrip")
                    self._state.set_ntrip_connected(False, f"Receive Error: {str(e)[:20]}")
                    if self._socket: self._socket.close()
                    self._socket = None
                    time.sleep(self._reconnect_timeout) # Wait before reconnecting
                    continue
                except Exception as e:
                    # Catch unexpected errors in the loop
                    self._logger.error(f"Unexpected error in NTRIP receive loop: {e}", exc_info=True)
                    self._state.increment_ntrip_error_count("ntrip")
                    self._state.set_ntrip_connected(False, f"Runtime Error: {str(e)[:20]}")
                    if self._socket: self._socket.close()
                    self._socket = None
                    time.sleep(self._reconnect_timeout) # Wait before reconnecting
                    continue


            else:
                # Not connected, attempt to connect (or reconnect)
                self._logger.debug(f"NTRIP disconnected, attempting connection... Timeout: {self._reconnect_timeout:.1f}s")
                if self._connect():
                     # Connection successful, reset backoff timeout
                     self._reconnect_timeout = NTRIP_INITIAL_RECONNECT_TIMEOUT
                     self._logger.info("NTRIP reconnected successfully.")
                else:
                    # Connection failed, increase backoff timeout
                    self._reconnect_timeout = min(self._reconnect_timeout * 1.5, NTRIP_MAX_RECONNECT_TIMEOUT)
                    self._logger.info(f"NTRIP connection failed. Retrying in {self._reconnect_timeout:.1f} seconds.")
                    # Wait before retrying, check running flag periodically
                    wait_start = time.monotonic()
                    while self._running.is_set() and (time.monotonic() - wait_start) < self._reconnect_timeout:
                         time.sleep(0.5) # Sleep in short intervals to check running flag

        # --- Cleanup after loop exits ---
        if self._socket:
             try:
                 self._socket.close()
             except OSError: pass # Ignore errors during final close
             self._socket = None
        self._logger.info("NTRIP run loop finished.")


# --- Status Display ---
class StatusDisplay:
    """Formats and prints the system status to the console."""
    def __init__(self, state: GnssState, config: Config):
        self._state = state
        self._config = config
        self._logger = logging.getLogger(self.__class__.__name__)
        self._running = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self):
        """Starts the status display thread."""
        if self._thread is not None and self._thread.is_alive():
            self._logger.warning("Status display thread already running.")
            return
        self._running.set()
        self._thread = threading.Thread(target=self._run, name="StatusThread", daemon=True)
        self._thread.start()
        self._logger.info("Status display thread started.")

    def stop(self):
        """Stops the status display thread."""
        self._running.clear()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=STATUS_UPDATE_INTERVAL + 0.5)
        self._logger.info("Status display stopped.")

    def _check_rtcm_types(self, received_types: deque) -> None:
        """Checks if important RTCM types are present in recent messages."""
        if not received_types:
            print("\n  No RTCM messages received recently.")
            return

        present_types = set(received_types)
        missing = []
        # Check against the defined important types
        for type_code, type_name in IMPORTANT_RTCM_TYPES.items():
            if type_code not in present_types:
                missing.append(type_name)

        if missing:
            print("\n  \033[91m*** WARNING: Important RTCM types potentially missing! ***\033[0m")
            for m in missing:
                print(f"  Missing/Not Seen Recently: {m}")
            print("  \033[93m(Ensure NTRIP mountpoint provides necessary MSM messages for RTK)\033[0m")
        else:
             print("\n  \033[92mAll checked important RTCM types seen recently.\033[0m")


    def _run(self) -> None:
        """Main loop for the status display thread."""
        while self._running.is_set():
            try:
                state = self._state.get_state_snapshot()
                self._print_status(state)
                # Wait for the next interval, checking the running flag
                # Use wait() on the event for cleaner exit handling
                self._running.wait(timeout=STATUS_UPDATE_INTERVAL)
            except Exception as e:
                self._logger.error(f"Error in status display loop: {e}", exc_info=True)
                # Avoid rapid looping on error
                time.sleep(STATUS_UPDATE_INTERVAL)
        self._logger.info("Status display loop finished.")


    def _print_status(self, state: Dict[str, Any]) -> None:
        """Prints the formatted status based on the provided state."""
        # ANSI escape code to clear screen and move cursor to top-left
        print("\033[H\033[J", end="")
        print("=" * 60)
        print(f" LC29HDA RTK Status - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ")
        print("=" * 60)

        def line(label, value, indent=2):
            print(f"{' ' * indent}{label:<25}: {value}")

        # Runtime
        runtime = datetime.now(timezone.utc) - state['start_time']
        runtime_str = str(runtime).split('.')[0] # Format as HH:MM:SS

        # --- GNSS Information ---
        print("\n[GNSS Information]")
        line("Firmware Version", state['firmware_version'])
        line("Runtime", runtime_str)
        line("Latitude", f"{state['position']['lat']:.8f}\N{DEGREE SIGN}") # Unicode degree symbol
        line("Longitude", f"{state['position']['lon']:.8f}\N{DEGREE SIGN}")
        line("Altitude", f"{state['position']['alt']:.3f} m")

        if state['last_fix_time']:
            fix_age = (datetime.now(timezone.utc) - state['last_fix_time']).total_seconds()
            line("Age of GNSS Fix", f"{fix_age:.1f} sec")
        else:
            line("Age of GNSS Fix", "N/A")

        if state.get('first_fix_time_sec') is not None:
            line("Time to First Fix", f"{state['first_fix_time_sec']:.1f} sec")
        else:
            line("Time to First Fix", "Pending...")

        rtk_status = state['rtk_status']
        # Add color to RTK status
        if rtk_status == "RTK Fixed": color_code = "\033[92m" # Green
        elif rtk_status == "RTK Float": color_code = "\033[93m" # Yellow
        elif rtk_status == "No Fix / Invalid": color_code = "\033[91m" # Red
        else: color_code = "\033[0m" # Default
        line("RTK Status", f"{color_code}\033[1m{rtk_status}\033[0m") # Bold status + color reset

        line("Fix Type Code", state['fix_type'])
        line("Satellites Used", state['num_satellites_used'])
        line("Satellites in View", state['num_satellites_in_view'])
        line("Max Satellites Seen", state['max_satellites_seen'])
        line("HDOP", f"{state['hdop']:.2f}")

        # Satellite Systems
        if state['satellite_systems']:
            systems_str = ", ".join(f"{sys}: {count}" for sys, count in sorted(state['satellite_systems'].items()))
            line("Satellites by System", systems_str)
        else:
            line("Satellites by System", "N/A")

        # SNR Statistics
        snr_stats = state['snr_stats']
        if snr_stats and snr_stats.get('avg', 0) > 0:
            line("SNR Stats (dB-Hz)", f"Min: {snr_stats['min']:.0f}, Max: {snr_stats['max']:.0f}, Avg: {snr_stats['avg']:.1f}")
            line("Signal Quality Counts", f"Good(>=30): {int(snr_stats['good_count'])}, Bad(<20): {int(snr_stats['bad_count'])}")
        else:
             line("SNR Stats (dB-Hz)", "N/A")

        # Fix History
        fix_counter = state['fix_type_counter']
        if fix_counter:
            total_fixes = sum(fix_counter.values())
            if total_fixes > 0:
                # Sort by count descending for readability
                sorted_history = sorted(fix_counter.items(), key=lambda item: item[1], reverse=True)
                history_str = ", ".join(f"{k}: {v*100/total_fixes:.1f}%" for k, v in sorted_history)
                line("Fix History (%)", history_str)
            else:
                 line("Fix History (%)", "No fixes yet")


        # --- NTRIP Connection ---
        print("\n[NTRIP Connection]")
        line("NTRIP Server", f"{self._config.ntrip_server}:{self._config.ntrip_port}/{self._config.ntrip_mountpoint}")
        ntrip_status_msg = state['ntrip_status_message']
        ntrip_conn_status = 'Connected' if state['ntrip_connected'] else 'Disconnected'
        color_code = "\033[92m" if state['ntrip_connected'] else "\033[91m" # Green/Red
        line("NTRIP Status", f"{color_code}{ntrip_conn_status}\033[0m - {ntrip_status_msg}")

        if state.get('last_ntrip_connect_time_sec') is not None:
            line("NTRIP Connect Time", f"{state['last_ntrip_connect_time_sec']:.2f} sec")
        line("NTRIP Reconnects", state['ntrip_reconnect_attempts'])
        line("Total RTCM Bytes Rx", f"{state['ntrip_total_bytes']:,}") # Formatted number

        if state['ntrip_last_data_time']:
            rtcm_age = (datetime.now(timezone.utc) - state['ntrip_last_data_time']).total_seconds()
            color_code = "\033[91m" if rtcm_age > NTRIP_DATA_TIMEOUT else "\033[0m"
            line("RTCM Data Age", f"{color_code}{rtcm_age:.1f} sec\033[0m")
        else:
            line("RTCM Data Age", "N/A")

        # Calculate average data rate over the window (approx 60s)
        rates_deque = state['ntrip_data_rates']
        if rates_deque:
             # Ensure we don't divide by zero if deque becomes empty between snapshot and here
             avg_rate_bps = sum(rates_deque) / len(rates_deque) if rates_deque else 0
             line("Avg RTCM Rate (last min)", f"{avg_rate_bps:.1f} bytes/sec")
        else:
             line("Avg RTCM Rate (last min)", "0.0 bytes/sec")


        # RTCM Message Info
        line("RTCM Blocks Received", state['rtcm_message_counter']) # Changed label for clarity
        rtcm_types_list = list(state['last_rtcm_message_types'])
        line("Last RTCM Types Seen", f"{rtcm_types_list if rtcm_types_list else 'None'}")
        # Check important types
        self._check_rtcm_types(state['last_rtcm_message_types'])


        # --- Diagnostics & Fallback ---
        print("\n[Diagnostics]")
        line("GPS Serial/Parse Errors", state['gps_error_count'])
        line("NTRIP Connection Errors", state['ntrip_error_count'])
        if state.get('last_command_response_time_sec') is not None:
             resp_time_ms = state['last_command_response_time_sec'] * 1000
             line("Last GNSS Cmd Resp Time", f"{resp_time_ms:.1f} ms")

        if not state['have_position_lock']:
            print("\n  \033[93m[INFO: Using Fallback Position for GGA]\033[0m")
            line("  Default Latitude", f"{self._config.default_lat:.8f}\N{DEGREE SIGN}", indent=4)
            line("  Default Longitude", f"{self._config.default_lon:.8f}\N{DEGREE SIGN}", indent=4)
            line("  Default Altitude", f"{self._config.default_alt:.2f} m", indent=4)


        print("\n" + "=" * 60)
        sys.stdout.flush() # Ensure output is immediate

# --- Main Controller ---
class RtkController:
    """Orchestrates the GNSS device, NMEA parser, NTRIP client, and status display."""
    def __init__(self, config: Config):
        self._config = config
        self._state = GnssState(config.default_lat, config.default_lon, config.default_alt)
        self._gnss_device = GnssDevice(config.serial_port, config.baud_rate, self._state)
        self._nmea_parser = NmeaParser(self._state)
        self._ntrip_client = NtripClient(config, self._state, self._gnss_device)
        self._status_display = StatusDisplay(self._state, config)
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
                      # Optional: Re-configure after reconnect?
                      # self._gnss_device.configure_module()


            line = self._gnss_device.read_line()
            if line: # Process non-empty lines
                self._logger.debug(f"Received from GNSS: {line}")
                self._nmea_parser.parse(line)
            elif line is None: # Indicates serial error or closed port
                 self._logger.warning("GNSS read loop detected closed/error state. Will attempt reconnect.")
                 time.sleep(2) # Wait before next connection attempt
            # else: line is "", meaning no data available currently, just loop again

            # Small sleep to prevent busy-waiting if read_line is very fast and often empty
            time.sleep(0.005)

        self._logger.info("GNSS data reading loop finished.")

    def start(self) -> bool:
        """Initializes components and starts all threads."""
        self._logger.info("Starting RTK Controller...")

        if not self._gnss_device.is_connected():
             self._logger.critical("Failed to connect to GNSS device on startup. Please check port and permissions. Exiting.")
             return False # Indicate failure

        # Configure the module
        self._gnss_device.configure_module()

        self._running.set() # Set running flag before starting threads

        # Start GNSS reading thread
        self._gnss_read_thread = threading.Thread(target=self._read_gnss_data_loop, name="GnssReadThread", daemon=True)
        self._gnss_read_thread.start()

        # Start NTRIP client thread
        self._ntrip_client.start()

        # Start Status display thread
        self._status_display.start()

        self._logger.info("All components started. Press Ctrl+C to stop.")
        return True # Indicate success

    def stop(self):
        """Stops all components and threads gracefully."""
        if not self._running.is_set():
             self._logger.info("RTK Controller already stopped.")
             return

        self._logger.info("Stopping RTK Controller...")
        self._running.clear() # Signal all loops to stop

        # Stop threads in an order that minimizes dependencies during shutdown
        self._status_display.stop()
        self._ntrip_client.stop() # Stops trying to send GGA/read RTCM

        # Wait for GNSS reading thread (which might be blocked on readline)
        if self._gnss_read_thread and self._gnss_read_thread.is_alive():
            # No need to join with timeout if it's daemon, just closing the serial port should unblock it eventually
            # self._gnss_read_thread.join(timeout=SERIAL_TIMEOUT + 1.0)
             pass


        # Close serial port (this should help unblock the read thread if it's stuck)
        self._gnss_device.close()

        # Final check if read thread exited
        if self._gnss_read_thread and self._gnss_read_thread.is_alive():
             self._logger.warning("GNSS read thread may not have exited cleanly after port close.")


        self._logger.info("RTK Controller stopped.")

# --- Main Execution ---
if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description='LC29HDA RTK GNSS Client - Spec V1.4)',
        formatter_class=argparse.ArgumentDefaultsHelpFormatter # Show defaults in help
    )
    # Serial Port Arguments
    parser.add_argument('--port', default=DEFAULT_SERIAL_PORT, help='Serial port of GNSS receiver')
    parser.add_argument('--baud', type=int, default=DEFAULT_BAUD_RATE, help='Baud rate for serial connection')

    # NTRIP Arguments
    ntrip_group = parser.add_argument_group('NTRIP Caster Configuration')
    ntrip_group.add_argument('--ntrip-server', default=DEFAULT_NTRIP_SERVER, help='NTRIP caster server address')
    ntrip_group.add_argument('--ntrip-port', type=int, default=DEFAULT_NTRIP_PORT, help='NTRIP caster server port')
    ntrip_group.add_argument('--ntrip-mountpoint', default=DEFAULT_NTRIP_MOUNTPOINT, help='NTRIP caster mountpoint')
    ntrip_group.add_argument('--ntrip-user', default=DEFAULT_NTRIP_USERNAME, help='NTRIP username')
    ntrip_group.add_argument('--ntrip-pass', default=DEFAULT_NTRIP_PASSWORD, help='NTRIP password')

    # Fallback Position Arguments
    pos_group = parser.add_argument_group('Fallback Position (Used for GGA when no fix)')
    pos_group.add_argument('--default-lat', type=float, default=DEFAULT_LAT, help='Default latitude')
    pos_group.add_argument('--default-lon', type=float, default=DEFAULT_LON, help='Default longitude')
    pos_group.add_argument('--default-alt', type=float, default=DEFAULT_ALT, help='Default altitude (meters)')

    # Logging Arguments
    log_group = parser.add_argument_group('Logging Configuration')
    log_group.add_argument('--log-file', default='lc29hda_rtk.log', help='Log file name')
    log_group.add_argument('--debug', action='store_true', help='Enable debug level logging to file and console')

    args = parser.parse_args()

    # --- Setup Logging Handlers ---
    log_level = logging.DEBUG if args.debug else logging.INFO
    log_formatter = logging.Formatter(LOG_FORMAT)

    # Clear existing handlers (if any added by basicConfig)
    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.setLevel(log_level) # Set level on root logger

    # Console Handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(log_formatter)
    console_handler.setLevel(log_level) # Console level matches debug flag
    root_logger.addHandler(console_handler)


    # File Handler
    try:
        file_handler = logging.FileHandler(args.log_file, mode='w') # Overwrite log each run
        file_handler.setFormatter(log_formatter)
        file_handler.setLevel(logging.DEBUG) # Always log DEBUG to file if enabled overall
        root_logger.addHandler(file_handler)
    except Exception as e:
         print(f"Error setting up file logger ({args.log_file}): {e}", file=sys.stderr)
    # ---------------------------


    logger.info(f"Starting application with log level {logging.getLevelName(log_level)}")
    if args.debug: logger.debug("Debug logging is ON.")


    config = Config(args)
    controller = RtkController(config)
    main_thread = threading.current_thread()

    try:
        if controller.start():
            # Keep main thread alive while other threads run.
            # Exit if any of the daemon threads die unexpectedly? Or just rely on Ctrl+C?
            # Relying on Ctrl+C for now.
             while controller._running.is_set(): # Check controller's running flag
                 # Check if threads are alive periodically (optional)
                 # if not controller._gnss_read_thread.is_alive() or \
                 #    not controller._ntrip_client._thread.is_alive() or \
                 #    not controller._status_display._thread.is_alive():
                 #     logger.error("A worker thread has died unexpectedly. Shutting down.")
                 #     break
                 time.sleep(1)

        else:
             logger.critical("Controller failed to start. Check logs.")

    except KeyboardInterrupt:
        logger.info("Ctrl+C received. Shutting down...")
    except Exception as e:
         logger.critical(f"Unhandled exception in main thread: {e}", exc_info=True)
    finally:
        # Ensure stop is called even if start failed or raised exception
        if 'controller' in locals() and isinstance(controller, RtkController):
             controller.stop()
        logger.info("Shutdown complete.")
        # Ensure all handlers are closed
        logging.shutdown()
