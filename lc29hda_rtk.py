# rtk_client_final.py - Refactored version V1.4 spec update
# Uses curses with panels/windows for improved flicker-free status display.
# Sends GGA continuously, even without GPS fix.
# Adheres to SOLID, DRY, Clean Code principles.
# Syntax Fix 4 (Verified Correction in _connect timeout handling)

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
import math # For skyplot calculation if implemented later
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
    RTCM_MSG_TYPE_ARP_1005: "ARP (1005/1006)", # Base station position
}

LOG_FORMAT = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
MAX_LOG_MESSAGES = 10 # Number of messages to keep in UI buffer

# --- Logging Setup ---
logging.basicConfig(level=logging.INFO, format=LOG_FORMAT, filename='rtk_client_final.log', filemode='w') # Changed log filename
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
            state_copy = {}
            for key, value in self.__dict__.items():
                if key == "_lock": continue
                if isinstance(value, (dict, Counter, deque)): state_copy[key] = value.copy()
                elif isinstance(value, list): state_copy[key] = value[:]
                else: state_copy[key] = value
            return state_copy

    def add_ui_log_message(self, message: str):
        """Adds a message to the UI log buffer."""
        with self._lock:
            timestamp = datetime.now().strftime("%H:%M:%S")
            self.ui_log_messages.append(f"[{timestamp}] {message}")

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
            timestamp = datetime.now().strftime("%H:%M:%S")
            self.ui_log_messages.append(f"[{timestamp}] {message}")

    def add_rtcm_type(self, msg_type: int) -> None:
        with self._lock: self.last_rtcm_message_types.append(msg_type)

    def add_ntrip_data_rate(self, bytes_received: int) -> None:
        with self._lock: self.ntrip_data_rates.append(bytes_received)

    def reset_ntrip_reconnects(self) -> None:
        with self._lock: self.ntrip_reconnect_attempts = 0

    def increment_ntrip_reconnects(self) -> None:
        with self._lock: self.ntrip_reconnect_attempts += 1

    def set_ntrip_connected(self, status: bool, message: str = "") -> None:
         with self._lock:
             changed = (self.ntrip_connected != status)
             self.ntrip_connected = status
             if message: self.ntrip_status_message = message
             if status:
                 self.ntrip_last_data_time = datetime.now(timezone.utc)
                 if changed:
                     self.reset_ntrip_reconnects()
                     self.add_ui_log_message("NTRIP Connected.")
             elif changed:
                  self.add_ui_log_message(f"NTRIP Disconnected: {message}")


# --- GNSS Device Communication ---
class GnssDevice:
    """Handles serial communication with the GNSS module."""
    def __init__(self, port: str, baudrate: int, state: GnssState):
        self._port_name = port
        self._baudrate = baudrate
        self._serial_port: Optional[serial.Serial] = None
        self._state = state
        self._logger = logging.getLogger(self.__class__.__name__)

    def connect(self) -> bool:
        """Establishes the serial connection."""
        if self.is_connected(): return True
        try:
            self._serial_port = serial.Serial(self._port_name, self._baudrate, timeout=SERIAL_TIMEOUT)
            self._logger.info(f"Connected to GNSS device on {self._port_name} at {self._baudrate} baud")
            self._state.add_ui_log_message(f"Serial connected: {self._port_name}")
            return True
        except serial.SerialException as e:
            self._logger.error(f"Error opening serial port {self._port_name}: {e}")
            self._state.add_ui_log_message(f"Serial Error: {e}")
            self._serial_port = None
            return False
        except Exception as e:
            self._logger.error(f"Unexpected error connecting to serial port {self._port_name}: {e}")
            self._state.add_ui_log_message(f"Serial Conn. Error: {e}")
            self._serial_port = None
            return False

    def is_connected(self) -> bool:
        """Checks if the serial port is open."""
        return self._serial_port is not None and self._serial_port.is_open

    @staticmethod
    def _calculate_checksum(sentence: str) -> str:
        checksum = 0
        if sentence.startswith('$'): sentence = sentence[1:]
        if '*' in sentence: sentence = sentence.split('*')[0]
        for char in sentence: checksum ^= ord(char)
        return f"{checksum:02X}"

    def send_command(self, command: str) -> Optional[str]:
        """Sends a command to the GNSS module."""
        if not self.is_connected():
            self._logger.error("Cannot send command: Serial port not connected.")
            return None
        if not command.startswith('$'): command = '$' + command
        if '*' in command: command = command.split('*')[0]
        checksum = self._calculate_checksum(command)
        full_command = f"{command}*{checksum}\r\n"
        try:
            start_time = time.monotonic()
            self._serial_port.reset_input_buffer()
            bytes_written = self._serial_port.write(full_command.encode('ascii'))
            self._serial_port.flush()
            self._logger.debug(f"Sent command ({bytes_written} bytes): {full_command.strip()}")
            response_bytes = self._serial_port.readline()
            end_time = time.monotonic()
            self._state.update(last_command_response_time_sec=(end_time - start_time))
            response = response_bytes.decode('ascii', errors='ignore').strip()
            self._logger.debug(f"Received response: {response}")
            return response
        except serial.SerialTimeoutException:
            self._logger.warning(f"Timeout waiting for response to command: {command}")
            return None
        except serial.SerialException as e:
            self._logger.error(f"Serial error sending command '{command}': {e}")
            self._state.increment_error_count("gps")
            self.close(); return None
        except Exception as e:
            self._logger.error(f"Unexpected error sending command '{command}': {e}", exc_info=True)
            self._state.increment_error_count("gps"); return None

    def read_line(self) -> Optional[str]:
        """Reads a line from the serial port."""
        if not self.is_connected(): return None
        try:
            if self._serial_port.in_waiting > 0:
                line_bytes = self._serial_port.readline()
                if not line_bytes: return ""
                return line_bytes.decode('ascii', errors='ignore').strip()
            else: return ""
        except serial.SerialException as e:
            self._logger.error(f"Serial error reading line: {e}")
            self._state.increment_error_count("gps"); self.close(); return None
        except Exception as e:
            self._logger.error(f"Unexpected error reading line: {e}", exc_info=True)
            self._state.increment_error_count("gps"); return None

    def write_data(self, data: bytes) -> Optional[int]:
        """Writes raw bytes to the serial port."""
        if not self.is_connected(): return None
        try: return self._serial_port.write(data)
        except serial.SerialTimeoutException:
             self._logger.warning("Serial write timeout occurred.")
             self._state.increment_error_count("gps"); return 0
        except serial.SerialException as e:
            self._logger.error(f"Serial error writing data: {e}")
            self._state.increment_error_count("gps"); self.close(); return None
        except Exception as e:
             self._logger.error(f"Unexpected error writing data: {e}", exc_info=True)
             self._state.increment_error_count("gps"); return None

    def configure_module(self) -> None:
        """Configures the LC29H(DA) module."""
        self._logger.info("Configuring LC29H (DA) module...")
        self._state.add_ui_log_message("Configuring GNSS module...")
        time.sleep(1)
        version_response = self.send_command("PQTMVERNO")
        if version_response:
            try:
                parts = version_response.split(',')
                if len(parts) > 1 and parts[0] == "$PQTMVERNO":
                    fw = parts[1]; self._state.update(firmware_version=fw)
                    self._logger.info(f"Detected Firmware: {fw}")
                    self._state.add_ui_log_message(f"Firmware: {fw}")
                elif "ERROR" in version_response: self._logger.warning(f"Failed to get firmware version: {version_response}"); self._state.update(firmware_version="Query Error")
                else: self._logger.warning(f"Unexpected firmware response format: {version_response}"); self._state.update(firmware_version="Parse Error")
            except Exception as e: self._logger.warning(f"Could not parse firmware version from '{version_response}': {e}"); self._state.update(firmware_version="Parse Exception")
        else: self._logger.warning("No response received for firmware query."); self._state.update(firmware_version="No Response")

        commands = ["PAIR062,0,1", "PAIR062,4,1", "PAIR062,2,1", "PAIR062,3,1", "PAIR062,5,1", "PAIR436,1", "PAIR513"]
        for cmd in commands: self.send_command(cmd); time.sleep(0.15)
        self._logger.info("Module configuration commands sent.")
        self._state.add_ui_log_message("Module configuration sent.")

    def close(self) -> None:
        """Closes the serial connection."""
        if self._serial_port and self._serial_port.is_open:
            port_name = self._port_name
            try: self._serial_port.close(); self._logger.info("Serial port closed.")
            except Exception as e: self._logger.error(f"Error closing serial port: {e}")
            finally: self._serial_port = None; self._state.add_ui_log_message(f"Serial disconnected: {port_name}")
        self._serial_port = None


