# ntrip_client.py - Handles NTRIP connection and data exchange

import socket
import base64
import threading
import time
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Dict, Any

# Import necessary components from other modules
from rtk_config import Config
from rtk_state import GnssState
from gnss_device import GnssDevice
from rtk_constants import *

logger = logging.getLogger(__name__)

class NtripConnectionState:
    """Class representing NTRIP connection state with proper state transitions."""
    
    # Define state constants
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    GAVE_UP = "gave_up"
    
    def __init__(self):
        self.current_state = self.DISCONNECTED
        self.last_state_change = datetime.now(timezone.utc)
        self.reconnect_attempts = 0
        self.error_message = ""
        self.status_message = "Not connected"
        
    def set_state(self, new_state: str, message: str = "") -> bool:
        """Changes the connection state and records the timestamp.
        Returns True if state actually changed."""
        if new_state == self.current_state:
            # State didn't change, only update message if provided
            if message and message != self.status_message:
                self.status_message = message
                return False  # No actual state change
                
        # Actual state change
        previous_state = self.current_state
        self.current_state = new_state
        self.last_state_change = datetime.now(timezone.utc)
        
        if message:
            self.status_message = message
            
        # Special handling for state transitions
        if new_state == self.CONNECTED:
            # Reset reconnect counter on successful connection
            self.reconnect_attempts = 0
            
        return True  # State changed

    def is_connected(self) -> bool:
        """Returns True if currently connected."""
        return self.current_state == self.CONNECTED
        
    def is_disconnected(self) -> bool:
        """Returns True if disconnected (but not gave up)."""
        return self.current_state == self.DISCONNECTED
        
    def is_connecting(self) -> bool:
        """Returns True if currently attempting connection."""
        return self.current_state == self.CONNECTING
        
    def has_given_up(self) -> bool:
        """Returns True if in gave up state."""
        return self.current_state == self.GAVE_UP
        
    def increment_reconnect_attempts(self) -> int:
        """Increments the reconnect counter and returns new value."""
        self.reconnect_attempts += 1
        return self.reconnect_attempts
        
    def get_connection_age(self) -> float:
        """Returns the time since last state change in seconds."""
        return (datetime.now(timezone.utc) - self.last_state_change).total_seconds()


