# gnss_device.py - Handles serial communication with the GNSS module

import serial
import time
import logging
from typing import Optional
from rtk_state import GnssState
from rtk_constants import SERIAL_TIMEOUT

logger = logging.getLogger(__name__)

class GnssDevice:
    """Handles serial communication with the GNSS module."""
    def __init__(self, port: str, baudrate: int, state: GnssState):
        self._port_name = port
        self._baudrate = baudrate
        self._serial_port: Optional[serial.Serial] = None
        self._state = state

    def connect(self) -> bool:
        """Establishes the serial connection."""
        if self.is_connected():
            logger.debug("Serial port already connected.")
            return True
        try:
            logger.debug(f"Attempting to connect to {self._port_name} at {self._baudrate} baud...")
            self._serial_port = serial.Serial(self._port_name, self._baudrate, timeout=SERIAL_TIMEOUT)
            # Check if port opened successfully
            if self._serial_port.is_open:
                 logger.info(f"Connected to GNSS device on {self._port_name} at {self._baudrate} baud")
                 self._state.add_ui_log_message(f"Serial connected: {self._port_name}")
                 return True
            else:
                 # This case might be less common with pyserial, exceptions usually raised
                 logger.error(f"Serial port {self._port_name} failed to open.")
                 self._state.add_ui_log_message(f"Serial Error: Failed to open {self._port_name}")
                 self._serial_port = None
                 return False
        except serial.SerialException as e:
            logger.error(f"Error opening serial port {self._port_name}: {e}")
            self._state.add_ui_log_message(f"Serial Error: {e}")
            self._serial_port = None
            return False
        except Exception as e: # Catch other potential errors like permission issues
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

    def send_command(self, command: str) -> Optional[str]:
        """Sends a command to the GNSS module and waits for a response."""
        if not self.is_connected():
            logger.error("Cannot send command: Serial port not connected.")
            return None

        # Ensure command format ($COMMAND*CS)
        if not command.startswith('$'):
            command = '$' + command
        if '*' in command: # Remove existing checksum if user included it
            command = command.split('*')[0]

        checksum = self._calculate_checksum(command)
        full_command = f"{command}*{checksum}\r\n"

        try:
            start_time = time.monotonic()
            # Ensure buffers are clear before sending/receiving
            self._serial_port.reset_input_buffer()
            self._serial_port.reset_output_buffer()

            bytes_written = self._serial_port.write(full_command.encode('ascii'))
            self._serial_port.flush() # Ensure data is sent
            logger.debug(f"Sent command ({bytes_written} bytes): {full_command.strip()}")

            # Read the response line
            response_bytes = self._serial_port.readline()
            end_time = time.monotonic()
            self._state.update(last_command_response_time_sec=(end_time - start_time))

            response = response_bytes.decode('ascii', errors='ignore').strip()
            logger.debug(f"Received response: {response}")
            return response
        except serial.SerialTimeoutException:
            logger.warning(f"Timeout waiting for response to command: {command.split(',')[0]}") # Log only command name
            self._state.update(last_command_response_time_sec=None) # Indicate timeout
            return None
        except serial.SerialException as e:
            logger.error(f"Serial error during command '{command.split(',')[0]}': {e}")
            self._state.increment_error_count("gps")
            self.close() # Close port on serial error
            return None
        except Exception as e:
            logger.error(f"Unexpected error sending command '{command.split(',')[0]}': {e}", exc_info=True)
            self._state.increment_error_count("gps")
            self._state.update(last_command_response_time_sec=None) # Indicate error
            return None

    def read_line(self) -> Optional[str]:
        """Reads a line from the serial port, non-blocking."""
        if not self.is_connected():
            # logger.warning("Attempted to read line, but not connected.")
            return None # Return None to indicate closed port

        try:
            # Check if there's data waiting to avoid blocking indefinitely if timeout is long/None
            if self._serial_port.in_waiting > 0:
                line_bytes = self._serial_port.readline()
                # readline with timeout might return empty bytes on timeout
                if not line_bytes:
                    # logger.debug("Readline returned empty bytes (timeout?).")
                    return "" # Indicate timeout/no complete line received yet
                # Decode received bytes
                try:
                     line = line_bytes.decode('ascii', errors='ignore').strip()
                     # logger.debug(f"Read line: {line}") # Very verbose
                     return line
                except UnicodeDecodeError as e:
                     logger.warning(f"Failed to decode received bytes: {line_bytes[:50]}... Error: {e}")
                     return "" # Return empty on decode error
            else:
                # logger.debug("No data waiting in serial buffer.")
                return "" # No data available right now
        except serial.SerialException as e:
            # Handle potential port closure or other serial errors during read
            logger.error(f"Serial error reading line: {e}")
            self._state.increment_error_count("gps")
            self.close() # Close the port if a serial error occurs
            return None # Indicate closed port/error state
        except Exception as e:
            logger.error(f"Unexpected error reading line: {e}", exc_info=True)
            self._state.increment_error_count("gps")
            # Decide whether to close port on unexpected errors too
            # self.close()
            return None # Indicate error state

    def write_data(self, data: bytes) -> Optional[int]:
        """Writes raw bytes to the serial port (e.g., RTCM data)."""
        if not self.is_connected():
            logger.error("Cannot write data: Serial port not connected.")
            return None
        try:
             bytes_written = self._serial_port.write(data)
             # logger.debug(f"Wrote {bytes_written} bytes to serial.")
             return bytes_written
        except serial.SerialTimeoutException:
             # Write timeouts are less common unless flow control is involved
             logger.warning("Serial write timeout occurred.")
             self._state.increment_error_count("gps")
             return 0 # Indicate zero bytes written on timeout
        except serial.SerialException as e:
            logger.error(f"Serial error writing data: {e}")
            self._state.increment_error_count("gps")
            self.close() # Close port on serial error
            return None # Indicate error
        except Exception as e:
             logger.error(f"Unexpected error writing data: {e}", exc_info=True)
             self._state.increment_error_count("gps")
             return None # Indicate error

    def configure_module(self) -> None:
        """Sends configuration commands to the LC29H(DA) module."""
        logger.info("Configuring LC29H (DA) module...")
        self._state.add_ui_log_message("Configuring GNSS module...")
        time.sleep(0.5) # Short delay after connect before sending commands

        # --- Get Firmware Version ---
        version_response = self.send_command("PQTMVERNO")
        fw_version = "Unknown" # Default
        if version_response:
            try:
                # Check for the expected format first
                if version_response.startswith("$PQTMVERNO,"):
                    parts = version_response.split(',')
                    if len(parts) > 1:
                        fw_version = parts[1] # Extract version string
                        logger.info(f"Detected Firmware (parsed): {fw_version}")
                    else:
                         logger.warning(f"Could not split firmware response: {version_response}")
                         fw_version = "Parse Error (Split)"
                # Handle the specific unexpected format seen in logs
                elif version_response == "2*01": # Example of handling specific odd response
                     logger.warning(f"Received known unexpected firmware response: {version_response}. Cannot parse version.")
                     fw_version = "Unexpected (2*01)"
                elif "ERROR" in version_response.upper():
                    logger.warning(f"Firmware query returned error: {version_response}")
                    fw_version = "Query Error"
                else:
                    # Log other unexpected formats
                    logger.warning(f"Unexpected firmware response format: {version_response}")
                    fw_version = "Parse Error (Format)"
            except Exception as e:
                logger.warning(f"Exception parsing firmware version from '{version_response}': {e}")
                fw_version = "Parse Exception"
        else:
            logger.warning("No response received for firmware query.")
            fw_version = "No Response"

        # Update state with determined firmware version
        self._state.update(firmware_version=fw_version)
        if fw_version != "Unknown":
            self._state.add_ui_log_message(f"Firmware: {fw_version}")

        # --- Send Configuration Commands ---
        # Commands based on previous logic and V1.4 spec check for DA
        # PAIR062,type,rate: Output NMEA sentence rate (0=GGA, 2=GSA, 3=GSV, 4=RMC, 5=VTG). Rate 1 = 1Hz.
        # PAIR436,1: Enable RTCM input passthrough? (Not explicitly in spec V1.4) - Assume enables RTCM input
        # PAIR513: Enable RTK mode? (Not explicitly in spec V1.4) - Assume enables RTK
        commands = [
            "PAIR062,0,1", # Output GGA at 1Hz
            "PAIR062,4,1", # Output RMC at 1Hz
            "PAIR062,2,1", # Output GSA at 1Hz
            "PAIR062,3,1", # Output GSV at 1Hz
            "PAIR062,5,1", # Output VTG at 1Hz (Optional, disable if not needed)
            "PAIR436,1",   # Enable RTCM input (Assumption)
            "PAIR513",     # Enable RTK mode (Assumption)
        ]
        logger.info(f"Sending {len(commands)} configuration commands...")
        success_count = 0
        for cmd in commands:
            response = self.send_command(cmd)
            # Basic check for acknowledgement (PAIR001,cmd_id,status - 0=success)
            if response and response.startswith(f"$PAIR001,{cmd.split(',')[0][4:]},0"):
                logger.debug(f"Command {cmd} acknowledged successfully.")
                success_count += 1
            elif response:
                 logger.warning(f"Command {cmd} returned unexpected response: {response}")
            else:
                 logger.warning(f"No response received for command {cmd}")
            time.sleep(0.15) # Small delay between commands

        if success_count == len(commands):
             logger.info("All module configuration commands sent and acknowledged.")
             self._state.add_ui_log_message("Module configuration sent (Ack).")
        else:
             logger.warning(f"Module configuration commands sent, but only {success_count}/{len(commands)} were acknowledged successfully.")
             self._state.add_ui_log_message(f"Module config sent ({success_count}/{len(commands)} Ack).")


    def close(self) -> None:
        """Closes the serial connection."""
        if self._serial_port and self._serial_port.is_open:
            port_name = self._port_name # Store before closing
            try:
                self._serial_port.close()
                logger.info(f"Serial port {port_name} closed.")
            except Exception as e:
                logger.error(f"Error closing serial port {port_name}: {e}")
            finally:
                # Ensure state reflects closure regardless of exceptions
                self._serial_port = None
                self._state.add_ui_log_message(f"Serial disconnected: {port_name}")
        elif self._serial_port is None:
             logger.debug("Close called, but serial port was already None.")
        else: # Port object exists but is not open
             logger.debug(f"Close called, but serial port {self._port_name} was already closed.")
             self._serial_port = None # Ensure it's set to None