# --- NMEA Parsing ---
class NmeaParser:
    """Parses NMEA sentences and updates the shared state."""
    def __init__(self, state: GnssState):
        self._state = state
        self._logger = logging.getLogger(self.__class__.__name__)
        self._current_gsv_sequence_sats = {}
        self._current_gsv_systems = Counter()

    def parse(self, sentence: str) -> None:
        """Parses a single NMEA sentence."""
        if not sentence or not sentence.startswith('$'): return
        try:
            msg = pynmea2.parse(sentence)
            current_epochs = self._state.get_state_snapshot()['epochs_since_start']
            self._state.update(epochs_since_start=current_epochs + 1)
            if isinstance(msg, pynmea2.types.talker.GGA): self._parse_gga(msg)
            elif isinstance(msg, pynmea2.types.talker.GSV): self._parse_gsv(msg)
            elif isinstance(msg, pynmea2.types.talker.GSA): self._parse_gsa(msg)
        except pynmea2.ParseError as e: self._logger.debug(f"Failed to parse NMEA sentence: {sentence} - Error: {e}")
        except Exception as e: self._logger.error(f"Error processing NMEA sentence: {sentence} - Error: {e}", exc_info=True); self._state.increment_error_count("gps")

    def _get_fix_status_string(self, fix_type: int) -> str:
        status_map = {FIX_QUALITY_RTK_FIXED: "RTK Fixed", FIX_QUALITY_RTK_FLOAT: "RTK Float", FIX_QUALITY_DGPS: "DGPS", FIX_QUALITY_GPS: "GPS (SPS)", FIX_QUALITY_ESTIMATED: "Estimated (DR)", FIX_QUALITY_INVALID: "No Fix / Invalid"}
        return status_map.get(fix_type, "Unknown Fix Type")

    def _parse_gga(self, msg: pynmea2.types.talker.GGA) -> None:
        current_state = self._state.get_state_snapshot(); old_fix_type = current_state['fix_type']
        new_fix_type = int(msg.gps_qual) if msg.gps_qual is not None and msg.gps_qual != '' else FIX_QUALITY_INVALID
        now = datetime.now(timezone.utc); updates = {'fix_type': new_fix_type}
        has_valid_coords = False
        if msg.latitude is not None and msg.longitude is not None and new_fix_type > FIX_QUALITY_INVALID:
            updates['position'] = {"lat": msg.latitude, "lon": msg.longitude, "alt": float(msg.altitude) if msg.altitude is not None else current_state.get('position',{}).get('alt', self._state.default_alt)}
            updates['have_position_lock'] = True; updates['last_fix_time'] = now; has_valid_coords = True
        else: updates['have_position_lock'] = False
        updates['num_satellites_used'] = int(msg.num_sats) if msg.num_sats is not None and msg.num_sats != '' else 0
        updates['hdop'] = float(msg.horizontal_dil) if msg.horizontal_dil is not None and msg.horizontal_dil != '' else DEFAULT_HDOP
        if not current_state.get('first_fix_time_sec') and new_fix_type > FIX_QUALITY_INVALID: updates['first_fix_time_sec'] = (now - current_state['start_time']).total_seconds()
        new_rtk_status = self._get_fix_status_string(new_fix_type); updates['rtk_status'] = new_rtk_status
        old_rtk_status = current_state['rtk_status']
        if new_rtk_status != old_rtk_status: self._state.add_ui_log_message(f"Fix status: {new_rtk_status}")
        if new_rtk_status == "RTK Fixed" and old_rtk_status != "RTK Fixed": updates['last_rtk_fix_time'] = now; updates['epochs_since_fix'] = 0
        elif current_state.get('last_rtk_fix_time'): updates['epochs_since_fix'] = current_state.get('epochs_since_fix', 0) + 1
        with self._state._lock: self._state.fix_type_counter[new_rtk_status] += 1
        if old_fix_type != new_fix_type: self._logger.info(f"Fix type changed from {old_fix_type} ({old_rtk_status}) to {new_fix_type} ({new_rtk_status})")
        self._state.update(**updates)

    def _parse_gsv(self, msg: pynmea2.types.talker.GSV) -> None:
        num_sv_in_view = int(msg.num_sv_in_view) if msg.num_sv_in_view is not None and msg.num_sv_in_view != '' else 0
        sentence_num = int(msg.sentence_num) if msg.sentence_num is not None and msg.sentence_num != '' else 0
        num_sentences = int(msg.num_sentences) if msg.num_sentences is not None and msg.num_sentences != '' else 0
        if sentence_num < 1 or num_sentences < 1: return
        is_first_sentence = (sentence_num == 1)
        if is_first_sentence: self._current_gsv_sequence_sats = {}; self._current_gsv_systems = Counter()
        talker = msg.talker; sat_system_map = {'GP': "GPS", 'GL': "GLONASS", 'GA': "Galileo", 'GB': "BeiDou", 'GQ': "QZSS", 'GI': "NavIC"}
        sat_system = sat_system_map.get(talker, "Unknown")
        for i in range(1, 5):
            prn_field = f'sv_prn_num_{i}'; elev_field = f'elevation_{i}'; azim_field = f'azimuth_{i}'; snr_field = f'snr_{i}'
            if hasattr(msg, prn_field) and getattr(msg, prn_field):
                prn = getattr(msg, prn_field); snr_val = getattr(msg, snr_field)
                try: snr = int(snr_val) if snr_val else 0
                except (ValueError, TypeError): snr = 0
                try: elev = int(getattr(msg, elev_field)) if getattr(msg, elev_field) else None
                except (ValueError, TypeError): elev = None
                try: azim = int(getattr(msg, azim_field)) if getattr(msg, azim_field) else None
                except (ValueError, TypeError): azim = None
                sat_key = f"{talker}-{prn}"
                self._current_gsv_sequence_sats[sat_key] = {'prn': prn, 'snr': snr, 'elevation': elev, 'azimuth': azim, 'system': sat_system, 'active': False}
                if snr > 0: self._current_gsv_systems[sat_system] += 1
        is_last_sentence = (sentence_num == num_sentences)
        if is_last_sentence:
            snr_stats = self._calculate_snr_stats(self._current_gsv_sequence_sats)
            updates = {'num_satellites_in_view': num_sv_in_view, 'max_satellites_seen': max(self._state.get_state_snapshot().get('max_satellites_seen', 0), num_sv_in_view), 'satellites_info': self._current_gsv_sequence_sats.copy(), 'satellite_systems': self._current_gsv_systems.copy(), 'snr_stats': snr_stats}
            self._state.update(**updates)
            self._current_gsv_sequence_sats = {}; self._current_gsv_systems = Counter()

    def _calculate_snr_stats(self, satellites_info: Dict[str, Dict[str, Any]]) -> Dict[str, float]:
        snrs = [sat['snr'] for sat in satellites_info.values() if sat.get('snr', 0) > 0]
        stats = {"min": 0.0, "max": 0.0, "avg": 0.0, "good_count": 0.0, "bad_count": 0.0}
        if not snrs: return stats
        stats["min"] = float(min(snrs)); stats["max"] = float(max(snrs)); stats["avg"] = sum(snrs) / len(snrs)
        stats["good_count"] = float(sum(1 for snr in snrs if snr >= 30)); stats["bad_count"] = float(sum(1 for snr in snrs if 1 <= snr < 20))
        return stats

    def _parse_gsa(self, msg: pynmea2.types.talker.GSA) -> None:
        active_sat_keys = set(); talker = msg.talker
        for i in range(1, 13):
             sat_id_field = f'sv_id{i:02}'
             if hasattr(msg, sat_id_field):
                 prn = getattr(msg, sat_id_field)
                 if prn:
                     sat_key = f"{talker}-{prn}"
                     if talker == 'GN':
                          found = False
                          with self._state._lock: current_sats = self._state.satellites_info
                          for key, sat_info in current_sats.items():
                              if sat_info.get('prn') == prn: active_sat_keys.add(key); found = True; break
                          if not found: self._logger.debug(f"GNGSA referenced PRN {prn} not found.")
                     else: active_sat_keys.add(sat_key)
        with self._state._lock:
            for key in list(self._state.satellites_info.keys()):
                 if key in self._state.satellites_info:
                    if key in active_sat_keys: self._state.satellites_info[key]['active'] = True
                    elif talker != 'GN' and key.startswith(talker + '-'): self._state.satellites_info[key]['active'] = False


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
        if self._thread is not None and self._thread.is_alive(): return
        self._running.set()
        self._thread = threading.Thread(target=self._run, name="NtripThread", daemon=True)
        self._thread.start()
        self._logger.info("NTRIP client thread started.")

    def stop(self):
        """Stops the NTRIP client thread."""
        self._running.clear()
        if self._socket:
            socket_to_close = self._socket
            self._socket = None
            try: socket_to_close.shutdown(socket.SHUT_RDWR)
            except OSError: pass
            try: socket_to_close.close(); self._logger.info("NTRIP socket closed.")
            except OSError as e: self._logger.warning(f"Error closing NTRIP socket: {e}")
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
            if self._thread.is_alive(): self._logger.warning("NTRIP thread did not exit cleanly.")
        self._logger.info("NTRIP client stopped.")


    def _connect(self) -> bool:
        """Establishes connection to the NTRIP caster."""
        if self._socket:
            try: self._socket.close();
            except OSError: pass;
            self._socket = None
        connect_msg = f"Connecting to {self._config.ntrip_server}:{self._config.ntrip_port}/{self._config.ntrip_mountpoint}..."
        self._state.set_ntrip_connected(False, "Connecting...")
        self._logger.info(connect_msg)
        try:
            start_connect = time.monotonic(); self._socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._socket.settimeout(NTRIP_TIMEOUT); self._socket.connect((self._config.ntrip_server, self._config.ntrip_port))
            auth_string = f"{self._config.ntrip_username}:{self._config.ntrip_password}"
            auth_b64 = base64.b64encode(auth_string.encode('ascii')).decode('ascii')
            request_lines = [f"GET /{self._config.ntrip_mountpoint} HTTP/1.1", f"Host: {self._config.ntrip_server}:{self._config.ntrip_port}", "Ntrip-Version: Ntrip/1.0", "User-Agent: Python NtripClient/1.1", f"Authorization: Basic {auth_b64}", "Accept: */*", "Connection: close", "\r\n"]
            request = "\r\n".join(request_lines); self._logger.debug(f"Sending NTRIP request:\n{request.strip()}"); self._socket.sendall(request.encode('ascii'))
            response_bytes = bytearray(); self._socket.settimeout(NTRIP_TIMEOUT)
            while b"\r\n\r\n" not in response_bytes:
                 chunk = self._socket.recv(1024)
                 if not chunk: raise ConnectionAbortedError("NTRIP server closed connection during header read")
                 response_bytes.extend(chunk);
                 if len(response_bytes) > 8192: raise OverflowError("NTRIP header too large")
            headers_part, _, body_part = response_bytes.partition(b"\r\n\r\n"); response_str = headers_part.decode('ascii', errors='ignore')
            end_connect = time.monotonic(); self._state.update(last_ntrip_connect_time_sec=(end_connect - start_connect)); self._logger.debug(f"Received NTRIP response headers:\n{response_str}")
            if b"ICY 200 OK" in headers_part or b"HTTP/1.1 200 OK" in headers_part:
                self._logger.info("NTRIP connection successful.")
                self._state.set_ntrip_connected(True, "Connected")
                self._reconnect_timeout = NTRIP_INITIAL_RECONNECT_TIMEOUT; self._send_gga(); self._last_gga_sent_time = datetime.now(timezone.utc)
                if body_part: self._handle_rtcm_data(body_part)
                return True
            else:
                status_line = response_str.splitlines()[0] if '\n' in response_str else response_str; self._logger.error(f"NTRIP connection failed. Status: '{status_line}'")
                self._state.set_ntrip_connected(False, f"Failed: {status_line[:30]}"); self._state.increment_ntrip_reconnects()
                if self._socket: self._socket.close(); self._socket = None
                return False
        except socket.timeout: # ****** THIS BLOCK IS NOW CORRECT ******
            self._logger.error("NTRIP connection timed out.")
            self._state.set_ntrip_connected(False, "Timeout")
            self._state.increment_ntrip_reconnects()
            # CORRECTED Socket cleanup logic:
            if self._socket:
                socket_to_close = self._socket # Use temporary variable
                self._socket = None # Set instance variable to None immediately
                try:
                    socket_to_close.close() # Close using the temp variable
                except OSError:
                    pass # Ignore errors closing socket on timeout
            return False # Indicate connection failure
        except (socket.gaierror, ConnectionRefusedError, ConnectionAbortedError, OverflowError, OSError) as e:
            self._logger.error(f"NTRIP socket connection error: {e}")
            self._state.set_ntrip_connected(False, f"Socket Error: {str(e)[:20]}")
            self._state.increment_ntrip_error_count("ntrip"); self._state.increment_ntrip_reconnects()
            if self._socket:
                try: self._socket.close();
                except OSError: pass
            self._socket = None
            return False
        except Exception as e:
            self._logger.error(f"Unexpected NTRIP connection error: {e}", exc_info=True)
            self._state.set_ntrip_connected(False, f"Error: {str(e)[:20]}")
            self._state.increment_ntrip_error_count("ntrip"); self._state.increment_ntrip_reconnects()
            if self._socket:
                try: self._socket.close();
                except OSError: pass
            self._socket = None
            return False

    def _create_gga_sentence(self) -> str:
        state = self._state.get_state_snapshot(); now = datetime.now(timezone.utc); time_str = now.strftime("%H%M%S.%f")[:9]
        lat, lon, alt = self._config.default_lat, self._config.default_lon, self._config.default_alt
        fix_quality = FIX_QUALITY_GPS; num_sats = 12; hdop = DEFAULT_HDOP
        if state.get('have_position_lock'):
             pos = state.get('position', {}); lat = pos.get('lat', self._config.default_lat); lon = pos.get('lon', self._config.default_lon); alt = pos.get('alt', self._config.default_alt)
             current_fix = state.get('fix_type', FIX_QUALITY_INVALID); fix_quality = current_fix if current_fix > FIX_QUALITY_INVALID else FIX_QUALITY_GPS
             num_sats = state.get('num_satellites_used', 0); hdop = state.get('hdop', DEFAULT_HDOP)
        lat_deg = int(abs(lat)); lat_min = (abs(lat) - lat_deg) * 60; lat_nmea = f"{lat_deg:02d}{lat_min:09.6f}"; lat_dir = "N" if lat >= 0 else "S"
        lon_deg = int(abs(lon)); lon_min = (abs(lon) - lon_deg) * 60; lon_nmea = f"{lon_deg:03d}{lon_min:09.6f}"; lon_dir = "E" if lon >= 0 else "W"
        alt_str = f"{alt:.1f}"; sep_str = "-0.0"
        gga_data = f"GNGGA,{time_str},{lat_nmea},{lat_dir},{lon_nmea},{lon_dir},{fix_quality},{num_sats:02d},{hdop:.2f},{alt_str},M,{sep_str},M,,"
        checksum = GnssDevice._calculate_checksum(gga_data); return f"${gga_data}*{checksum}\r\n"

    def _send_gga(self) -> None:
        if not self._socket or not self._state.ntrip_connected: return
        gga_sentence = self._create_gga_sentence()
        if not gga_sentence: self._logger.error("Failed to create GGA sentence."); return
        try:
            self._socket.sendall(gga_sentence.encode('ascii')); self._logger.debug("Sent GGA to NTRIP server.")
        except (OSError, socket.timeout, BrokenPipeError) as e:
            self._logger.error(f"Error sending GGA to NTRIP: {e}. Disconnecting.")
            self._state.increment_ntrip_error_count("ntrip")
            if self._socket:
                socket_to_close = self._socket; self._socket = None;
                try: socket_to_close.close();
                except OSError: pass
            self._state.set_ntrip_connected(False, "GGA Send Error")
        except Exception as e:
            self._logger.error(f"Unexpected error sending GGA: {e}", exc_info=True)
            self._state.increment_ntrip_error_count("ntrip")
            if self._socket:
                socket_to_close = self._socket; self._socket = None;
                try: socket_to_close.close();
                except OSError: pass
            self._state.set_ntrip_connected(False, "GGA Send Error")


    @staticmethod
    def _extract_rtcm_message_types(data: bytes) -> List[int]:
        types_found = []; i = 0; data_len = len(data)
        while i < data_len - 5:
            if data[i] == 0xD3 and (data[i+1] & 0xC0) == 0:
                try:
                    payload_length = ((data[i+1] & 0x03) << 8) | data[i+2]; total_length = 3 + payload_length + 3
                    if i + total_length <= data_len: message_type = (data[i+3] << 4) | (data[i+4] >> 4); types_found.append(message_type); i += total_length
                    else: break
                except IndexError: break
            else: i += 1
        return types_found

    def _handle_rtcm_data(self, data: bytes) -> None:
        if not data: return
        first_preamble = data.find(0xD3)
        if first_preamble == -1: self._logger.warning(f"Received block without RTCM preamble. Discarding {len(data)} bytes."); return
        elif first_preamble > 0: self._logger.warning(f"Discarding {first_preamble} non-RTCM bytes."); data = data[first_preamble:]
        bytes_sent = self._gnss_device.write_data(data)
        if bytes_sent is not None and bytes_sent > 0:
            now = datetime.now(timezone.utc); rtcm_types = self._extract_rtcm_message_types(data[:bytes_sent])
            with self._state._lock:
                self._state.ntrip_total_bytes += bytes_sent; self._state.ntrip_last_data_time = now; self._state.last_rtcm_data_received = data[:20]
                self._state.rtcm_message_counter += 1; self._state.ntrip_data_rates.append(bytes_sent)
                if rtcm_types: self._state.last_rtcm_message_types.extend(rtcm_types)
            self._logger.debug(f"Sent {bytes_sent} bytes of RTCM data. Types: {rtcm_types if rtcm_types else 'None'}")
        elif bytes_sent is None: self._logger.error("Failed to send RTCM data (serial error).")

    def _run(self) -> None:
        self._logger.info("NTRIP run loop started.")
        while self._running.is_set():
            is_connected = self._state.get_state_snapshot()['ntrip_connected']
            if is_connected and self._socket is not None:
                try:
                    self._socket.settimeout(1.0); rtcm_data = self._socket.recv(2048)
                    if rtcm_data: self._handle_rtcm_data(rtcm_data); self._state.update(ntrip_last_data_time=datetime.now(timezone.utc))
                    else:
                        self._logger.info("NTRIP connection closed by server.")
                        socket_to_close = self._socket; self._socket = None; socket_to_close.close()
                        self._state.set_ntrip_connected(False, "Closed by server")
                        time.sleep(self._reconnect_timeout); continue
                except socket.timeout:
                    now = datetime.now(timezone.utc)
                    if (now - self._last_gga_sent_time).total_seconds() >= NTRIP_GGA_INTERVAL: self._send_gga(); self._last_gga_sent_time = now
                    last_data_time = self._state.get_state_snapshot().get('ntrip_last_data_time')
                    if last_data_time and (now - last_data_time).total_seconds() > NTRIP_DATA_TIMEOUT:
                         self._logger.warning(f"No RTCM data received for {NTRIP_DATA_TIMEOUT}s. Reconnecting.")
                         socket_to_close = self._socket; self._socket = None; socket_to_close.close()
                         self._state.set_ntrip_connected(False, "No data received"); continue
                except (OSError, ConnectionResetError, BrokenPipeError) as e:
                    self._logger.error(f"NTRIP socket error during receive: {e}. Reconnecting.")
                    self._state.increment_ntrip_error_count("ntrip")
                    if self._socket:
                        socket_to_close = self._socket; self._socket = None;
                        try: socket_to_close.close();
                        except OSError: pass
                    self._state.set_ntrip_connected(False, f"Receive Error: {str(e)[:20]}"); time.sleep(self._reconnect_timeout); continue
                except Exception as e:
                    self._logger.error(f"Unexpected error in NTRIP receive loop: {e}", exc_info=True)
                    self._state.increment_ntrip_error_count("ntrip")
                    if self._socket:
                        socket_to_close = self._socket; self._socket = None;
                        try: socket_to_close.close();
                        except OSError: pass
                    self._state.set_ntrip_connected(False, f"Runtime Error: {str(e)[:20]}"); time.sleep(self._reconnect_timeout); continue
            else:
                if self._connect(): self._reconnect_timeout = NTRIP_INITIAL_RECONNECT_TIMEOUT
                else:
                    self._reconnect_timeout = min(self._reconnect_timeout * 1.5, NTRIP_MAX_RECONNECT_TIMEOUT); self._logger.info(f"NTRIP connection failed. Retrying in {self._reconnect_timeout:.1f} seconds.")
                    wait_start = time.monotonic()
                    while self._running.is_set() and (time.monotonic() - wait_start) < self._reconnect_timeout: time.sleep(0.5)
        if self._socket:
            try: self._socket.close();
            except OSError: pass;
            self._socket = None
        self._logger.info("NTRIP run loop finished.")


