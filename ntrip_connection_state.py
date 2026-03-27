"""NTRIP connection state machine with proper state transitions."""

from datetime import datetime, timezone


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
