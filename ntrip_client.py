# ntrip_client.py - Handles NTRIP connection and data exchange

import base64
import logging
import random
import socket
import ssl
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from gnss_device import GnssDevice

# Import necessary components from other modules
from rtk_config import Config
from rtk_constants import *
from rtk_state import GnssState

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
        state_changed = (new_state != self.current_state)
        # Always update timestamp and message if provided or state changed
        if state_changed or (message and message != self.status_message):
            self.current_state = new_state
            self.last_state_change = datetime.now(timezone.utc)
            if message: self.status_message = message

            # Reset reconnect counter on successful connection or explicit disconnect
            if new_state == self.CONNECTED or (state_changed and new_state == self.DISCONNECTED):
                self.reconnect_attempts = 0

            return state_changed
        return False # No state change and message didn't change

    def is_connected(self) -> bool:
        return self.current_state == self.CONNECTED

    def is_disconnected(self) -> bool:
        # Includes gave up state for simplicity in some checks
        return self.current_state in [self.DISCONNECTED, self.GAVE_UP]

    def is_connecting(self) -> bool:
        return self.current_state == self.CONNECTING

    def has_given_up(self) -> bool:
        return self.current_state == self.GAVE_UP

    def increment_reconnect_attempts(self) -> int:
        self.reconnect_attempts += 1
        return self.reconnect_attempts

    def get_connection_age(self) -> float:
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
        self._stats = {
            'total_bytes_received': 0,
            'last_data_time': None,
            'rtcm_message_counter': 0,
            'data_rates': [],
            'rtcm_message_types': [],
            'last_rtcm_data': None
        }

    def is_running(self) -> bool:
        """Returns True if the NTRIP client thread is alive."""
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.is_running():
            logger.warning("NTRIP client thread already running.")
            return

        if self._connection_state.has_given_up():
            self._connection_state.set_state(NtripConnectionState.DISCONNECTED, "Restarting connection attempt")

        self._running.set()
        self._update_state_from_connection_state() # Initial state update
        self._thread = threading.Thread(target=self._run, name="NtripThread", daemon=True)
        self._thread.start()
        logger.info("NTRIP client thread started.")
        self._log_ui_message("NTRIP client starting...")

    def stop(self) -> None:
        if not self._running.is_set():
            logger.debug("NTRIP client already stopped.")
            return

        logger.info("Stopping NTRIP client...")
        self._running.clear() # Signal thread to stop

        # Close the socket *before* joining to potentially unblock the thread
        self._close_socket()

        if self._thread and self._thread.is_alive():
            # Give the thread a reasonable time to exit based on socket timeout
            join_timeout = NTRIP_TIMEOUT + 1.0 # Add a buffer
            self._thread.join(timeout=join_timeout)
            if self._thread.is_alive():
                 # Log the problematic state if join fails
                 logger.warning(f"NTRIP thread did not exit cleanly after {join_timeout}s. Current state: {self._connection_state.current_state}")
                 self._log_ui_message("NTRIP: Thread Shutdown Issue")
            else:
                logger.info("NTRIP client thread stopped cleanly.")
        else:
             logger.debug("NTRIP client thread was not running or already stopped.")

        # Final state update after stopping
        self._connection_state.set_state(NtripConnectionState.DISCONNECTED, "Stopped by user")
        self._update_state_from_connection_state()
        logger.info("NTRIP client stopped.")
        # UI message handled by state update or RtkController

    def _update_state_from_connection_state(self):
        """Updates the global state object from the connection state."""
        conn_state = self._connection_state
        status_message = conn_state.status_message
        now = datetime.now(timezone.utc)

        # Refine status message for UI display during reconnection attempts
        if conn_state.current_state == NtripConnectionState.DISCONNECTED and self._next_reconnect_time and not conn_state.has_given_up():
            if now < self._next_reconnect_time:
                seconds_left = (self._next_reconnect_time - now).total_seconds()
                if seconds_left > 0:
                    status_message = f"Retry {conn_state.reconnect_attempts}/{MAX_NTRIP_RETRIES} in {seconds_left:.1f}s"
            else:
                 # If past reconnect time, should be connecting or failed again
                 status_message = f"Retry {conn_state.reconnect_attempts}/{MAX_NTRIP_RETRIES} overdue..."

        # Update core NTRIP state
        self._state.update(
            ntrip_connected=conn_state.is_connected(),
            ntrip_status_message=status_message,
            ntrip_reconnect_attempts=conn_state.reconnect_attempts,
            ntrip_connection_gave_up=conn_state.has_given_up(),
            ntrip_next_reconnect_time=self._next_reconnect_time # Ensure this is updated
        )

        # Update statistics
        self._state.update(
            ntrip_total_bytes=self._stats['total_bytes_received'],
            ntrip_last_data_time=self._stats['last_data_time'],
            rtcm_message_counter=self._stats['rtcm_message_counter']
        )

        # Update deques (lock needed as display thread might read them)
        with self._state._lock:
            if self._stats['data_rates']:
                for rate in self._stats['data_rates']:
                    self._state.ntrip_data_rates.append(rate)
                self._stats['data_rates'] = []

            if self._stats['rtcm_message_types']:
                for msg_type in self._stats['rtcm_message_types']:
                    self._state.last_rtcm_message_types.append(msg_type)
                self._stats['rtcm_message_types'] = []

        if self._stats['last_rtcm_data'] is not None:
            self._state.update(last_rtcm_data_received=self._stats['last_rtcm_data'])
            self._stats['last_rtcm_data'] = None

    def _close_socket(self):
        """Safely closes the socket if it exists and is valid."""
        # Use a temporary variable to avoid race conditions if called concurrently (though unlikely)
        socket_to_close = self._socket
        if socket_to_close:
            self._socket = None # Prevent reuse immediately
            try:
                # Shutdown is preferred to signal the other end
                socket_to_close.shutdown(socket.SHUT_RDWR)
                logger.debug("NTRIP socket shutdown completed.")
            except OSError as e:
                # Ignore specific errors often seen on already closed sockets
                # errno 107: Transport endpoint is not connected
                # errno 9: Bad file descriptor (already closed)
                if e.errno not in [107, 9]:
                    logger.warning(f"Ignoring OSError during socket shutdown: {e}")
                else:
                    logger.debug(f"Ignoring known OSError during shutdown (socket likely already closed): {e.errno}")
            except Exception as e:
                logger.warning(f"Unexpected error during socket shutdown: {e}", exc_info=True)

            try:
                socket_to_close.close()
                logger.info("NTRIP socket closed.")
            except OSError as e:
                # errno 9: Bad file descriptor
                if e.errno != 9:
                    logger.warning(f"Error closing NTRIP socket: {e}")
                else:
                    logger.debug("Ignoring OSError 9 closing socket (already closed).")
            except Exception as e:
                 logger.warning(f"Unexpected error closing NTRIP socket: {e}", exc_info=True)


    def _connect(self) -> bool:
        """Establishes connection to the NTRIP caster. Returns True on success."""
        # Check running flag early
        if not self._running.is_set():
            logger.debug("NTRIP connect aborted: Shutdown requested.")
            return False

        self._connection_state.set_state(NtripConnectionState.CONNECTING, "Connecting...")
        self._update_state_from_connection_state()
        self._close_socket() # Ensure any old socket is closed before creating new

        connect_msg = f"Connecting to {self._config.ntrip_server}:{self._config.ntrip_port}/{self._config.ntrip_mountpoint}..."
        logger.info(connect_msg)
        # No UI log here, state update handles it

        try:
            start_connect = time.monotonic()
            # Create and configure the socket
            self._socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._socket.settimeout(NTRIP_TIMEOUT)
            if self._config.ntrip_tls:
                context = ssl.create_default_context()
                self._socket = context.wrap_socket(
                    self._socket, server_hostname=self._config.ntrip_server
                )
            self._socket.connect((self._config.ntrip_server, self._config.ntrip_port))

            # Prepare and send request
            auth_string = f"{self._config.ntrip_username}:{self._config.ntrip_password}"
            auth_b64 = base64.b64encode(auth_string.encode('ascii')).decode('ascii')
            request_lines = [
                f"GET /{self._config.ntrip_mountpoint} HTTP/1.1",
                f"Host: {self._config.ntrip_server}:{self._config.ntrip_port}",
                "Ntrip-Version: Ntrip/2.0", # Use Ntrip/2.0 for better compatibility
                "User-Agent: Python NtripClient/FastDev",
                f"Authorization: Basic {auth_b64}",
                "Accept: */*",
                "Connection: close", # Keep close for simple request/response
                "\r\n"
            ]
            request = "\r\n".join(request_lines)
            logger.debug(f"Sending NTRIP request:\n{request.strip()}")
            self._socket.sendall(request.encode('ascii'))

            # Read Response Headers robustly
            response_bytes = bytearray()
            self._socket.settimeout(NTRIP_TIMEOUT) # Ensure timeout is set for recv
            while b"\r\n\r\n" not in response_bytes:
                # Check running flag before blocking recv
                if not self._running.is_set():
                    logger.info("NTRIP header read aborted: Shutdown requested.")
                    self._close_socket()
                    return False

                chunk = self._socket.recv(1024)
                if not chunk:
                    raise ConnectionAbortedError("NTRIP server closed connection during header read")
                response_bytes.extend(chunk)
                if len(response_bytes) > NTRIP_HEADER_SIZE_LIMIT:
                    raise OverflowError("NTRIP header too large")

            headers_part, _, body_part = response_bytes.partition(b"\r\n\r\n")
            response_str = headers_part.decode('ascii', errors='ignore')
            end_connect = time.monotonic()
            self._state.update(last_ntrip_connect_time_sec=(end_connect - start_connect))
            logger.debug(f"Received NTRIP response headers:\n{response_str}")

            first_line = response_str.splitlines()[0].strip() if response_str else ""
            if " 200 OK" in first_line or first_line.startswith("ICY 200 OK"): # Handle ICY variant
                logger.info("NTRIP connection successful.")
                self._connection_state.set_state(NtripConnectionState.CONNECTED, "Connected")
                # UI message handled by state update
                self._reconnect_timeout = NTRIP_INITIAL_RECONNECT_TIMEOUT # Reset backoff

                # Send initial GGA if needed (caster dependent)
                self._send_gga()
                self._last_gga_sent_time = datetime.now(timezone.utc)

                # Process any data that came with the response
                if body_part:
                    self._handle_rtcm_data(body_part)

                return True
            else:
                status_line = first_line if first_line else "No Status Line"
                logger.error(f"NTRIP connection failed. Status: '{status_line}'")
                current_attempts = self._connection_state.increment_reconnect_attempts()
                self._connection_state.set_state(NtripConnectionState.DISCONNECTED, f"Failed ({current_attempts}): {status_line[:30]}")
                self._close_socket() # Clean up failed connection
                return False

        except socket.timeout:
            logger.error("NTRIP connection timed out.")
            current_attempts = self._connection_state.increment_reconnect_attempts()
            self._connection_state.set_state(NtripConnectionState.DISCONNECTED, f"Timeout ({current_attempts})")
            self._close_socket()
            return False
        except (socket.gaierror, ConnectionRefusedError, ConnectionAbortedError, OverflowError, OSError) as e:
            logger.error(f"NTRIP socket connection error: {e}")
            current_attempts = self._connection_state.increment_reconnect_attempts()
            self._connection_state.set_state(NtripConnectionState.DISCONNECTED, f"Sock Err ({current_attempts}): {str(e)[:25]}")
            self._state.increment_error_count("ntrip")
            self._close_socket()
            return False
        except Exception as e:
            logger.error(f"Unexpected NTRIP connection error: {e}", exc_info=True)
            current_attempts = self._connection_state.increment_reconnect_attempts()
            self._connection_state.set_state(NtripConnectionState.DISCONNECTED, f"Error ({current_attempts}): {str(e)[:25]}")
            self._state.increment_error_count("ntrip")
            self._close_socket()
            return False
        finally:
             # Always update state after connection attempt
             self._update_state_from_connection_state()

    def _create_gga_sentence(self) -> Optional[str]:
        """Creates a NMEA GGA sentence. Returns None if state is unavailable."""
        try:
            state = self._state.get_state_snapshot()
            now = datetime.now(timezone.utc)
            time_str = now.strftime("%H%M%S.%f")[:9] # Format HHMMSS.ss

            lat, lon, alt = self._config.default_lat, self._config.default_lon, self._config.default_alt
            fix_quality = FIX_QUALITY_INVALID
            num_sats = 0
            hdop = DEFAULT_HDOP

            if state.get('have_position_lock') and state.get('fix_type', FIX_QUALITY_INVALID) > FIX_QUALITY_INVALID:
                pos = state.get('position', {})
                # Use defaults from config if state is missing them somehow
                lat = pos.get('lat', self._config.default_lat)
                lon = pos.get('lon', self._config.default_lon)
                alt = pos.get('alt', self._config.default_alt)
                fix_quality = state.get('fix_type', FIX_QUALITY_INVALID)
                num_sats = state.get('num_satellites_used', 0)
                hdop = state.get('hdop', DEFAULT_HDOP)

            # Format latitude
            lat_deg = int(abs(lat))
            lat_min = (abs(lat) - lat_deg) * 60
            lat_nmea = f"{lat_deg:02d}{lat_min:09.6f}" # ddmmmmmm.mmmmmm
            lat_dir = "N" if lat >= 0 else "S"

            # Format longitude
            lon_deg = int(abs(lon))
            lon_min = (abs(lon) - lon_deg) * 60
            lon_nmea = f"{lon_deg:03d}{lon_min:09.6f}" # dddmmmmmm.mmmmmm
            lon_dir = "E" if lon >= 0 else "W"

            # Format other fields
            alt_str = f"{alt:.1f}"
            sep_str = "-0.0" # Geoid separation - usually unknown, use dummy value
            hdop_str = f"{hdop:.2f}"
            num_sats_str = f"{num_sats:02d}" # Ensure 2 digits

            # Assemble sentence data
            gga_data = f"GNGGA,{time_str},{lat_nmea},{lat_dir},{lon_nmea},{lon_dir},{fix_quality},{num_sats_str},{hdop_str},{alt_str},M,{sep_str},M,,"
            checksum = self._calculate_checksum(gga_data)
            return f"${gga_data}*{checksum}\r\n"
        except Exception as e:
             logger.error(f"Failed to create GGA sentence: {e}", exc_info=True)
             return None

    def _calculate_checksum(self, sentence: str) -> str:
        """Calculates the NMEA checksum."""
        checksum = 0
        if sentence.startswith('$'): sentence = sentence[1:]
        if '*' in sentence: sentence = sentence.split('*')[0]
        for char in sentence: checksum ^= ord(char)
        return f"{checksum:02X}"

    def _send_gga(self) -> None:
        """Sends GGA sentence to NTRIP caster."""
        # Check state before creating GGA
        if not self._socket or not self._connection_state.is_connected():
            logger.debug("Cannot send GGA: NTRIP not connected.")
            return

        gga_sentence = self._create_gga_sentence()
        if not gga_sentence:
            logger.error("Failed to create GGA sentence, cannot send.")
            return

        current_socket = self._socket # Use local variable in case it changes
        if not current_socket:
             logger.warning("Cannot send GGA: Socket became invalid.")
             return

        try:
            current_socket.sendall(gga_sentence.encode('ascii'))
            logger.debug("Sent GGA to NTRIP server.")
            self._last_gga_sent_time = datetime.now(timezone.utc) # Update time only on success
        except (OSError, socket.timeout, BrokenPipeError) as e:
            logger.error(f"Error sending GGA to NTRIP: {e}. Disconnecting.")
            self._state.increment_error_count("ntrip")
            self._close_socket()
            self._connection_state.set_state(NtripConnectionState.DISCONNECTED, "GGA Send Error")
            self._update_state_from_connection_state()
        except Exception as e:
            logger.error(f"Unexpected error sending GGA: {e}", exc_info=True)
            self._state.increment_error_count("ntrip")
            self._close_socket()
            self._connection_state.set_state(NtripConnectionState.DISCONNECTED, "GGA Send Error (Unexp)")
            self._update_state_from_connection_state()


    def _log_ui_message(self, message: str):
        """Adds a message to the UI log via the GnssState object."""
        # Use the state object's method which handles formatting and truncation
        self._state.add_ui_log_message(message)
        # Also log the original message for debugging clarity
        logger.debug(f"[UI Bound] {message}")


    @staticmethod
    def _extract_rtcm_message_types(data: bytes) -> List[int]:
        """Extracts RTCM message types from received data."""
        types_found = []
        i = 0
        data_len = len(data)
        while i < data_len - 5: # Need at least preamble + header bytes
            # Look for RTCM3 preamble (0xD3)
            if data[i] == 0xD3 and (data[i+1] & 0xFC) == 0: # Check reserved bits are zero
                try:
                    payload_length = ((data[i+1] & 0x03) << 8) | data[i+2]
                    total_length = 3 + payload_length + 3 # Preamble+Header + Payload + CRC
                    if i + total_length <= data_len:
                        # Extract message type (12 bits starting at byte 3, bit 0)
                        message_type = (data[i+3] << 4) | (data[i+4] >> 4)
                        types_found.append(message_type)
                        i += total_length # Move to the next potential message
                    else:
                        # Incomplete message at the end of the buffer
                        logger.debug(f"Incomplete RTCM message found at index {i}. Need {total_length}, have {data_len-i}.")
                        break # Stop parsing this chunk
                except IndexError:
                    logger.debug(f"IndexError parsing RTCM header at index {i}.")
                    break # Corrupted data or index issue
            else:
                i += 1 # Move to the next byte if not a preamble
        return types_found

    def _handle_rtcm_data(self, data: bytes) -> None:
        """Processes received RTCM data and forwards it."""
        if not data: return

        bytes_sent = self._gnss_device.write_data(data)
        if bytes_sent is not None and bytes_sent > 0:
            now = datetime.now(timezone.utc)
            rtcm_types = self._extract_rtcm_message_types(data[:bytes_sent])

            # Update stats locally first
            self._stats['total_bytes_received'] += bytes_sent
            self._stats['last_data_time'] = now
            # Keep only a small snippet of the last data for debugging state
            self._stats['last_rtcm_data'] = data[:min(bytes_sent, 20)]
            self._stats['rtcm_message_counter'] += 1
            # Calculate rate based on actual bytes sent
            self._stats['data_rates'].append(bytes_sent)
            if rtcm_types:
                self._stats['rtcm_message_types'].extend(rtcm_types)

            # Update shared state less frequently to reduce lock contention
            if len(self._stats['data_rates']) >= 5 or len(self._stats['rtcm_message_types']) >= 5:
                self._update_state_from_connection_state()

            # Debug log can be more frequent
            logger.debug(f"Sent {bytes_sent} bytes RTCM. Types: {rtcm_types if rtcm_types else 'None'}")
        elif bytes_sent is None:
            logger.error("Failed to send RTCM data to GNSS (serial write error).")
            # Consider if this should trigger NTRIP disconnect/reconnect

    def _check_retry_limit(self) -> bool:
        """Checks retry limit and updates state. Returns True if giving up."""
        reconnect_attempts = self._connection_state.reconnect_attempts
        if reconnect_attempts >= MAX_NTRIP_RETRIES:
            if not self._connection_state.has_given_up():
                 logger.warning(f"NTRIP connection failed after {reconnect_attempts} attempts. Giving up.")
                 self._connection_state.set_state(NtripConnectionState.GAVE_UP, f"Max retries ({MAX_NTRIP_RETRIES})")
                 self._update_state_from_connection_state() # Update state immediately
                 self._log_ui_message(f"NTRIP Gave Up (Retries: {reconnect_attempts})")
            return True
        return False

    def _run(self) -> None:
        """Main loop for the NTRIP client thread."""
        logger.info("NTRIP run loop starting.")
        last_state_update = datetime.now(timezone.utc)
        state_update_interval = timedelta(seconds=1) # For UI countdown timer

        while self._running.is_set():
            now = datetime.now(timezone.utc)

            # --- State Management ---
            current_socket = self._socket # Cache socket status for this iteration
            is_connected = self._connection_state.is_connected() and current_socket is not None

            # Update shared state periodically for UI refresh
            if now - last_state_update >= state_update_interval:
                self._update_state_from_connection_state()
                last_state_update = now

            # --- Connected State Logic ---
            if is_connected:
                try:
                    # Use non-blocking check first? select() might be better but adds complexity
                    # For simplicity, use timeout on recv
                    current_socket.settimeout(0.1) # Short timeout for recv check

                    # Check running flag before blocking recv
                    if not self._running.is_set(): break

                    rtcm_data = current_socket.recv(2048)
                    if rtcm_data:
                        self._handle_rtcm_data(rtcm_data)
                        # Reset data timeout check
                        last_data_time = self._stats['last_data_time']
                    else:
                        # Server closed connection gracefully
                        logger.info("NTRIP connection closed by server.")
                        self._close_socket()
                        self._connection_state.set_state(NtripConnectionState.DISCONNECTED, "Closed by server")
                        # State update happens in next loop iteration or finally block
                        continue # Skip GGA send, etc.

                except socket.timeout:
                    # No data received in this short interval, check other tasks
                    now = datetime.now(timezone.utc) # Update time after potential wait

                    # Send GGA periodically
                    if (now - self._last_gga_sent_time).total_seconds() >= NTRIP_GGA_INTERVAL:
                        self._send_gga() # Handles its own errors/disconnects

                    # Check for data timeout (only if still connected after potential GGA error)
                    current_socket = self._socket # Re-check socket after potential _send_gga disconnect
                    if current_socket and self._connection_state.is_connected():
                        last_data_time = self._stats['last_data_time']
                        if last_data_time and (now - last_data_time).total_seconds() > NTRIP_DATA_TIMEOUT:
                             logger.warning(f"No RTCM data received for {NTRIP_DATA_TIMEOUT}s. Reconnecting.")
                             self._close_socket()
                             self._connection_state.set_state(NtripConnectionState.DISCONNECTED, "No data timeout")
                             # State update happens in next loop iteration
                             continue

                except (OSError, ConnectionResetError, BrokenPipeError) as e:
                    logger.error(f"NTRIP socket error during receive: {e}. Reconnecting.")
                    self._state.increment_error_count("ntrip")
                    self._close_socket()
                    self._connection_state.set_state(NtripConnectionState.DISCONNECTED, f"Receive Error: {str(e)[:20]}")
                    # State update happens in next loop iteration
                    continue
                except Exception as e:
                    logger.error(f"Unexpected error in NTRIP receive loop: {e}", exc_info=True)
                    self._state.increment_error_count("ntrip")
                    self._close_socket()
                    self._connection_state.set_state(NtripConnectionState.DISCONNECTED, f"Runtime Error: {str(e)[:20]}")
                    # State update happens in next loop iteration
                    continue

            # --- Disconnected / Gave Up State Logic ---
            elif not self._connection_state.has_given_up():
                # Attempt to reconnect if disconnected and haven't given up
                logger.debug("NTRIP client disconnected. Checking reconnect conditions.")

                should_reconnect = False
                if self._next_reconnect_time is None:
                    should_reconnect = True # First time disconnected or after successful connect
                elif now >= self._next_reconnect_time:
                     should_reconnect = True # Reconnect time reached

                if should_reconnect:
                    logger.info(f"Attempting NTRIP connection (Attempt: {self._connection_state.reconnect_attempts + 1})...")
                    if self._connect(): # connect() handles state changes and updates
                        # Successful connection resets state in connect()
                        logger.info(f"NTRIP reconnected. Next GGA in {NTRIP_GGA_INTERVAL}s.")
                        # UI message handled by state update from connect()
                        self._next_reconnect_time = None # Clear reconnect schedule
                    else:
                        # Connection failed, check retry limit
                        if self._check_retry_limit():
                            # Gave up, wait long time or until stop/reset
                            wait_time = NTRIP_MAX_RECONNECT_TIMEOUT * 2
                            logger.debug(f"NTRIP gave up. Waiting {wait_time}s or until stopped.")
                            self._running.wait(timeout=wait_time)
                        else:
                            # Schedule next retry with backoff
                            self._reconnect_timeout = min(self._reconnect_timeout * 1.5, NTRIP_MAX_RECONNECT_TIMEOUT)
                            # Add jitter to avoid thundering herd effect
                            jitter = random.uniform(0, self._reconnect_timeout * 0.1)
                            wait_time = self._reconnect_timeout + jitter
                            self._next_reconnect_time = now + timedelta(seconds=wait_time)

                            # Update state with retry info (handled by _update_state_from_connection_state call)
                            logger.info(f"NTRIP connection failed ({self._connection_state.reconnect_attempts}/{MAX_NTRIP_RETRIES}). Retrying in {wait_time:.1f} seconds.")
                            # UI message handled by state update
                            self._running.wait(timeout=wait_time) # Wait for next attempt or shutdown
                # else:
                    # Not time to reconnect yet, just loop and wait implicitly
                    # Short sleep to prevent busy-waiting when not time yet
                    # time.sleep(0.1)

            else: # Gave Up State
                logger.debug("NTRIP client has given up. Waiting for stop/reset...")
                self._running.wait(timeout=NTRIP_MAX_RECONNECT_TIMEOUT * 2) # Long wait

            # Ensure a small delay even if connected and busy, to prevent 100% CPU
            # This is less critical now with socket timeouts but good practice
            if self._running.is_set():
                 time.sleep(0.01) # 10ms sleep

        # --- Loop Exit Cleanup ---
        logger.info("NTRIP run loop finishing.")
        self._close_socket() # Ensure socket is closed on exit
        self._connection_state.set_state(NtripConnectionState.DISCONNECTED, "Run loop exited")
        self._update_state_from_connection_state() # Final state update

    def reset_connection(self) -> bool:
        """Resets the connection state, clearing 'gave up' and attempts reconnect."""
        if not self._running.is_set():
            logger.info("Cannot reset connection: NTRIP client not running.")
            return False

        logger.warning("NTRIP connection reset requested.")
        self._log_ui_message("NTRIP connection reset.")

        # Reset state machine
        self._connection_state.set_state(NtripConnectionState.DISCONNECTED, "Reset by user")
        self._connection_state.reconnect_attempts = 0 # Reset attempts
        self._reconnect_timeout = NTRIP_INITIAL_RECONNECT_TIMEOUT # Reset backoff
        self._next_reconnect_time = None # Force immediate reconnect attempt on next loop iteration

        # Close any existing connection immediately
        self._close_socket()

        # Update state immediately to reflect reset
        self._update_state_from_connection_state()

        # No need to interrupt the thread's wait; the loop will handle it
        logger.info("NTRIP state reset. Will attempt immediate reconnection.")
        return True