# --- Status Display (curses based - Enhanced) ---
class StatusDisplay:
    """Formats and prints the system status using curses panels."""
    def __init__(self, state: GnssState, config: Config):
        self._state = state
        self._config = config
        self._logger = logging.getLogger(self.__class__.__name__)
        self._stdscr = None
        self._panels: Dict[str, curses.window] = {}
        self._needs_redraw = True

        self._layout = {"header": {"y": 0, "x": 0, "h": 3, "w": 0},"info": {"y": 3, "x": 0, "h": 0, "w": 0},"sat": {"y": 3, "x": 0, "h": 0, "w": 0},"msg": {"y": 0, "x": 0, "h": 5, "w": 0}}
        self.COLOR_GREEN = curses.A_NORMAL; self.COLOR_YELLOW = curses.A_NORMAL; self.COLOR_RED = curses.A_NORMAL
        self.COLOR_LABEL = curses.A_NORMAL; self.COLOR_VALUE = curses.A_NORMAL; self.COLOR_NORMAL = curses.A_NORMAL
        self.ATTR_BOLD = curses.A_BOLD; self.COLOR_SAT_GPS = curses.A_NORMAL; self.COLOR_SAT_GLO = curses.A_NORMAL
        self.COLOR_SAT_GAL = curses.A_NORMAL; self.COLOR_SAT_BDS = curses.A_NORMAL; self.COLOR_SAT_QZS = curses.A_NORMAL
        self.COLOR_SAT_OTH = curses.A_NORMAL


    def _setup_curses(self, stdscr):
        """Initial curses setup."""
        self._stdscr = stdscr; curses.curs_set(0); stdscr.nodelay(True); stdscr.timeout(int(STATUS_UPDATE_INTERVAL * 500))
        if curses.has_colors():
            curses.start_color(); curses.use_default_colors()
            curses.init_pair(1, curses.COLOR_GREEN, -1); curses.init_pair(2, curses.COLOR_YELLOW, -1); curses.init_pair(3, curses.COLOR_RED, -1)
            curses.init_pair(4, curses.COLOR_CYAN, -1); curses.init_pair(5, curses.COLOR_WHITE, -1); curses.init_pair(6, curses.COLOR_BLUE, -1)
            self.COLOR_GREEN = curses.color_pair(1) | curses.A_BOLD; self.COLOR_YELLOW = curses.color_pair(2) | curses.A_BOLD; self.COLOR_RED = curses.color_pair(3) | curses.A_BOLD
            self.COLOR_LABEL = curses.color_pair(4); self.COLOR_VALUE = curses.color_pair(5); self.COLOR_SAT_GPS = curses.color_pair(1)
            self.COLOR_SAT_GLO = curses.color_pair(2); self.COLOR_SAT_GAL = curses.color_pair(6) | curses.A_BOLD; self.COLOR_SAT_BDS = curses.color_pair(3)
            self.COLOR_SAT_QZS = curses.color_pair(4); self.COLOR_SAT_OTH = curses.A_DIM
        else: self.COLOR_GREEN = curses.A_BOLD; self.COLOR_YELLOW = curses.A_BOLD; self.COLOR_RED = curses.A_BOLD; self.ATTR_BOLD = curses.A_BOLD
        self.COLOR_NORMAL = curses.A_NORMAL

    def _create_windows(self):
        """Create curses windows based on layout."""
        max_y, max_x = self._stdscr.getmaxyx(); self._panels = {}
        min_h, min_w = 20, 80
        if max_y < min_h or max_x < min_w: raise curses.error(f"Terminal too small! Minimum {min_h}x{min_w} required.")
        info_panel_width = max_x // 2; sat_panel_width = max_x - info_panel_width; msg_panel_height = self._layout["msg"]["h"]
        header_height = self._layout["header"]["h"]; main_panel_height = max_y - header_height - msg_panel_height
        self._panels["header"] = self._stdscr.derwin(header_height, max_x, 0, 0)
        self._panels["info"] = self._stdscr.derwin(main_panel_height, info_panel_width, header_height, 0)
        self._panels["sat"] = self._stdscr.derwin(main_panel_height, sat_panel_width, header_height, info_panel_width)
        m_y = max_y - msg_panel_height; self._panels["msg"] = self._stdscr.derwin(msg_panel_height, max_x, m_y, 0)
        self._needs_redraw = True

    def _draw_borders(self):
        """Draw borders and separators."""
        for name in ["info", "sat", "msg"]:
            if name in self._panels: self._panels[name].border()
        if "info" in self._panels and "sat" in self._panels:
            max_y, _ = self._stdscr.getmaxyx(); info_h, info_w = self._panels["info"].getmaxyx()
            sep_x = info_w; start_y = self._layout["header"]["h"]; end_y = start_y + info_h; msg_y = max_y - self._layout["msg"]["h"]
            for y in range(start_y, end_y):
                 if y < max_y:
                    try:
                         if y == start_y: char = curses.ACS_TTEE;
                         elif y == msg_y -1: char = curses.ACS_BTEE;
                         else: char = curses.ACS_VLINE
                         self._stdscr.addch(y, sep_x, char)
                    except curses.error: pass

    def _draw_header(self, win, state):
        """Draw the header panel."""
        if not win: return; win.erase(); _, max_x = win.getmaxyx()
        try:
            win.addstr(0, 0, "=" * max_x); title = f" LC29HDA RTK Status - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} "; win.addstr(1, 0, title.center(max_x), self.ATTR_BOLD); win.addstr(2, 0, "=" * max_x)
        except curses.error: pass
        win.noutrefresh()

    def _draw_info_panel(self, win, state):
        """Draw GNSS and NTRIP info panel."""
        if not win: return; win.erase(); win.border(); max_y, max_x = win.getmaxyx(); y = 1; x = 2; label_width = 22
        def line(label, value, attr=self.COLOR_VALUE):
            nonlocal y;
            if y >= max_y - 1:
                return
            label_str = f"{label:<{label_width}}:"
            try:
                win.addstr(y, x, label_str, self.COLOR_LABEL)
                value_str = str(value); available_width = max_x - x - len(label_str) - 2
                truncated_value = value_str[:available_width]
                win.addstr(y, x + len(label_str) + 1, truncated_value, attr)
            except curses.error: pass
            y += 1
        win.addstr(y, x, "[GNSS Info]", self.ATTR_BOLD); y += 1
        runtime = datetime.now(timezone.utc) - state['start_time']; y = line("Runtime", str(runtime).split('.')[0])
        y = line("Firmware", state['firmware_version']); y = line("Latitude", f"{state['position']['lat']:.8f}\N{DEGREE SIGN}")
        y = line("Longitude", f"{state['position']['lon']:.8f}\N{DEGREE SIGN}"); y = line("Altitude", f"{state['position']['alt']:.3f} m")
        if state['last_fix_time']: fix_age = (datetime.now(timezone.utc) - state['last_fix_time']).total_seconds(); age_attr = self.COLOR_YELLOW if fix_age > 10 else self.COLOR_VALUE; y = line("Fix Age", f"{fix_age:.1f} sec", attr=age_attr)
        else: y = line("Fix Age", "N/A")
        if state.get('first_fix_time_sec') is not None: y = line("TTFF", f"{state['first_fix_time_sec']:.1f} sec")
        else: y = line("TTFF", "Pending...")
        rtk_status = state['rtk_status']; rtk_attr = self.ATTR_BOLD
        if rtk_status == "RTK Fixed": rtk_attr |= self.COLOR_GREEN;
        elif rtk_status == "RTK Float": rtk_attr |= self.COLOR_YELLOW;
        elif rtk_status == "No Fix / Invalid": rtk_attr |= self.COLOR_RED;
        else: rtk_attr |= self.COLOR_VALUE
        y = line("RTK Status", rtk_status, attr=rtk_attr)
        y = line("Fix Type Code", state['fix_type']); y = line("Sats Used / View", f"{state['num_satellites_used']} / {state['num_satellites_in_view']}"); y = line("HDOP", f"{state['hdop']:.2f}")
        if state['satellite_systems']: systems_str = ", ".join(f"{sys}:{c}" for sys, c in sorted(state['satellite_systems'].items())); y = line("Systems View", systems_str)
        else: y = line("Systems View", "N/A")
        y += 1; win.addstr(y, x, "[NTRIP Info]", self.ATTR_BOLD); y += 1
        y = line("Server", f"{self._config.ntrip_server}:{self._config.ntrip_port}"); y = line("Mountpoint", self._config.ntrip_mountpoint)
        ntrip_conn_status = 'Connected' if state['ntrip_connected'] else 'Disconnected'; ntrip_attr = self.COLOR_GREEN if state['ntrip_connected'] else self.COLOR_RED
        y = line("Status", f"{ntrip_conn_status} - {state['ntrip_status_message']}", attr=ntrip_attr)
        if state['ntrip_last_data_time']: rtcm_age = (datetime.now(timezone.utc) - state['ntrip_last_data_time']).total_seconds(); rtcm_age_attr = self.COLOR_RED if rtcm_age > NTRIP_DATA_TIMEOUT else self.COLOR_VALUE; y = line("RTCM Age", f"{rtcm_age:.1f} sec", attr=rtcm_age_attr)
        else: y = line("RTCM Age", "N/A")
        rates_deque = state['ntrip_data_rates']; avg_rate_bps = sum(rates_deque) / len(rates_deque) if rates_deque else 0; y = line("RTCM Rate", f"{avg_rate_bps:.1f} B/s")
        y = line("Total RTCM Bytes", f"{state['ntrip_total_bytes']:,}"); y = line("Reconnects", state['ntrip_reconnect_attempts'])
        rtcm_types_list = list(state['last_rtcm_message_types']); types_str = str(rtcm_types_list[-5:]) + ('...' if len(rtcm_types_list)>5 else '') if rtcm_types_list else 'None'; y = line("Last RTCM Types", types_str)
        win.noutrefresh()

    def _draw_sat_panel(self, win, state):
        """Draw satellite details panel."""
        if not win: return; win.erase(); win.border(); max_y, max_x = win.getmaxyx(); y = 1; x = 2
        win.addstr(y, x, "[Satellites in View]", self.ATTR_BOLD); y += 1
        header = f"{'PRN':>3} {'Sys':<5} {'SNR':>3} {'El':>3} {'Az':>3} {'Use':<3}"; col_widths = [3, 5, 3, 3, 3, 3]; col_spacing = 1
        total_width = sum(col_widths) + col_spacing * (len(col_widths) - 1)
        if max_x > x + total_width + 1 : win.addstr(y, x, header, self.ATTR_BOLD); y += 1; win.addstr(y, x, "-" * total_width); y += 1
        else: win.addstr(y, x, "Too narrow for Sat Table", self.COLOR_YELLOW); y+=1
        sorted_sats = sorted(state.get('satellites_info', {}).items(), key=lambda item: (item[1].get('system', 'zzz'), int(item[1].get('prn', 999))))
        for _, sat_info in sorted_sats:
            if y >= max_y - 1: break
            prn = sat_info.get('prn', '??'); system = sat_info.get('system', 'UNK'); snr = sat_info.get('snr', 0); elev = sat_info.get('elevation', None); azim = sat_info.get('azimuth', None); active = sat_info.get('active', False)
            sys_attr = self.COLOR_NORMAL; sys_short = system[:3].upper()
            if system == "GPS": sys_attr = self.COLOR_SAT_GPS; sys_short="GPS";
            elif system == "GLONASS": sys_attr = self.COLOR_SAT_GLO; sys_short="GLO";
            elif system == "Galileo": sys_attr = self.COLOR_SAT_GAL; sys_short="GAL";
            elif system == "BeiDou": sys_attr = self.COLOR_SAT_BDS; sys_short="BDS";
            elif system == "QZSS": sys_attr = self.COLOR_SAT_QZS; sys_short="QZS";
            elif system == "NavIC": sys_attr = self.COLOR_SAT_OTH; sys_short="NAV"
            snr_attr = self.COLOR_NORMAL | curses.A_DIM
            if snr >= 35: snr_attr = self.COLOR_GREEN;
            elif snr >= 25: snr_attr = self.COLOR_YELLOW;
            elif snr > 0: snr_attr = self.COLOR_RED
            prn_str = f"{prn:>{col_widths[0]}}"; sys_str = f"{sys_short:<{col_widths[1]}}"; snr_str = f"{snr:>{col_widths[2]}}" if snr else f"{'-':>{col_widths[2]}}"; el_str = f"{elev:>{col_widths[3]}}" if elev is not None else f"{'-':>{col_widths[3]}}"; az_str = f"{azim:>{col_widths[4]}}" if azim is not None else f"{'-':>{col_widths[4]}}"; use_str = f"{'[*]':<{col_widths[5]}}" if active else f"{'[ ]':<{col_widths[5]}}"
            current_x = x
            try:
                win.addstr(y, current_x, prn_str); current_x += col_widths[0] + col_spacing; win.addstr(y, current_x, sys_str, sys_attr); current_x += col_widths[1] + col_spacing
                win.addstr(y, current_x, snr_str, snr_attr); current_x += col_widths[2] + col_spacing; win.addstr(y, current_x, el_str); current_x += col_widths[3] + col_spacing
                win.addstr(y, current_x, az_str); current_x += col_widths[4] + col_spacing; win.addstr(y, current_x, use_str, self.ATTR_BOLD if active else self.COLOR_NORMAL); y += 1
            except curses.error: break
        win.noutrefresh()

    def _draw_msg_panel(self, win, state):
        """Draw the message log panel."""
        if not win: return; win.erase(); win.border(); max_y, max_x = win.getmaxyx(); y = 1; x = 2
        win.addstr(0, x, "[Messages]", self.ATTR_BOLD)
        messages = state.get('ui_log_messages', deque()); num_msg_lines = max_y - 2
        start_index = max(0, len(messages) - num_msg_lines); line_num = 0
        for i in range(start_index, len(messages)):
            msg = messages[i]; display_line = y + line_num
            if display_line >= max_y - 1: break
            truncated_msg = msg[:max_x - x - 1]; msg_attr = self.COLOR_NORMAL; lmsg = msg.lower()
            if "error" in lmsg or "failed" in lmsg or "lost" in lmsg or "disconnected" in lmsg: msg_attr = self.COLOR_RED
            elif "connected" in lmsg or "fixed" in lmsg or "sent" in lmsg or "starting" in lmsg: msg_attr = self.COLOR_GREEN
            elif "warning" in lmsg or "reconnecting" in lmsg or "float" in lmsg: msg_attr = self.COLOR_YELLOW
            try: win.addstr(display_line, x, truncated_msg, msg_attr)
            except curses.error: break
            line_num += 1
        win.noutrefresh()


    def update_display(self, stdscr):
        """Main display update called periodically."""
        if self._stdscr is None: self._setup_curses(stdscr)
        state = self._state.get_state_snapshot()
        try:
            if self._needs_redraw: self._stdscr.clear(); self._create_windows(); self._needs_redraw = False
            if "header" in self._panels: self._draw_header(self._panels["header"], state)
            if "info" in self._panels: self._draw_info_panel(self._panels["info"], state)
            if "sat" in self._panels: self._draw_sat_panel(self._panels["sat"], state)
            if "msg" in self._panels: self._draw_msg_panel(self._panels["msg"], state)
            self._draw_borders(); curses.doupdate()
        except curses.error as e: self._logger.error(f"Curses error during display update: {e}. Terminal might be too small."); self.trigger_redraw()

    def trigger_redraw(self):
        """Flags that a full redraw is needed (e.g., after resize)."""
        self._needs_redraw = True

