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

    def start(self):
        """Starts the NTRIP client thread."""
        if self._thread is not None and self._thread.is_alive():
            logger.warning("NTRIP client thread already running.")
            return
        self._running.set()
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
            try:
                # Shutdown may fail if socket is already closed or in error state
                socket_to_close.shutdown(socket.SHUT_RDWR)
            except OSError:
                # Ignore shutdown errors, just proceed to close
                logger.debug("Ignoring OSError during socket shutdown (likely already closed).")
            except Exception as e:
                 logger.warning(f"Unexpected error during socket shutdown: {e}")

            try:
                socket_to_close.close()
                logger.info("NTRIP socket closed.")
            except OSError as e:
                logger.warning(f"Error closing NTRIP socket: {e}")
            except Exception as e:
                 logger.warning(f"Unexpected error closing NTRIP socket: {e}")


        # Wait for thread to finish
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
            if self._thread.is_alive():
                 logger.warning("NTRIP thread did not exit cleanly.")
        logger.info("NTRIP client stopped.")


    def _connect(self) -> bool:
        """Establishes connection to the NTRIP caster."""
        if self._socket: # Ensure old socket is closed before creating new one
            try:
                self._socket.close()
                logger.debug("Closed previous NTRIP socket before reconnecting.")
            except OSError:
                pass # Ignore if already closed
            self._socket = None

        connect_msg = f"Connecting to {self._config.ntrip_server}:{self._config.ntrip_port}/{self._config.ntrip_mountpoint}..."
        # Set state *before* attempting connection
        self._state.set_ntrip_connected(False, "Connecting...") # Logs to UI implicitly
        logger.info(connect_msg)

        try:
            start_connect = time.monotonic()
            # Create and configure the socket
            self._socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._socket.settimeout(NTRIP_TIMEOUT) # Timeout for connection attempt
            self._socket.connect((self._config.ntrip_server, self._config.ntrip_port)) # --- This is line 76 from traceback ---

            # Prepare Authentication and HTTP Request
            auth_string = f"{self._config.ntrip_username}:{self._config.ntrip_password}"
            auth_b64 = base64.b64encode(auth_string.encode('ascii')).decode('ascii')
            # Ntrip-Version 2.0 is preferred if caster supports it, but 1.0 is safer fallback
            request_lines = [
                f"GET /{self._config.ntrip_mountpoint} HTTP/1.1",
                f"Host: {self._config.ntrip_server}:{self._config.ntrip_port}",
                "Ntrip-Version: Ntrip/1.0", # Stick with 1.0 for broader compatibility
                "User-Agent: Python NtripClient/1.2", # Slightly updated agent
                f"Authorization: Basic {auth_b64}",
                "Accept: */*",
                "Connection: close", # Important: Close connection after response/stream ends
                "\r\n" # Extra CRLF to end headers
            ]
            request = "\r\n".join(request_lines)
            logger.debug(f"Sending NTRIP request:\n{request.strip()}")
            self._socket.sendall(request.encode('ascii'))

            # Read Response Headers
            response_bytes = bytearray()
            self._socket.settimeout(NTRIP_TIMEOUT) # Timeout for receiving response
            while b"\r\n\r\n" not in response_bytes:
                 chunk = self._socket.recv(1024)
                 if not chunk:
                      # Server closed connection unexpectedly during header read
                      raise ConnectionAbortedError("NTRIP server closed connection during header read")
                 response_bytes.extend(chunk)
                 # Protect against excessively large headers
                 if len(response_bytes) > 8192: # 8KB limit for headers
                      raise OverflowError("NTRIP header response exceeded 8KB limit")

            # Process Response Headers
            headers_part, _, body_part = response_bytes.partition(b"\r\n\r\n")
            response_str = headers_part.decode('ascii', errors='ignore')
            end_connect = time.monotonic()
            self._state.update(last_ntrip_connect_time_sec=(end_connect - start_connect))
            logger.debug(f"Received NTRIP response headers:\n{response_str}")

            # Check response status
            # Look for "200 OK" in the first line
            first_line = response_str.splitlines()[0] if response_str else ""
            if " 200 OK" in first_line: # More robust check than ICY/HTTP specific strings
                logger.info("NTRIP connection successful.")
                self._state.set_ntrip_connected(True, "Connected") # Logs to UI
                self._reconnect_timeout = NTRIP_INITIAL_RECONNECT_TIMEOUT # Reset backoff
                self._send_gga() # Send initial GGA immediately after connection
                self._last_gga_sent_time = datetime.now(timezone.utc)
                # Handle any data received immediately after headers
                if body_part:
                     logger.debug(f"Processing {len(body_part)} bytes received with headers.")
                     self._handle_rtcm_data(body_part)
                return True
            else:
                # Connection failed - log status line
                status_line = first_line if first_line else "No Status Line Received"
                logger.error(f"NTRIP connection failed. Status: '{status_line}'")
                self._state.set_ntrip_connected(False, f"Failed: {status_line[:30]}")
                self._state.increment_ntrip_reconnects() # Logs failure to UI
                if self._socket:
                    self._socket.close()
                self._socket = None
                return False

        except socket.timeout:
            logger.error("NTRIP connection timed out.")
            self._state.set_ntrip_connected(False, "Timeout")
            self._state.increment_ntrip_reconnects()
            # Cleanup socket if it exists
            if self._socket:
                socket_to_close = self._socket
                self._socket = None
                try: socket_to_close.close()
                except OSError: pass
            return False
        # Catch specific network errors + OSError for broader coverage (like No route to host)
        except (socket.gaierror, ConnectionRefusedError, ConnectionAbortedError, OverflowError, OSError) as e:
            logger.error(f"NTRIP socket connection error: {e}")
            self._state.set_ntrip_connected(False, f"Socket Error: {str(e)[:20]}")
            # *** CORRECTED ERROR COUNT CALL ***
            self._state.increment_error_count("ntrip") # Use the unified method
            # *** END CORRECTION ***
            self._state.increment_ntrip_reconnects()
            # Cleanup socket
            if self._socket:
                try: self._socket.close()
                except OSError: pass
            self._socket = None
            return False
        except Exception as e: # Catch any other unexpected error during connection
            logger.error(f"Unexpected NTRIP connection error: {e}", exc_info=True)
            self._state.set_ntrip_connected(False, f"Error: {str(e)[:20]}")
            # *** CORRECTED ERROR COUNT CALL ***
            self._state.increment_error_count("ntrip") # Use the unified method
            # *** END CORRECTION ***
            self._state.increment_ntrip_reconnects()
            # Cleanup socket
            if self._socket:
                try: self._socket.close()
                except OSError: pass
            self._socket = None
            return False

    def _create_gga_sentence(self) -> str:
        """Creates a GGA sentence based on the current state."""
        # Uses state object passed during init
        state = self._state.get_state_snapshot()
        now = datetime.now(timezone.utc)
        # Format time as HHMMSS.ss (pynmea2 expects microseconds, but spec often shows centiseconds)
        # Let's stick to two decimal places for seconds for compatibility.
        time_str = now.strftime("%H%M%S.%f")[:9] # HHMMSS.mm

        # Get position, defaulting to config values if no lock
        lat, lon, alt = self._config.default_lat, self._config.default_lon, self._config.default_alt
        fix_quality = FIX_QUALITY_INVALID # Default to invalid
        num_sats = 0
        hdop = DEFAULT_HDOP

        if state.get('have_position_lock'):
             pos = state.get('position', {})
             lat = pos.get('lat', self._config.default_lat)
             lon = pos.get('lon', self._config.default_lon)
             alt = pos.get('alt', self._config.default_alt)
             # Use current fix quality if valid, otherwise keep invalid
             current_fix = state.get('fix_type', FIX_QUALITY_INVALID)
             fix_quality = current_fix # Use the actual fix quality from state
             num_sats = state.get('num_satellites_used', 0)
             hdop = state.get('hdop', DEFAULT_HDOP)
        else:
             # If no position lock, explicitly set fix quality to Invalid (0)
             fix_quality = FIX_QUALITY_INVALID

        # Format Lat/Lon to NMEA standard (DDMM.MMMMM)
        lat_deg = int(abs(lat))
        lat_min = (abs(lat) - lat_deg) * 60
        lat_nmea = f"{lat_deg:02d}{lat_min:09.6f}" # Ensure 2 digits for Deg, 9 for Min (incl. decimal)
        lat_dir = "N" if lat >= 0 else "S"

        lon_deg = int(abs(lon))
        lon_min = (abs(lon) - lon_deg) * 60
        lon_nmea = f"{lon_deg:03d}{lon_min:09.6f}" # Ensure 3 digits for Deg, 9 for Min
        lon_dir = "E" if lon >= 0 else "W"

        # Format other fields
        alt_str = f"{alt:.1f}" # Altitude with one decimal place
        # Geoid separation often unknown/unused client-side, use dummy value
        sep_str = "-0.0"
        # Ensure HDOP has two decimal places
        hdop_str = f"{hdop:.2f}"
        # Ensure num sats is two digits
        num_sats_str = f"{num_sats:02d}"

        # Assemble the GGA data part (without $ and *checksum)
        # Fields: Time, Lat, N/S, Lon, E/W, FixQual, NumSats, HDOP, Alt, M, GeoidSep, M, DGPSAge, DGPSStationID
        gga_data = (
            f"GNGGA,{time_str},{lat_nmea},{lat_dir},{lon_nmea},{lon_dir},"
            f"{fix_quality},{num_sats_str},{hdop_str},{alt_str},M,"
            f"{sep_str},M,," # Leave DGPS fields empty
        )

        # Calculate and append checksum
        checksum = GnssDevice._calculate_checksum(gga_data) # Use static method from GnssDevice
        return f"${gga_data}*{checksum}\r\n"

    def _send_gga(self) -> None:
        """Sends the generated GGA sentence to the NTRIP caster."""
        if not self._socket or not self._state.ntrip_connected:
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
            # *** CORRECTED ERROR COUNT CALL ***
            self._state.increment_error_count("ntrip") # Use the unified method
            # *** END CORRECTION ***
            if self._socket:
                socket_to_close = self._socket
                self._socket = None
                try: socket_to_close.close()
                except OSError: pass
            self._state.set_ntrip_connected(False, "GGA Send Error")
        except Exception as e: # Catch any other unexpected error
            logger.error(f"Unexpected error sending GGA: {e}", exc_info=True)
            # *** CORRECTED ERROR COUNT CALL ***
            self._state.increment_error_count("ntrip") # Use the unified method
            # *** END CORRECTION ***
            if self._socket:
                socket_to_close = self._socket
                self._socket = None
                try: socket_to_close.close()
                except OSError: pass
            self._state.set_ntrip_connected(False, "GGA Send Error")


    @staticmethod
    def _extract_rtcm_message_types(data: bytes) -> List[int]:
        """Extracts message types from a block of RTCM3 data."""
        # Basic RTCM3 structure: Preamble (0xD3) + 6 reserved bits (000000) + 10 bits length + Payload + 24 bits CRC
        types_found = []
        i = 0
        data_len = len(data)
        while i < data_len - 5: # Need at least preamble, length, type (3 bytes) + CRC (3 bytes) = 6 bytes theoretically minimum? 5 is safer start.
            # Find preamble
            if data[i] == 0xD3:
                 # Check reserved bits are zero (first 6 bits of byte after preamble)
                 if (data[i+1] & 0xFC) == 0:
                     try:
                         # Extract payload length (last 2 bits of byte i+1, plus byte i+2)
                         payload_length = ((data[i+1] & 0x03) << 8) | data[i+2]
                         # Total message length = Preamble(1) + Reserved/Len(2) + Payload + CRC(3)
                         total_length = 3 + payload_length + 3
                         # Check if the full message is potentially within the buffer
                         if i + total_length <= data_len:
                             # Extract message type (first 12 bits of payload = byte i+3 and first 4 bits of byte i+4)
                             message_type = (data[i+3] << 4) | (data[i+4] >> 4)
                             types_found.append(message_type)
                             # TODO: Add CRC check here if needed (complex)
                             # Move index to the start of the next potential message
                             i += total_length
                         else:
                             # Full message extends beyond current data buffer
                             logger.debug(f"Incomplete RTCM message found at index {i}. Length {total_length}, Buffer {data_len-i}.")
                             break # Stop processing this buffer
                     except IndexError:
                         # Should not happen with the length check above, but safety first
                         logger.debug(f"IndexError while parsing RTCM at index {i}.")
                         break # Stop processing
                 else:
                     # Preamble found, but reserved bits are not zero - skip this byte
                     logger.debug(f"Byte after RTCM preamble 0xD3 has non-zero reserved bits at index {i+1}.")
                     i += 1
            else:
                # Current byte is not preamble, move to next
                i += 1
        return types_found

    def _handle_rtcm_data(self, data: bytes) -> None:
        """Processes received RTCM data and sends it to the GNSS device."""
        if not data:
            return

        # Optional: Log if data doesn't start with preamble? Could be normal if stream breaks mid-message.
        # first_preamble = data.find(0xD3)
        # if first_preamble == -1: logger.warning(f"Received block without RTCM preamble. Discarding {len(data)} bytes."); return
        # elif first_preamble > 0: logger.warning(f"Discarding {first_preamble} non-RTCM bytes."); data = data[first_preamble:]

        # Write the received data directly to the GNSS device
        bytes_sent = self._gnss_device.write_data(data)

        if bytes_sent is not None and bytes_sent > 0:
            now = datetime.now(timezone.utc)
            rtcm_types = self._extract_rtcm_message_types(data[:bytes_sent]) # Analyse only the bytes successfully sent
            # Update state safely
            with self._state._lock:
                self._state.ntrip_total_bytes += bytes_sent
                self._state.ntrip_last_data_time = now
                self._state.last_rtcm_data_received = data[:20] # Store first 20 bytes as sample
                self._state.rtcm_message_counter += 1
                # Update data rate deque
                self._state.ntrip_data_rates.append(bytes_sent)
                # Update last seen RTCM types deque
                if rtcm_types:
                    self._state.last_rtcm_message_types.extend(rtcm_types)
            logger.debug(f"Sent {bytes_sent} bytes of RTCM data. Types: {rtcm_types if rtcm_types else 'None decoded'}")
        elif bytes_sent is None:
            logger.error("Failed to send RTCM data to GNSS device (serial error).")
            # Error should have been handled and port closed by GnssDevice.write_data

    def _run(self) -> None:
        """Main loop for the NTRIP client thread."""
        logger.info("NTRIP run loop started.")
        while self._running.is_set():
            # Check connection status from state
            is_connected = self._state.get_state_snapshot()['ntrip_connected']

            if is_connected and self._socket is not None:
                # --- Connected State ---
                try:
                    # Set short timeout for recv to allow periodic GGA sending
                    self._socket.settimeout(1.0)
                    rtcm_data = self._socket.recv(2048) # Read up to 2KB

                    if rtcm_data:
                        # Data received
                        self._handle_rtcm_data(rtcm_data)
                        # Update last data time only if data received
                        # self._state.update(ntrip_last_data_time=datetime.now(timezone.utc)) # Redundant, handle_rtcm does this
                    else:
                        # Socket closed gracefully by server (recv returns empty bytes)
                        logger.info("NTRIP connection closed by server.")
                        socket_to_close = self._socket
                        self._socket = None
                        socket_to_close.close()
                        self._state.set_ntrip_connected(False, "Closed by server")
                        # No need to sleep here, loop will transition to disconnected state naturally
                        continue

                except socket.timeout:
                    # Normal case - no data received within timeout
                    now = datetime.now(timezone.utc)
                    # Check if it's time to send GGA
                    if (now - self._last_gga_sent_time).total_seconds() >= NTRIP_GGA_INTERVAL:
                         self._send_gga() # Handles its own errors
                         self._last_gga_sent_time = now # Update time *after* successful send attempt (or failed)

                    # Check for data timeout
                    last_data_time = self._state.get_state_snapshot().get('ntrip_last_data_time')
                    if last_data_time and (now - last_data_time).total_seconds() > NTRIP_DATA_TIMEOUT:
                         logger.warning(f"No RTCM data received for {NTRIP_DATA_TIMEOUT}s. Reconnecting.")
                         socket_to_close = self._socket
                         self._socket = None
                         try: socket_to_close.close()
                         except OSError: pass
                         self._state.set_ntrip_connected(False, "No data received")
                         continue # Loop will go to disconnected state

                except (OSError, ConnectionResetError, BrokenPipeError) as e:
                    # Socket errors during receive
                    logger.error(f"NTRIP socket error during receive: {e}. Reconnecting.")
                    # *** CORRECTED ERROR COUNT CALL ***
                    self._state.increment_error_count("ntrip") # Use the unified method
                    # *** END CORRECTION ***
                    if self._socket:
                        socket_to_close = self._socket
                        self._socket = None
                        try: socket_to_close.close()
                        except OSError: pass
                    self._state.set_ntrip_connected(False, f"Receive Error: {str(e)[:20]}")
                    # time.sleep(self._reconnect_timeout) # Optional sleep before trying _connect again
                    continue
                except Exception as e:
                    # Unexpected errors
                    logger.error(f"Unexpected error in NTRIP receive loop: {e}", exc_info=True)
                    # *** CORRECTED ERROR COUNT CALL ***
                    self._state.increment_error_count("ntrip") # Use the unified method
                    # *** END CORRECTION ***
                    if self._socket:
                        socket_to_close = self._socket
                        self._socket = None
                        try: socket_to_close.close()
                        except OSError: pass
                    self._state.set_ntrip_connected(False, f"Runtime Error: {str(e)[:20]}")
                    # time.sleep(self._reconnect_timeout) # Optional sleep
                    continue

            else:
                # --- Disconnected State ---
                logger.debug("NTRIP client in disconnected state. Attempting connection.")
                if self._connect():
                     # Connection successful, reset backoff timer
                     self._reconnect_timeout = NTRIP_INITIAL_RECONNECT_TIMEOUT
                     logger.info(f"NTRIP reconnected. Next GGA in {NTRIP_GGA_INTERVAL}s.")
                else:
                    # Connection failed, increase backoff timeout
                    self._reconnect_timeout = min(self._reconnect_timeout * 1.5, NTRIP_MAX_RECONNECT_TIMEOUT)
                    logger.info(f"NTRIP connection failed. Retrying in {self._reconnect_timeout:.1f} seconds.")
                    # Wait using event.wait for better shutdown responsiveness
                    # If stop() is called, wait() will return False immediately
                    self._running.wait(timeout=self._reconnect_timeout)
                    # Check if stop was requested during wait
                    if not self._running.is_set():
                         logger.info("NTRIP stop requested during reconnect wait.")
                         break # Exit the loop if stop was called

        # --- Cleanup after loop exits ---
        if self._socket:
            try: self._socket.close()
            except OSError: pass
            self._socket = None
        logger.info("NTRIP run loop finished.")
