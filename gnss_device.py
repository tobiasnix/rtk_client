# gnss_device.py - Handles serial communication with the GNSS module

import logging
import time
from typing import Optional

import serial

from module_profiles import LC29HProfile, ModuleProfile
from rtk_constants import SERIAL_TIMEOUT
from rtk_state import GnssState

logger = logging.getLogger(__name__)

def discover_gnss_ports() -> list:
    """Discover available serial ports that might have GNSS devices."""
    import serial.tools.list_ports
    candidates = []
    for port_info in serial.tools.list_ports.comports():
        desc = (port_info.description or '').lower()
        mfg = (port_info.manufacturer or '').lower()
        # Match common GNSS manufacturers/descriptions
        keywords = ['quectel', 'gnss', 'gps', 'u-blox', 'ublox', 'septentrio', 'nmea', 'acm', 'cp210']
        if any(kw in desc or kw in mfg for kw in keywords):
            candidates.append(port_info.device)
    # If no specific match, return all ports as fallback
    if not candidates:
        candidates = [p.device for p in serial.tools.list_ports.comports()]
    return candidates


class GnssDevice:
    """Handles serial communication with the GNSS module."""
    def __init__(self, port: str, baudrate: int, state: GnssState,
                 profile: Optional[ModuleProfile] = None):
        self._port_name = port
        self._baudrate = baudrate
        self._serial_port: Optional[serial.Serial] = None
        self._state = state
        self._profile: ModuleProfile = profile or LC29HProfile()

    def connect(self) -> bool:
        """Establishes the serial connection."""
        if self.is_connected():
            logger.debug("Serial port already connected.")
            return True
        try:
            logger.debug(f"Attempting to connect to {self._port_name} at {self._baudrate} baud...")
            # Set write_timeout to match read timeout for consistency
            self._serial_port = serial.Serial(
                self._port_name,
                self._baudrate,
                timeout=SERIAL_TIMEOUT,
                write_timeout=SERIAL_TIMEOUT
            )
            if self._serial_port.is_open:
                 logger.info(f"Connected to GNSS device on {self._port_name} at {self._baudrate} baud")
                 # Clear buffers upon successful connection
                 self._serial_port.reset_input_buffer()
                 self._serial_port.reset_output_buffer()
                 self._state.add_ui_log_message(f"Serial connected: {self._port_name}")
                 return True
            else:
                 logger.error(f"Serial port {self._port_name} failed to open but no exception.")
                 self._state.add_ui_log_message(f"Serial Error: Failed to open {self._port_name}")
                 self._serial_port = None
                 return False
        except serial.SerialException as e:
            logger.error(f"Error opening serial port {self._port_name}: {e}")
            self._state.add_ui_log_message(f"Serial Error: {e}")
            self._serial_port = None
            return False
        except Exception as e:
            logger.error(f"Unexpected error connecting to serial port {self._port_name}: {e}", exc_info=True)
            self._state.add_ui_log_message(f"Serial Conn. Error: {e}")
            self._serial_port = None
            return False

    def is_connected(self) -> bool:
        """Checks if the serial port is open."""
        return self._serial_port is not None and self._serial_port.is_open

    @staticmethod
    def _calculate_checksum(sentence: str) -> str:
        """Calculates the NMEA checksum for a sentence (excluding $ and *)."""
        checksum = 0
        if sentence.startswith('$'):
            sentence = sentence[1:]
        if '*' in sentence:
            sentence = sentence.split('*')[0]
        for char in sentence:
            checksum ^= ord(char)
        return f"{checksum:02X}"

    def send_command(self, command: str, expect_ack: bool = True) -> Optional[str]:
        """Sends a command to the GNSS module and waits for a response.
           expect_ack: If True, expects a standard $PAIR001 ACK."""
        if not self.is_connected():
            logger.error("Cannot send command: Serial port not connected.")
            return None

        original_command_name = command.split(',')[0] # For logging

        if not command.startswith('$'):
            command = '$' + command
        if '*' in command:
            command = command.split('*')[0]

        checksum = self._calculate_checksum(command)
        full_command = f"{command}*{checksum}\r\n"

        try:
            start_time = time.monotonic()
            # Lock could be added here if multiple threads might send commands
            # with self._state._lock: # Example if needed
            self._serial_port.reset_output_buffer() # Clear output buffer first
            self._serial_port.reset_input_buffer()  # <<< Clear input buffer BEFORE sending

            bytes_written = self._serial_port.write(full_command.encode('ascii'))
            self._serial_port.flush() # Ensure data is sent immediately
            logger.debug(f"Sent command ({bytes_written} bytes): {full_command.strip()}")

            # Read the response line
            response_bytes = self._serial_port.readline()
            end_time = time.monotonic()
            response_time = end_time - start_time
            self._state.update(last_command_response_time_sec=response_time)

            response = response_bytes.decode('ascii', errors='ignore').strip()
            logger.debug(f"Received response for {original_command_name}: {response} (in {response_time:.3f}s)")

            # Optional: Add check for ACK if expected
            if expect_ack:
                 if self._profile.check_ack(original_command_name, response):
                     logger.debug(f"Command {original_command_name} acknowledged successfully.")
                 elif response:
                     logger.warning(f"Command {original_command_name} received non-ACK response: {response}")
                 else:
                     logger.warning(f"No response received for expected ACK to {original_command_name}")
                     # return None

            return response # Return whatever was received

        except serial.SerialTimeoutException:
            logger.warning(f"Timeout waiting for response to command: {original_command_name}")
            self._state.update(last_command_response_time_sec=None)
            return None
        except serial.SerialException as e:
            logger.error(f"Serial error during command '{original_command_name}': {e}")
            self._state.increment_error_count("gps")
            self.close()
            return None
        except Exception as e:
            logger.error(f"Unexpected error sending command '{original_command_name}': {e}", exc_info=True)
            self._state.increment_error_count("gps")
            self._state.update(last_command_response_time_sec=None)
            return None

    def read_line(self) -> Optional[str]:
        """Reads a line from the serial port, non-blocking."""
        if not self.is_connected():
            return None

        try:
            # Check waiting bytes first
            if self._serial_port.in_waiting > 0:
                line_bytes = self._serial_port.readline()
                if not line_bytes:
                    return "" # Timeout occurred while reading
                try:
                     line = line_bytes.decode('ascii', errors='ignore').strip()
                     # Very verbose logging - disable unless debugging specific NMEA issues
                     # logger.debug(f"Read line: {line}")
                     return line
                except UnicodeDecodeError as e:
                     logger.warning(f"Failed to decode received bytes: {line_bytes[:50]}... Error: {e}")
                     return "" # Return empty on decode error
            else:
                return "" # No data available right now
        except serial.SerialException as e:
            logger.error(f"Serial error reading line: {e}")
            self._state.increment_error_count("gps")
            self.close()
            return None
        except OSError as e:
             # Catch OS-level errors (e.g., device disconnected)
             logger.error(f"OS error reading line: {e}")
             self._state.increment_error_count("gps")
             self.close()
             return None
        except Exception as e:
            logger.error(f"Unexpected error reading line: {e}", exc_info=True)
            self._state.increment_error_count("gps")
            # Close might be too aggressive here, depends on the error
            # self.close()
            return None

    def write_data(self, data: bytes) -> Optional[int]:
        """Writes raw bytes to the serial port (e.g., RTCM data)."""
        if not self.is_connected():
            logger.error("Cannot write data: Serial port not connected.")
            return None
        try:
             # Add check for zero bytes to prevent unnecessary write calls
             if not data:
                 return 0
             bytes_written = self._serial_port.write(data)
             # Consider adding flush if immediate sending is critical,
             # though write() often handles buffering internally.
             # self._serial_port.flush()
             return bytes_written
        except serial.SerialTimeoutException:
             logger.warning("Serial write timeout occurred.")
             self._state.increment_error_count("gps")
             return 0
        except serial.SerialException as e:
            logger.error(f"Serial error writing data: {e}")
            self._state.increment_error_count("gps")
            self.close()
            return None
        except OSError as e:
             logger.error(f"OS error writing data: {e}")
             self._state.increment_error_count("gps")
             self.close()
             return None
        except Exception as e:
             logger.error(f"Unexpected error writing data: {e}", exc_info=True)
             self._state.increment_error_count("gps")
             return None

    def configure_module(self) -> bool:
        """Sends configuration commands via the active ModuleProfile. Returns True if all expected ACKs received."""
        logger.info(f"Configuring module via {self._profile.display_name} profile...")
        self._state.add_ui_log_message("Configuring GNSS module...")
        time.sleep(0.5)  # Delay before starting config

        # --- Get Firmware Version ---
        fw_cmd = self._profile.firmware_command()
        if fw_cmd is not None:
            version_response = self.send_command(fw_cmd, expect_ack=False)
            fw_version = "Unknown"
            if version_response:
                parsed = self._profile.parse_firmware_response(version_response)
                if parsed:
                    fw_version = parsed
                    logger.info(f"Detected Firmware (parsed): {fw_version}")
                else:
                    logger.warning(f"Could not parse firmware response: {version_response}")
                    fw_version = "Parse Error"
            else:
                logger.warning("No response received for firmware query.")
                fw_version = "No Response"
        else:
            fw_version = "N/A"
            logger.info("Profile does not support firmware query; skipping.")

        self._state.update(firmware_version=fw_version)
        if fw_version not in ["Unknown", "No Response", "N/A"]:
            self._state.add_ui_log_message(f"Firmware: {fw_version}")

        # --- Send Configuration Commands ---
        commands = self._profile.config_commands()
        if not commands:
            logger.info("Profile has no configuration commands; skipping.")
            return True

        logger.info(f"Sending {len(commands)} configuration commands...")
        success_count = 0
        total_sent = 0

        for item in commands:
            cmd_str = item["cmd"]
            expect_ack = item["ack"]
            command_name = cmd_str.split(",")[0]  # For logging

            response = self.send_command(cmd_str, expect_ack=expect_ack)
            total_sent += 1

            if expect_ack:
                if self._profile.check_ack(cmd_str, response):
                    success_count += 1
                else:
                    logger.error(f"Configuration command {command_name} failed or received unexpected response.")
            elif response:
                success_count += 1
            else:
                logger.error(f"Configuration command {command_name} (no ACK expected) received no response.")

            time.sleep(0.15)  # Delay between commands

        config_success = success_count == total_sent
        log_func = logger.info if config_success else logger.warning
        log_func(f"Module configuration complete. {success_count}/{total_sent} commands acknowledged/responded as expected.")
        self._state.add_ui_log_message(f"Module config sent ({success_count}/{total_sent} Ack).")
        return config_success


    def close(self) -> None:
        """Closes the serial connection."""
        if self._serial_port and self._serial_port.is_open:
            port_name = self._port_name
            try:
                # Ensure buffers are cleared before closing to avoid issues
                self._serial_port.reset_input_buffer()
                self._serial_port.reset_output_buffer()
                self._serial_port.close()
                logger.info(f"Serial port {port_name} closed.")
            except Exception as e:
                logger.error(f"Error closing serial port {port_name}: {e}")
            finally:
                self._serial_port = None
                self._state.add_ui_log_message(f"Serial disconnected: {port_name}")
        elif self._serial_port is None:
             logger.debug("Close called, but serial port was already None.")
        else:
             logger.debug(f"Close called, but serial port {self._port_name} was already closed.")
             self._serial_port = None