# --- Main Controller ---
class RtkController:
    """Orchestrates the GNSS device, NMEA parser, NTRIP client."""
    def __init__(self, config: Config):
        self._config = config; self._state = GnssState(config.default_lat, config.default_lon, config.default_alt)
        self._gnss_device = GnssDevice(config.serial_port, config.baud_rate, self._state); self._nmea_parser = NmeaParser(self._state)
        self._ntrip_client = NtripClient(config, self._state, self._gnss_device); self._running = threading.Event()
        self._gnss_read_thread: Optional[threading.Thread] = None; self._logger = logger

    def _read_gnss_data_loop(self):
        """Thread loop to continuously read and parse data from GNSS device."""
        self._logger.info("GNSS data reading loop started.")
        while self._running.is_set():
            if not self._gnss_device.is_connected():
                 self._logger.warning("GNSS device disconnected. Attempting reconnect in 5s...")
                 time.sleep(5);
                 if not self._running.is_set(): break
                 if not self._gnss_device.connect(): continue
                 else: self._logger.info("Reconnected to GNSS device.")
            line = self._gnss_device.read_line()
            if line: self._nmea_parser.parse(line)
            elif line is None: self._logger.warning("GNSS read loop detect closed/error state."); time.sleep(2)
            time.sleep(0.005)
        self._logger.info("GNSS data reading loop finished.")

    def start(self) -> bool:
        """Initializes components and starts worker threads."""
        self._logger.info("Starting RTK Controller components..."); self._state.add_ui_log_message("System starting...")
        if not self._gnss_device.connect(): self._logger.critical("Failed to connect to GNSS device on startup."); self._state.add_ui_log_message("FATAL: Cannot connect to GNSS device!"); return False
        self._gnss_device.configure_module(); self._running.set()
        self._gnss_read_thread = threading.Thread(target=self._read_gnss_data_loop, name="GnssReadThread", daemon=True); self._gnss_read_thread.start()
        self._ntrip_client.start(); self._logger.info("Worker threads started."); self._state.add_ui_log_message("System running."); return True

    def stop(self):
        """Stops all components and threads gracefully."""
        if not self._running.is_set(): return
        self._logger.info("Stopping RTK Controller components..."); self._state.add_ui_log_message("System shutting down...")
        self._running.clear(); self._ntrip_client.stop(); self._gnss_device.close(); self._logger.info("RTK Controller components stopped.")

    def get_current_state(self) -> Dict[str, Any]:
        return self._state.get_state_snapshot()

    @property
    def is_running(self) -> bool:
        return self._running.is_set()


