# rtk_controller.py - Orchestrates the RTK client components

import logging
import threading
import time
from typing import Any, Dict, Optional

from gnss_device import GnssDevice
from nmea_parser import NmeaParser
from ntrip_client import NtripClient

# Import required components from other modules
from rtk_config import Config
from rtk_state import GnssState

# Note: StatusDisplay is not directly used by the controller, but by main.py

logger = logging.getLogger(__name__)

class RtkController:
    """Orchestrates the GNSS device, NMEA parser, NTRIP client."""
    def __init__(self, config: Config):
        self._config = config
        # Initialize state first, as other components depend on it
        self._state = GnssState(config.default_lat, config.default_lon, config.default_alt)
        # Initialize components, passing the state and other dependencies
        self._gnss_device = GnssDevice(config.serial_port, config.baud_rate, self._state)
        self._nmea_parser = NmeaParser(self._state)
        self._ntrip_client = NtripClient(config, self._state, self._gnss_device)
        # Main running flag for the application
        self._running = threading.Event()
        # Placeholder for the GNSS reading thread
        self._gnss_read_thread: Optional[threading.Thread] = None

    def _read_gnss_data_loop(self):
        """Thread loop to continuously read and parse data from GNSS device."""
        logger.info("GNSS data reading loop started.")
        while self._running.is_set():
            if not self._gnss_device.is_connected():
                 logger.warning("GNSS device disconnected. Attempting reconnect in 5s...")
                 # Use event wait for better shutdown responsiveness
                 self._running.wait(timeout=5.0)
                 if not self._running.is_set(): break # Exit if stopped during sleep
                 if not self._gnss_device.connect():
                      continue # Try again after next loop iteration
                 else:
                      logger.info("Reconnected to GNSS device.")
                      # Optional: Re-configure module after reconnect?
                      # self._gnss_device.configure_module()

            # Read line from device
            line = self._gnss_device.read_line()

            if line: # Process if line is not empty
                self._nmea_parser.parse(line)
            elif line is None: # Indicates serial error/port closed
                 logger.warning("GNSS read loop detected closed/error state. Will attempt reconnect.")
                 self._running.wait(timeout=2.0) # Wait before next connection attempt

            # Small sleep to prevent 100% CPU usage and allow other threads to run
            time.sleep(0.005) # 5 milliseconds

        logger.info("GNSS data reading loop finished.")

    def start(self) -> bool:
        """Initializes components and starts worker threads."""
        logger.info("Starting RTK Controller components...")
        self._state.add_ui_log_message("System starting...")

        # Attempt to connect to the GNSS device
        if not self._gnss_device.connect():
             logger.critical("Failed to connect to GNSS device on startup.")
             self._state.add_ui_log_message("FATAL: Cannot connect to GNSS device!")
             return False # Indicate failure

        # Configure the module after successful connection
        self._gnss_device.configure_module()

        self._running.set() # Set running flag before starting threads

        # Start GNSS reading thread
        self._gnss_read_thread = threading.Thread(target=self._read_gnss_data_loop, name="GnssReadThread", daemon=True)
        self._gnss_read_thread.start()
        if not self._gnss_read_thread.is_alive():
            logger.critical("Failed to start GNSS reading thread.")
            self._state.add_ui_log_message("FATAL: Failed to start GNSS thread!")
            self._running.clear()
            return False

        # Start NTRIP client thread
        self._ntrip_client.start()
        # Check if NTRIP thread started (optional, basic check)
        time.sleep(0.1) # Give thread a moment to start
        if not self._ntrip_client.is_running():
             logger.critical("Failed to start NTRIP client thread.")
             self._state.add_ui_log_message("FATAL: Failed to start NTRIP thread!")
             self._running.clear() # Signal read thread to stop
             self._gnss_device.close() # Close serial port
             return False


        # Status display thread is handled by the main curses loop in main.py

        logger.info("Worker threads started.")
        self._state.add_ui_log_message("System running. Press 'q' to quit.")
        return True # Indicate success

    def stop(self) -> None:
        """Stops all components and threads gracefully."""
        if not self._running.is_set():
            logger.info("RTK Controller already stopped or not started.")
            return

        logger.info("Stopping RTK Controller components...")
        self._state.add_ui_log_message("System shutting down...")
        self._running.clear() # Signal all loops to stop

        # Stop NTRIP client first (it might be writing to GNSS device)
        self._ntrip_client.stop()

        # GNSS read thread is daemon, will exit when main thread exits,
        # but closing the device helps unblock it faster.
        self._gnss_device.close()

        # Optional: Explicitly join non-daemon threads if they existed

        logger.info("RTK Controller components stopped.")

    def get_current_state(self) -> Dict[str, Any]:
        """Provides safe access to the current state snapshot."""
        return self._state.get_state_snapshot()

    @property
    def is_running(self) -> bool:
        """Indicates if the controller's main running flag is set."""
        return self._running.is_set()

    def reset_ntrip_connection(self) -> bool:
        """Resets the NTRIP connection via the client."""
        return self._ntrip_client.reset_connection()

    @property
    def state(self) -> GnssState:
       return self._state
