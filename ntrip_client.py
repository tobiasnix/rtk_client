# ntrip_client.py - Handles NTRIP connection and data exchange

import socket
import base64
import threading
import time
import logging
from datetime import datetime, timezone
from typing import Optional, List

# Import necessary components from other modules
from rtk_config import Config
from rtk_state import GnssState
from gnss_device import GnssDevice # Relies on GnssDevice for GGA creation and RTCM sending
from rtk_constants import * # Import constants

logger = logging.getLogger(__name__)

class NtripClient:
    """Handles connection and data exchange with the NTRIP caster."""
    def __init__(self, config: Config, state: GnssState, gnss_device: GnssDevice):
        self._config = config
        self._state = state
        self._gnss_device = gnss_device # Need this to send RTCM data
        self._socket: Optional[socket.socket] = None
        self._running = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._last_gga_sent_time = datetime.min.replace(tzinfo=timezone.utc)
        self._reconnect_timeout = NTRIP_INITIAL_RECONNECT_TIMEOUT
        # No internal 'gave_up' flag needed, use state directly

    def start(self):
        """Starts the NTRIP client thread."""
        if self._thread is not None and self._thread.is_alive():
            logger.warning("NTRIP client thread already running.")
            return
        self._running.set()
        self._state.set_ntrip_gave_up(False) # Ensure flag is reset on start
        self._thread = threading.Thread(target=self._run, name="NtripThread", daemon=True)
        self._thread.start()
        logger.info("NTRIP client thread started.")

    def stop(self):
        """Stops the NTRIP client thread."""
        self._running.clear()
        # Close socket immediately to interrupt blocking calls
        if self._socket:
            socket_to_close = self._socket
            self._socket = None
            try: socket_to_close.shutdown(socket.SHUT_RDWR)
            except OSError: logger.debug("Ignoring OSError during socket shutdown.")
            except Exception as e: logger.warning(f"Unexpected error during socket shutdown: {e}")
            try: socket_to_close.close(); logger.info("NTRIP socket closed.")
            except OSError as e: logger.warning(f"Error closing NTRIP socket: {e}")
            except Exception as e: logger.warning(f"Unexpected error closing NTRIP socket: {e}")

        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
            if self._thread.is_alive(): logger.warning("NTRIP thread did not exit cleanly.")
        logger.info("NTRIP client stopped.")


    def _connect(self) -> bool:
        """Establishes connection to the NTRIP caster. Returns True on success."""
        if self._socket:
            try: self._socket.close(); logger.debug("Closed previous NTRIP socket.")
            except OSError: pass
            self._socket = None

        connect_msg = f"Connecting to {self._config.ntrip_server}:{self._config.ntrip_port}/{self._config.ntrip_mountpoint}..."
        # Set connecting status, but don't reset reconnect counter here
        self._state.set_ntrip_connected(False, "Connecting...")
        logger.info(connect_msg)

        try:
            start_connect = time.monotonic()
            self._socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._socket.settimeout(NTRIP_TIMEOUT)
            self._socket.connect((self._config.ntrip_server, self._config.ntrip_port))

            # Prepare request
            auth_string = f"{self._config.ntrip_username}:{self._config.ntrip_password}"
            auth_b64 = base64.b64encode(auth_string.encode('ascii')).decode('ascii')
            request_lines = [ f"GET /{self._config.ntrip_mountpoint} HTTP/1.1", f"Host: {self._config.ntrip_server}:{self._config.ntrip_port}", "Ntrip-Version: Ntrip/1.0", "User-Agent: Python NtripClient/1.2", f"Authorization: Basic {auth_b64}", "Accept: */*", "Connection: close", "\r\n" ]
            request = "\r\n".join(request_lines)
            logger.debug(f"Sending NTRIP request:\n{request.strip()}")
            self._socket.sendall(request.encode('ascii'))

            # Read Response Headers
            response_bytes = bytearray()
            self._socket.settimeout(NTRIP_TIMEOUT)
            while b"\r\n\r\n" not in response_bytes:
                 chunk = self._socket.recv(1024)
                 if not chunk: raise ConnectionAbortedError("NTRIP server closed connection during header read")
                 response_bytes.extend(chunk);
                 if len(response_bytes) > 8192: raise OverflowError("NTRIP header too large")

            headers_part, _, body_part = response_bytes.partition(b"\r\n\r\n")
            response_str = headers_part.decode('ascii', errors='ignore')
            end_connect = time.monotonic(); self._state.update(last_ntrip_connect_time_sec=(end_connect - start_connect))
            logger.debug(f"Received NTRIP response headers:\n{response_str}")

            first_line = response_str.splitlines()[0] if response_str else ""
            if " 200 OK" in first_line:
                logger.info("NTRIP connection successful.")
                # Set connected state (this resets reconnect counter and gave_up flag in state)
                self._state.set_ntrip_connected(True, "Connected")
                # --- Reset internal backoff on success ---
                self._reconnect_timeout = NTRIP_INITIAL_RECONNECT_TIMEOUT
                # --- End Reset ---
                self._send_gga() # Send initial GGA
                self._last_gga_sent_time = datetime.now(timezone.utc)
                if body_part: self._handle_rtcm_data(body_part)
                return True # Indicate success
            else:
                status_line = first_line if first_line else "No Status Line"; logger.error(f"NTRIP connection failed. Status: '{status_line}'")
                # Increment counter FIRST, then set status
                current_attempts = self._state.increment_ntrip_reconnects()
                self._state.set_ntrip_connected(False, f"Failed ({current_attempts}): {status_line[:25]}")
                if self._socket: self._socket.close()
                self._socket = None
                return False # Indicate failure

        except socket.timeout:
            logger.error("NTRIP connection timed out.")
            current_attempts = self._state.increment_ntrip_reconnects()
            self._state.set_ntrip_connected(False, f"Timeout ({current_attempts})", log_to_ui=False)
            if self._socket: 
                socket_to_close = self._socket; self._socket = None; 
                try: socket_to_close.close(); 
                except OSError: pass
            return False
        except (socket.gaierror, ConnectionRefusedError, ConnectionAbortedError, OverflowError, OSError) as e:
            logger.error(f"NTRIP socket connection error: {e}")
            current_attempts = self._state.increment_ntrip_reconnects()
            self._state.set_ntrip_connected(False, f"Sock Err ({current_attempts}): {str(e)[:20]}", log_to_ui=False)
            self._state.increment_error_count("ntrip")
            if self._socket: 
                try: self._socket.close(); 
                except OSError: pass
            self._socket = None
            return False
        except Exception as e:
            logger.error(f"Unexpected NTRIP connection error: {e}", exc_info=True)
            current_attempts = self._state.increment_ntrip_reconnects()
            self._state.set_ntrip_connected(False, f"Error ({current_attempts}): {str(e)[:20]}", log_to_ui=False)
            self._state.increment_error_count("ntrip")
            if self._socket: 
                try: self._socket.close(); 
                except OSError: pass
            self._socket = None
            return False

    def _create_gga_sentence(self) -> str:
        # (No changes needed in this method)
        state = self._state.get_state_snapshot(); now = datetime.now(timezone.utc); time_str = now.strftime("%H%M%S.%f")[:9]
        lat, lon, alt = self._config.default_lat, self._config.default_lon, self._config.default_alt
        fix_quality = FIX_QUALITY_INVALID; num_sats = 0; hdop = DEFAULT_HDOP
        if state.get('have_position_lock'):
             pos = state.get('position', {}); lat = pos.get('lat', self._config.default_lat); lon = pos.get('lon', self._config.default_lon); alt = pos.get('alt', self._config.default_alt)
             current_fix = state.get('fix_type', FIX_QUALITY_INVALID); fix_quality = current_fix
             num_sats = state.get('num_satellites_used', 0); hdop = state.get('hdop', DEFAULT_HDOP)
        else: fix_quality = FIX_QUALITY_INVALID
        lat_deg = int(abs(lat)); lat_min = (abs(lat) - lat_deg) * 60; lat_nmea = f"{lat_deg:02d}{lat_min:09.6f}"; lat_dir = "N" if lat >= 0 else "S"
        lon_deg = int(abs(lon)); lon_min = (abs(lon) - lon_deg) * 60; lon_nmea = f"{lon_deg:03d}{lon_min:09.6f}"; lon_dir = "E" if lon >= 0 else "W"
        alt_str = f"{alt:.1f}"; sep_str = "-0.0"; hdop_str = f"{hdop:.2f}"; num_sats_str = f"{num_sats:02d}"
        gga_data = f"GNGGA,{time_str},{lat_nmea},{lat_dir},{lon_nmea},{lon_dir},{fix_quality},{num_sats_str},{hdop_str},{alt_str},M,{sep_str},M,,"
        checksum = GnssDevice._calculate_checksum(gga_data); return f"${gga_data}*{checksum}\r\n"


    def _send_gga(self) -> None:
        # (No changes needed, error count call was already correct)
        if not self._socket or not self._state.ntrip_connected: logger.debug("Cannot send GGA: NTRIP not connected."); return
        gga_sentence = self._create_gga_sentence()
        if not gga_sentence: logger.error("Failed to create GGA sentence."); return
        try:
            self._socket.sendall(gga_sentence.encode('ascii')); logger.debug("Sent GGA to NTRIP server.")
        except (OSError, socket.timeout, BrokenPipeError) as e:
            logger.error(f"Error sending GGA to NTRIP: {e}. Disconnecting.")
            self._state.increment_error_count("ntrip")
            if self._socket: 
                socket_to_close = self._socket; self._socket = None; 
                try: socket_to_close.close(); 
                except OSError: pass
            self._state.set_ntrip_connected(False, "GGA Send Error")
        except Exception as e:
            logger.error(f"Unexpected error sending GGA: {e}", exc_info=True)
            self._state.increment_error_count("ntrip")
            if self._socket: 
                socket_to_close = self._socket; self._socket = None; 
                try: socket_to_close.close(); 
                except OSError: pass
            self._state.set_ntrip_connected(False, "GGA Send Error")


    @staticmethod
    def _extract_rtcm_message_types(data: bytes) -> List[int]:
        # (No changes needed in this method)
        types_found = []; i = 0; data_len = len(data)
        while i < data_len - 5:
            if data[i] == 0xD3 and (data[i+1] & 0xFC) == 0:
                try:
                    payload_length = ((data[i+1] & 0x03) << 8) | data[i+2]; total_length = 3 + payload_length + 3
                    if i + total_length <= data_len: message_type = (data[i+3] << 4) | (data[i+4] >> 4); types_found.append(message_type); i += total_length
                    else: logger.debug(f"Incomplete RTCM message at index {i}. Len {total_length}, Buf {data_len-i}."); break
                except IndexError: logger.debug(f"IndexError while parsing RTCM at index {i}."); break
            else: i += 1
        return types_found

    def _handle_rtcm_data(self, data: bytes) -> None:
        # (No changes needed in this method)
        if not data: return
        bytes_sent = self._gnss_device.write_data(data)
        if bytes_sent is not None and bytes_sent > 0:
            now = datetime.now(timezone.utc); rtcm_types = self._extract_rtcm_message_types(data[:bytes_sent])
            with self._state._lock:
                self._state.ntrip_total_bytes += bytes_sent; self._state.ntrip_last_data_time = now; self._state.last_rtcm_data_received = data[:20]
                self._state.rtcm_message_counter += 1; self._state.ntrip_data_rates.append(bytes_sent)
                if rtcm_types: self._state.last_rtcm_message_types.extend(rtcm_types)
            logger.debug(f"Sent {bytes_sent} bytes RTCM. Types: {rtcm_types if rtcm_types else 'None'}")
        elif bytes_sent is None: logger.error("Failed to send RTCM data to GNSS (serial error).")

    def _run(self) -> None:
        """Main loop for the NTRIP client thread."""
        logger.info("NTRIP run loop started.")
        while self._running.is_set():
            current_state = self._state.get_state_snapshot()
            is_connected = current_state.get('ntrip_connected', False)
            gave_up = current_state.get('ntrip_connection_gave_up', False)

            if is_connected and self._socket is not None:
                # --- Connected State ---
                try:
                    self._socket.settimeout(1.0); rtcm_data = self._socket.recv(2048)
                    if rtcm_data: self._handle_rtcm_data(rtcm_data)
                    else:
                        logger.info("NTRIP connection closed by server.")
                        socket_to_close = self._socket; self._socket = None; socket_to_close.close()
                        self._state.set_ntrip_connected(False, "Closed by server"); continue
                except socket.timeout:
                    now = datetime.now(timezone.utc)
                    if (now - self._last_gga_sent_time).total_seconds() >= NTRIP_GGA_INTERVAL: self._send_gga(); self._last_gga_sent_time = now
                    last_data_time = current_state.get('ntrip_last_data_time')
                    if last_data_time and (now - last_data_time).total_seconds() > NTRIP_DATA_TIMEOUT:
                         logger.warning(f"No RTCM data for {NTRIP_DATA_TIMEOUT}s. Reconnecting.")
                         socket_to_close = self._socket; self._socket = None; 
                         try: socket_to_close.close(); 
                         except OSError: pass
                         self._state.set_ntrip_connected(False, "No data received"); continue
                except (OSError, ConnectionResetError, BrokenPipeError) as e:
                    logger.error(f"NTRIP socket error during receive: {e}. Reconnecting.")
                    self._state.increment_error_count("ntrip")
                    if self._socket: 
                        socket_to_close = self._socket; 
                        self._socket = None; 
                        try: socket_to_close.close(); 
                        except OSError: pass
                    self._state.set_ntrip_connected(False, f"Receive Error: {str(e)[:20]}"); continue
                except Exception as e:
                    logger.error(f"Unexpected error in NTRIP receive loop: {e}", exc_info=True)
                    self._state.increment_error_count("ntrip")
                    if self._socket: 
                        socket_to_close = self._socket; 
                        self._socket = None; 
                        try: socket_to_close.close(); 
                        except OSError: pass
                    self._state.set_ntrip_connected(False, f"Runtime Error: {str(e)[:20]}"); continue
            elif not gave_up:
                # --- Disconnected State (and haven't given up) ---
                logger.debug("NTRIP client disconnected. Attempting connection.")
                if self._connect():
                     # Successful connection resets counter and gave_up flag via set_ntrip_connected(True,...)
                     logger.info(f"NTRIP reconnected. Next GGA in {NTRIP_GGA_INTERVAL}s.")
                else:
                    # Connection failed, check retry count
                    # Re-fetch state in case it changed during _connect()
                    reconnect_attempts = self._state.get_state_snapshot().get('ntrip_reconnect_attempts', 0)
                    if reconnect_attempts >= MAX_NTRIP_RETRIES:
                        logger.warning(f"NTRIP connection failed after {reconnect_attempts} attempts. Giving up.")
                        # Set gave up state - this also updates status message
                        self._state.set_ntrip_gave_up(True, f"Max retries ({MAX_NTRIP_RETRIES}) reached")
                        # Stay in loop but don't attempt connect, wait for external stop/restart
                        self._running.wait(timeout=NTRIP_MAX_RECONNECT_TIMEOUT) # Wait longer when giving up
                    else:
                        # Haven't reached limit, increase backoff and wait
                        self._reconnect_timeout = min(self._reconnect_timeout * 1.5, NTRIP_MAX_RECONNECT_TIMEOUT)
                        logger.info(f"NTRIP connection failed ({reconnect_attempts}/{MAX_NTRIP_RETRIES}). Retrying in {self._reconnect_timeout:.1f} seconds.")
                        self._running.wait(timeout=self._reconnect_timeout)
                        if not self._running.is_set(): break # Exit loop if stopped during wait
            else:
                # --- Gave Up State ---
                logger.debug("NTRIP client has given up trying to connect. Waiting.")
                # Just wait indefinitely (or with long timeout) until main loop stops
                self._running.wait(timeout=NTRIP_MAX_RECONNECT_TIMEOUT * 2) # Wait even longer

        # --- Cleanup after loop exits ---
        if self._socket: 
            try: self._socket.close(); 
            except OSError: pass; 
            self._socket = None
        logger.info("NTRIP run loop finished.")