# --- Main Execution with Curses ---
def main_curses(stdscr, args: argparse.Namespace):
    """Main function wrapped by curses."""
    controller = None
    try:
        config = Config(args); controller = RtkController(config); status_display = StatusDisplay(controller._state, config)
        status_display._setup_curses(stdscr)
        if not controller.start():
            stdscr.clear(); stdscr.addstr(0, 0, "Error: Failed to start RTK Controller. Check logs. Press any key to exit.", status_display.COLOR_RED)
            stdscr.refresh(); stdscr.nodelay(False); stdscr.getch(); return
        while controller.is_running:
            key = stdscr.getch()
            if key == curses.KEY_RESIZE: status_display.trigger_redraw(); logger.info("Terminal resized."); controller._state.add_ui_log_message("Terminal resized.")
            elif key == ord('q') or key == ord('Q'): logger.info("Quit key pressed. Shutting down."); controller._state.add_ui_log_message("Shutdown initiated by user (q)."); break
            current_state = controller.get_current_state(); status_display.update_display(stdscr)
    except KeyboardInterrupt: logger.info("Ctrl+C received in curses loop. Shutting down."); controller._state.add_ui_log_message("Shutdown initiated by user (Ctrl+C).")
    except curses.error as e: logger.critical(f"Curses error in main loop: {e}", exc_info=True); print(f"FATAL CURSES ERROR: {e}. Check log '{args.log_file}'.", file=sys.stderr)
    except Exception as e: logger.critical(f"Unhandled exception in curses loop: {e}", exc_info=True); print(f"FATAL ERROR: {e}. Check log '{args.log_file}'.", file=sys.stderr)
    finally:
        logger.info("Exiting curses loop, stopping controller...");
        if controller: controller.stop()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='LC29HDA RTK GNSS Client (Refactored - Spec V1.4 - Curses UI V2)', formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument('--port', default=DEFAULT_SERIAL_PORT, help='Serial port of GNSS receiver')
    parser.add_argument('--baud', type=int, default=DEFAULT_BAUD_RATE, help='Baud rate for serial connection')
    ntrip_group = parser.add_argument_group('NTRIP Caster Configuration'); ntrip_group.add_argument('--ntrip-server', default=DEFAULT_NTRIP_SERVER, help='NTRIP caster server address'); ntrip_group.add_argument('--ntrip-port', type=int, default=DEFAULT_NTRIP_PORT, help='NTRIP caster server port'); ntrip_group.add_argument('--ntrip-mountpoint', default=DEFAULT_NTRIP_MOUNTPOINT, help='NTRIP caster mountpoint'); ntrip_group.add_argument('--ntrip-user', default=DEFAULT_NTRIP_USERNAME, help='NTRIP username'); ntrip_group.add_argument('--ntrip-pass', default=DEFAULT_NTRIP_PASSWORD, help='NTRIP password')
    pos_group = parser.add_argument_group('Fallback Position (Used for GGA when no fix)'); pos_group.add_argument('--default-lat', type=float, default=DEFAULT_LAT, help='Default latitude'); pos_group.add_argument('--default-lon', type=float, default=DEFAULT_LON, help='Default longitude'); pos_group.add_argument('--default-alt', type=float, default=DEFAULT_ALT, help='Default altitude (meters)')
    log_group = parser.add_argument_group('Logging Configuration'); log_group.add_argument('--log-file', default='rtk_client_final.log', help='Log file name (curses UI disables console logging)'); log_group.add_argument('--debug', action='store_true', help='Enable debug level logging to file') # Changed default log filename
    args = parser.parse_args()
    log_level = logging.DEBUG if args.debug else logging.INFO; log_formatter = logging.Formatter(LOG_FORMAT)
    try:
        root_logger = logging.getLogger(); root_logger.handlers.clear(); root_logger.setLevel(log_level)
        file_handler = logging.FileHandler(args.log_file, mode='w'); file_handler.setFormatter(log_formatter); file_handler.setLevel(logging.DEBUG) # Log all levels to file
        root_logger.addHandler(file_handler); logger.info(f"File logging setup ({args.log_file}) at level {logging.getLevelName(log_level)}")
        if args.debug: logger.debug("Debug logging is ON.")
    except Exception as e: print(f"Error setting up file logger ({args.log_file}): {e}", file=sys.stderr); sys.exit(1)
    try: curses.wrapper(main_curses, args); print(f"Application finished normally. Log file: {args.log_file}")
    except curses.error as e: print(f"\nCurses initialization failed: {e}", file=sys.stderr); print("Ensure your terminal supports curses (e.g., not basic Windows cmd, use WSL, Linux terminal, macOS terminal) and is large enough (min 80x20 recommended).", file=sys.stderr); sys.exit(1)
    except Exception as e: print(f"\nAn unexpected error occurred: {e}", file=sys.stderr); logger.critical(f"Unhandled exception preventing curses wrapper: {e}", exc_info=True); print(f"Check log file '{args.log_file}' for details.", file=sys.stderr); sys.exit(1)
    finally: logging.shutdown()