class NtripClient:
    """Handles connection and data exchange with the NTRIP caster."""
    def __init__(self, config: Config, state: GnssState, gnss_device: GnssDevice):
        self._config = config
        self._state = state
        self._gnss_device = gnss_device
        self._socket: Optional[socket.socket] = None
        self._running = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._last_gga_sent_time = datetime.min.replace(tzinfo=timezone.utc)
        self._reconnect_timeout = NTRIP_INITIAL_RECONNECT_TIMEOUT
        self._connection_state = NtripConnectionState()
        self._next_reconnect_time: Optional[datetime] = None
        
        # Statistics and metrics
        self._stats = {
            'total_bytes_received': 0,
            'last_data_time': None,
            'rtcm_message_counter': 0,
            'data_rates': [],
            'rtcm_message_types': [],
            'last_rtcm_data': None
        }

    def start(self):
        """Starts the NTRIP client thread."""
        if self._thread is not None and self._thread.is_alive():
            logger.warning("NTRIP client thread already running.")
            return
            
        # Clear the gave up state if we're starting/restarting
        if self._connection_state.has_given_up():
            self._connection_state.set_state(NtripConnectionState.DISCONNECTED, "Attempting connection...")
            
        self._running.set()
        self._update_state_from_connection_state()
        self._thread = threading.Thread(target=self._run, name="NtripThread", daemon=True)
        self._thread.start()
        logger.info("NTRIP client thread started.")
        self._state.add_ui_log_message("NTRIP client started.")

    def stop(self):
        """Stops the NTRIP client thread."""
        if not self._running.is_set():
            logger.debug("NTRIP client already stopped.")
            return
            
        self._running.clear()
        self._close_socket()
        
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
            if self._thread.is_alive():
                logger.warning("NTRIP thread did not exit cleanly.")
        
        logger.info("NTRIP client stopped.")
        self._state.add_ui_log_message("NTRIP client stopped.")

    def _update_state_from_connection_state(self):
        """Updates the global state object from the connection state."""
        conn_state = self._connection_state
        
        # Update status message with countdown if waiting to reconnect
        status_message = conn_state.status_message
        if (not conn_state.is_connected() and not conn_state.has_given_up() and 
            self._next_reconnect_time is not None):
            now = datetime.now(timezone.utc)
            if now < self._next_reconnect_time:
                seconds_left = (self._next_reconnect_time - now).total_seconds()
                if seconds_left > 0:
                    reconnect_attempts = conn_state.reconnect_attempts
                    status_message = f"Retry {reconnect_attempts}/{MAX_NTRIP_RETRIES} in {seconds_left:.1f}s"
        
        # Update NTRIP connection status
        self._state.update(
            ntrip_connected=conn_state.is_connected(),
            ntrip_status_message=status_message,
            ntrip_reconnect_attempts=conn_state.reconnect_attempts,
            ntrip_connection_gave_up=conn_state.has_given_up()
        )
        
        # Update statistics
        self._state.update(
            ntrip_total_bytes=self._stats['total_bytes_received'],
            ntrip_last_data_time=self._stats['last_data_time'],
            rtcm_message_counter=self._stats['rtcm_message_counter']
        )
        
        # Update data rate deque
        if self._stats['data_rates']:
            with self._state._lock:
                for rate in self._stats['data_rates']:
                    self._state.ntrip_data_rates.append(rate)
                self._stats['data_rates'] = []  # Clear after updating
                
        # Update RTCM message types
        if self._stats['rtcm_message_types']:
            with self._state._lock:
                for msg_type in self._stats['rtcm_message_types']:
                    self._state.last_rtcm_message_types.append(msg_type)
                self._stats['rtcm_message_types'] = []  # Clear after updating
                
        # Update last RTCM data if available
        if self._stats['last_rtcm_data'] is not None:
            self._state.update(last_rtcm_data_received=self._stats['last_rtcm_data'])
            self._stats['last_rtcm_data'] = None  # Clear after updating

    def _close_socket(self):
        """Safely closes the socket if it exists."""
        if self._socket:
            socket_to_close = self._socket
            self._socket = None
            try:
                socket_to_close.shutdown(socket.SHUT_RDWR)
            except OSError:
                logger.debug("Ignoring OSError during socket shutdown.")
            except Exception as e:
                logger.warning(f"Unexpected error during socket shutdown: {e}")
                
            try:
                socket_to_close.close()
                logger.info("NTRIP socket closed.")
            except OSError as e:
                logger.warning(f"Error closing NTRIP socket: {e}")
            except Exception as e:
                logger.warning(f"Unexpected error closing NTRIP socket: {e}")

    def _connect(self) -> bool:
        """Establishes connection to the NTRIP caster. Returns True on success."""
        # Update connection state to connecting
        self._connection_state.set_state(
            NtripConnectionState.CONNECTING, 
            "Connecting..."
        )
        self._update_state_from_connection_state()
        
        # Close any existing socket
        if self._socket:
            try:
                self._socket.close()
                logger.debug("Closed previous NTRIP socket.")
            except OSError:
                pass
            self._socket = None

        connect_msg = f"Connecting to {self._config.ntrip_server}:{self._config.ntrip_port}/{self._config.ntrip_mountpoint}..."
        logger.info(connect_msg)

        try:
            start_connect = time.monotonic()
            self._socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._socket.settimeout(NTRIP_TIMEOUT)
            self._socket.connect((self._config.ntrip_server, self._config.ntrip_port))

            # Prepare request
            auth_string = f"{self._config.ntrip_username}:{self._config.ntrip_password}"
            auth_b64 = base64.b64encode(auth_string.encode('ascii')).decode('ascii')
            request_lines = [
                f"GET /{self._config.ntrip_mountpoint} HTTP/1.1",
                f"Host: {self._config.ntrip_server}:{self._config.ntrip_port}",
                "Ntrip-Version: Ntrip/1.0",
                "User-Agent: Python NtripClient/1.2",
                f"Authorization: Basic {auth_b64}",
                "Accept: */*",
                "Connection: close",
                "\r\n"
            ]
            request = "\r\n".join(request_lines)
            logger.debug(f"Sending NTRIP request:\n{request.strip()}")
            self._socket.sendall(request.encode('ascii'))

            # Read Response Headers
            response_bytes = bytearray()
            self._socket.settimeout(NTRIP_TIMEOUT)
            while b"\r\n\r\n" not in response_bytes:
                chunk = self._socket.recv(1024)
                if not chunk:
                    raise ConnectionAbortedError("NTRIP server closed connection during header read")
                response_bytes.extend(chunk)
                if len(response_bytes) > 8192:
                    raise OverflowError("NTRIP header too large")

            headers_part, _, body_part = response_bytes.partition(b"\r\n\r\n")
            response_str = headers_part.decode('ascii', errors='ignore')
            end_connect = time.monotonic()
            
            # Update connection time in state
            self._state.update(last_ntrip_connect_time_sec=(end_connect - start_connect))
            logger.debug(f"Received NTRIP response headers:\n{response_str}")

            first_line = response_str.splitlines()[0] if response_str else ""
            if " 200 OK" in first_line:
                # Connection successful
                logger.info("NTRIP connection successful.")
                
                # Update connection state
                self._connection_state.set_state(
                    NtripConnectionState.CONNECTED, 
                    "Connected"
                )
                self._update_state_from_connection_state()
                
                # Reset reconnect timeout
                self._reconnect_timeout = NTRIP_INITIAL_RECONNECT_TIMEOUT
                
                # Send initial GGA
                self._send_gga()
                self._last_gga_sent_time = datetime.now(timezone.utc)
                
                # Process any data that came with the response
                if body_part:
                    self._handle_rtcm_data(body_part)
                    
                return True
            else:
                # Connection failed
                status_line = first_line if first_line else "No Status Line"
                logger.error(f"NTRIP connection failed. Status: '{status_line}'")
                
                # Increment reconnect attempts
                current_attempts = self._connection_state.increment_reconnect_attempts()
                
                # Update connection state
                self._connection_state.set_state(
                    NtripConnectionState.DISCONNECTED, 
                    f"Failed ({current_attempts}): {status_line[:25]}"
                )
                self._update_state_from_connection_state()
                
                # Clean up socket
                if self._socket:
                    self._socket.close()
                self._socket = None
                
                return False

        except socket.timeout:
            logger.error("NTRIP connection timed out.")
            current_attempts = self._connection_state.increment_reconnect_attempts()
            
            self._connection_state.set_state(
                NtripConnectionState.DISCONNECTED, 
                f"Timeout ({current_attempts})"
            )
            self._update_state_from_connection_state()
            
            self._close_socket()
            return False
            
        except (socket.gaierror, ConnectionRefusedError, ConnectionAbortedError, OverflowError, OSError) as e:
            logger.error(f"NTRIP socket connection error: {e}")
            current_attempts = self._connection_state.increment_reconnect_attempts()
            
            self._connection_state.set_state(
                NtripConnectionState.DISCONNECTED, 
                f"Sock Err ({current_attempts}): {str(e)[:20]}"
            )
            self._update_state_from_connection_state()
            
            self._state.increment_error_count("ntrip")
            self._close_socket()
            return False
            
        except Exception as e:
            logger.error(f"Unexpected NTRIP connection error: {e}", exc_info=True)
            current_attempts = self._connection_state.increment_reconnect_attempts()
            
            self._connection_state.set_state(
                NtripConnectionState.DISCONNECTED, 
                f"Error ({current_attempts}): {str(e)[:20]}"
            )
            self._update_state_from_connection_state()
            
            self._state.increment_error_count("ntrip")
            self._close_socket()
            return False

    def _create_gga_sentence(self) -> str:
        """Creates a NMEA GGA sentence for sending position to NTRIP caster."""
        state = self._state.get_state_snapshot()
        now = datetime.now(timezone.utc)
        time_str = now.strftime("%H%M%S.%f")[:9]
        
        lat, lon, alt = self._config.default_lat, self._config.default_lon, self._config.default_alt
        fix_quality = FIX_QUALITY_INVALID
        num_sats = 0
        hdop = DEFAULT_HDOP
        
        if state.get('have_position_lock'):
            pos = state.get('position', {})
            lat = pos.get('lat', self._config.default_lat)
            lon = pos.get('lon', self._config.default_lon)
            alt = pos.get('alt', self._config.default_alt)
            current_fix = state.get('fix_type', FIX_QUALITY_INVALID)
            fix_quality = current_fix
            num_sats = state.get('num_satellites_used', 0)
            hdop = state.get('hdop', DEFAULT_HDOP)
        else:
            fix_quality = FIX_QUALITY_INVALID
            
        lat_deg = int(abs(lat))
        lat_min = (abs(lat) - lat_deg) * 60
        lat_nmea = f"{lat_deg:02d}{lat_min:09.6f}"
        lat_dir = "N" if lat >= 0 else "S"
        
        lon_deg = int(abs(lon))
        lon_min = (abs(lon) - lon_deg) * 60
        lon_nmea = f"{lon_deg:03d}{lon_min:09.6f}"
        lon_dir = "E" if lon >= 0 else "W"
        
        alt_str = f"{alt:.1f}"
        sep_str = "-0.0"
        hdop_str = f"{hdop:.2f}"
        num_sats_str = f"{num_sats:02d}"
        
        gga_data = f"GNGGA,{time_str},{lat_nmea},{lat_dir},{lon_nmea},{lon_dir},{fix_quality},{num_sats_str},{hdop_str},{alt_str},M,{sep_str},M,,"
        checksum = self._calculate_checksum(gga_data)
        return f"${gga_data}*{checksum}\r\n"

    def _calculate_checksum(self, sentence: str) -> str:
        """Calculates the NMEA checksum for a sentence (excluding $ and *)."""
        checksum = 0
        # Strip leading $ if present
        if sentence.startswith('$'):
            sentence = sentence[1:]
        # Remove existing checksum if present
        if '*' in sentence:
            sentence = sentence.split('*')[0]

        # Calculate checksum
        for char in sentence:
            checksum ^= ord(char)
        return f"{checksum:02X}" # Return as 2-digit uppercase hex

    def _send_gga(self) -> None:
        """Sends GGA sentence to NTRIP caster for position updates."""
        if not self._socket or not self._connection_state.is_connected():
            logger.debug("Cannot send GGA: NTRIP not connected.")
            return
            
        gga_sentence = self._create_gga_sentence()
        if not gga_sentence:
            logger.error("Failed to create GGA sentence.")
            return
            
        try:
            self._socket.sendall(gga_sentence.encode('ascii'))
            logger.debug("Sent GGA to NTRIP server.")
        except (OSError, socket.timeout, BrokenPipeError) as e:
            logger.error(f"Error sending GGA to NTRIP: {e}. Disconnecting.")
            self._state.increment_error_count("ntrip")
            self._close_socket()
            
            self._connection_state.set_state(
                NtripConnectionState.DISCONNECTED, 
                "GGA Send Error"
            )
            self._update_state_from_connection_state()
            
        except Exception as e:
            logger.error(f"Unexpected error sending GGA: {e}", exc_info=True)
            self._state.increment_error_count("ntrip")
            self._close_socket()
            
            self._connection_state.set_state(
                NtripConnectionState.DISCONNECTED, 
                "GGA Send Error"
            )
            self._update_state_from_connection_state()

    @staticmethod
    def _extract_rtcm_message_types(data: bytes) -> List[int]:
        """Extracts RTCM message types from received data."""
        types_found = []
        i = 0
        data_len = len(data)
        
        while i < data_len - 5:
            if data[i] == 0xD3 and (data[i+1] & 0xFC) == 0:
                try:
                    payload_length = ((data[i+1] & 0x03) << 8) | data[i+2]
                    total_length = 3 + payload_length + 3
                    if i + total_length <= data_len:
                        message_type = (data[i+3] << 4) | (data[i+4] >> 4)
                        types_found.append(message_type)
                        i += total_length
                    else:
                        logger.debug(f"Incomplete RTCM message at index {i}. Len {total_length}, Buf {data_len-i}.")
                        break
                except IndexError:
                    logger.debug(f"IndexError while parsing RTCM at index {i}.")
                    break
            else:
                i += 1
                
        return types_found

    def _handle_rtcm_data(self, data: bytes) -> None:
        """Processes received RTCM data and forwards it to the GNSS device."""
        if not data:
            return
            
        bytes_sent = self._gnss_device.write_data(data)
        if bytes_sent is not None and bytes_sent > 0:
            now = datetime.now(timezone.utc)
            rtcm_types = self._extract_rtcm_message_types(data[:bytes_sent])
            
            # Update local statistics
            self._stats['total_bytes_received'] += bytes_sent
            self._stats['last_data_time'] = now
            self._stats['last_rtcm_data'] = data[:20]
            self._stats['rtcm_message_counter'] += 1
            self._stats['data_rates'].append(bytes_sent)
            
            if rtcm_types:
                self._stats['rtcm_message_types'].extend(rtcm_types)
                
            # Update state (less frequently to reduce lock contention)
            if len(self._stats['data_rates']) >= 5 or len(self._stats['rtcm_message_types']) >= 5:
                self._update_state_from_connection_state()
                
            logger.debug(f"Sent {bytes_sent} bytes RTCM. Types: {rtcm_types if rtcm_types else 'None'}")
        elif bytes_sent is None:
            logger.error("Failed to send RTCM data to GNSS (serial error).")

    def _check_retry_limit(self) -> bool:
        """Checks if retry limit is reached and updates state accordingly.
        Returns True if limit reached and we should give up."""
        reconnect_attempts = self._connection_state.reconnect_attempts
        
        if reconnect_attempts >= MAX_NTRIP_RETRIES:
            if not self._connection_state.has_given_up():
                logger.warning(f"NTRIP connection failed after {reconnect_attempts} attempts. Giving up.")
                
                self._connection_state.set_state(
                    NtripConnectionState.GAVE_UP, 
                    f"Max retries ({MAX_NTRIP_RETRIES}) reached"
                )
                self._update_state_from_connection_state()
                self._state.add_ui_log_message(f"NTRIP gave up after {reconnect_attempts} attempts.")
                
            return True
        return False

    def _run(self) -> None:
        """Main loop for the NTRIP client thread."""
        logger.info("NTRIP run loop started.")
        last_update_time = datetime.now(timezone.utc)
        update_interval = 1.0  # Update state display every second for countdown
        
        while self._running.is_set():
            # Update state periodically to refresh countdown timer
            now = datetime.now(timezone.utc)
            if (now - last_update_time).total_seconds() >= update_interval:
                self._update_state_from_connection_state()
                last_update_time = now
            
            if self._connection_state.is_connected() and self._socket is not None:
                # Connected State
                try:
                    self._socket.settimeout(1.0)
                    rtcm_data = self._socket.recv(2048)
                    if rtcm_data:
                        self._handle_rtcm_data(rtcm_data)
                    else:
                        logger.info("NTRIP connection closed by server.")
                        self._close_socket()
                        
                        self._connection_state.set_state(
                            NtripConnectionState.DISCONNECTED, 
                            "Closed by server"
                        )
                        self._update_state_from_connection_state()
                        continue
                        
                except socket.timeout:
                    now = datetime.now(timezone.utc)
                    
                    # Send GGA periodically
                    if (now - self._last_gga_sent_time).total_seconds() >= NTRIP_GGA_INTERVAL:
                        self._send_gga()
                        self._last_gga_sent_time = now
                    
                    # Check for data timeout
                    last_data_time = self._stats['last_data_time']
                    if last_data_time and (now - last_data_time).total_seconds() > NTRIP_DATA_TIMEOUT:
                        logger.warning(f"No RTCM data for {NTRIP_DATA_TIMEOUT}s. Reconnecting.")
                        self._close_socket()
                        
                        self._connection_state.set_state(
                            NtripConnectionState.DISCONNECTED, 
                            "No data received"
                        )
                        self._update_state_from_connection_state()
                        continue
                        
                except (OSError, ConnectionResetError, BrokenPipeError) as e:
                    logger.error(f"NTRIP socket error during receive: {e}. Reconnecting.")
                    self._state.increment_error_count("ntrip")
                    self._close_socket()
                    
                    self._connection_state.set_state(
                        NtripConnectionState.DISCONNECTED, 
                        f"Receive Error: {str(e)[:20]}"
                    )
                    self._update_state_from_connection_state()
                    continue
                    
                except Exception as e:
                    logger.error(f"Unexpected error in NTRIP receive loop: {e}", exc_info=True)
                    self._state.increment_error_count("ntrip")
                    self._close_socket()
                    
                    self._connection_state.set_state(
                        NtripConnectionState.DISCONNECTED, 
                        f"Runtime Error: {str(e)[:20]}"
                    )
                    self._update_state_from_connection_state()
                    continue
                    
            elif not self._connection_state.has_given_up():
                # Disconnected State (and haven't given up)
                logger.debug("NTRIP client disconnected. Attempting connection.")
                
                if self._connect():
                    # Successful connection
                    logger.info(f"NTRIP reconnected. Next GGA in {NTRIP_GGA_INTERVAL}s.")
                    self._state.add_ui_log_message("NTRIP connection established.")
                else:
                    # Connection failed, check retry count
                    if self._check_retry_limit():
                        # Gave up, wait longer before potentially trying again
                        self._running.wait(timeout=NTRIP_MAX_RECONNECT_TIMEOUT)
                        if not self._running.is_set():
                            break
                    else:
                        # Haven't reached limit, increase backoff and wait
                        self._reconnect_timeout = min(self._reconnect_timeout * 1.5, NTRIP_MAX_RECONNECT_TIMEOUT)
                        reconnect_attempts = self._connection_state.reconnect_attempts
                        
                        # Set next reconnect time for countdown
                        self._next_reconnect_time = datetime.now(timezone.utc) + \
                                                   timedelta(seconds=self._reconnect_timeout)
                        
                        # Update state with detailed status message including retry info
                        retry_message = f"Retry {reconnect_attempts}/{MAX_NTRIP_RETRIES} in {self._reconnect_timeout:.1f}s"
                        self._connection_state.status_message = retry_message
                        self._update_state_from_connection_state()
                        
                        # Add next_reconnect_time to state for display
                        self._state.update(ntrip_next_reconnect_time=self._next_reconnect_time)
                        
                        # Log the retry information
                        logger.info(f"NTRIP connection failed ({reconnect_attempts}/{MAX_NTRIP_RETRIES}). Retrying in {self._reconnect_timeout:.1f} seconds.")
                        self._state.add_ui_log_message(f"NTRIP: {retry_message}")
                        
                        # Wait before next attempt
                        self._running.wait(timeout=self._reconnect_timeout)
                        if not self._running.is_set():
                            break
            else:
                # Gave Up State - Wait until restart or stop
                logger.debug("NTRIP client has given up trying to connect. Waiting.")
                self._running.wait(timeout=NTRIP_MAX_RECONNECT_TIMEOUT * 2)
                if not self._running.is_set():
                    break

        # Cleanup when loop exits
        self._close_socket()
        logger.info("NTRIP run loop finished.")
        
    def reset_connection(self):
        """Resets the connection state and attempts to reconnect.
        Can be called to recover from a 'gave up' state."""
        if not self._running.is_set():
            logger.info("Cannot reset connection: NTRIP client not running.")
            return False
            
        # Reset connection state
        self._connection_state.set_state(NtripConnectionState.DISCONNECTED, "Reset by user")
        self._connection_state.reconnect_attempts = 0
        self._reconnect_timeout = NTRIP_INITIAL_RECONNECT_TIMEOUT
        self._update_state_from_connection_state()
        
        # Close any existing connection
        self._close_socket()
        
        logger.info("NTRIP connection reset. Will attempt reconnection.")
        self._state.add_ui_log_message("NTRIP connection reset by user.")
        return True
