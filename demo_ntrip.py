# demo_ntrip.py - Simulates NTRIP connection with fake RTCM stats

import logging
import random
import threading
from datetime import datetime, timezone
from typing import Optional

from rtk_state import GnssState

logger = logging.getLogger(__name__)

# Typical RTCM message types from a real caster
_DEMO_RTCM_TYPES = [1077, 1087, 1097, 1127, 1005]


class DemoNtripClient:
    """Simulates an NTRIP connection for demo mode.

    Implements the same interface as NtripClient so the controller
    can use it as a drop-in replacement when --demo is active.
    """

    def __init__(self, state: GnssState):
        self._state = state
        self._running = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._connect_delay = 3.0  # seconds before "connecting"

    def start(self) -> None:
        """Starts the simulated NTRIP connection thread."""
        if self.is_running():
            return
        self._running.set()
        self._thread = threading.Thread(target=self._run, name="DemoNtripThread", daemon=True)
        self._thread.start()
        logger.info("Demo NTRIP client started.")

    def stop(self) -> None:
        """Stops the simulation thread."""
        if not self._running.is_set():
            return
        self._running.clear()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5.0)
        self._state.set_ntrip_connected(False, "Demo stopped")
        logger.info("Demo NTRIP client stopped.")

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def reset_connection(self) -> bool:
        """Simulates a connection reset."""
        if not self._running.is_set():
            return False
        self._state.set_ntrip_connected(False, "Demo: resetting...")
        self._state.add_ui_log_message("Demo NTRIP: reset requested")
        logger.info("Demo NTRIP connection reset.")
        return True

    def _run(self) -> None:
        """Main simulation loop."""
        logger.info("Demo NTRIP thread running.")

        # Phase 1: Simulate connection delay
        self._state.update(ntrip_status_message="Demo: Connecting...")
        self._state.add_ui_log_message("Demo NTRIP: connecting...")

        if not self._running.wait(timeout=self._connect_delay):
            return  # Stopped during connect delay

        if not self._running.is_set():
            return

        # Phase 2: Connected
        self._state.set_ntrip_connected(True, "Demo: Connected")
        self._state.add_ui_log_message("Demo NTRIP: connected")
        logger.info("Demo NTRIP simulated connection established.")

        total_bytes = 0
        msg_counter = 0
        type_idx = 0

        # Phase 3: Pump fake stats
        while self._running.is_set():
            self._running.wait(timeout=1.0)
            if not self._running.is_set():
                break

            # Simulate receiving RTCM data
            chunk_size = random.randint(150, 300)
            total_bytes += chunk_size
            msg_counter += 1
            rtcm_type = _DEMO_RTCM_TYPES[type_idx % len(_DEMO_RTCM_TYPES)]
            type_idx += 1

            now = datetime.now(timezone.utc)

            self._state.update(
                ntrip_total_bytes=total_bytes,
                ntrip_last_data_time=now,
                rtcm_message_counter=msg_counter,
            )

            # Update deques under lock
            with self._state._lock:
                self._state.ntrip_data_rates.append(chunk_size)
                self._state.last_rtcm_message_types.append(rtcm_type)

        logger.info("Demo NTRIP thread exiting.")
