"""Dedicated edge-case tests for NtripConnectionState."""

import time

from ntrip_connection_state import NtripConnectionState


class TestSetStateEdgeCases:
    def test_same_state_different_message_updates(self):
        """Same state but a different message should update timestamp and message,
        though set_state returns False (state itself did not change)."""
        state = NtripConnectionState()
        # Move to CONNECTING first
        state.set_state(NtripConnectionState.CONNECTING, "Attempt 1")
        old_timestamp = state.last_state_change

        result = state.set_state(NtripConnectionState.CONNECTING, "Attempt 2")

        # State enum did not change, so return value is False
        assert result is False
        # But message and timestamp should be updated
        assert state.status_message == "Attempt 2"
        assert state.last_state_change >= old_timestamp

    def test_same_state_same_message_returns_false(self):
        """Exact same state and message should return False and change nothing."""
        state = NtripConnectionState()
        state.set_state(NtripConnectionState.CONNECTING, "Waiting")
        old_timestamp = state.last_state_change

        result = state.set_state(NtripConnectionState.CONNECTING, "Waiting")

        assert result is False
        # Timestamp should NOT have been updated since nothing changed
        assert state.last_state_change == old_timestamp

    def test_connected_resets_attempts(self):
        """Setting state to CONNECTED should reset reconnect_attempts to 0."""
        state = NtripConnectionState()
        state.reconnect_attempts = 5
        state.set_state(NtripConnectionState.CONNECTING, "Trying")

        state.set_state(NtripConnectionState.CONNECTED, "OK")

        assert state.reconnect_attempts == 0

    def test_disconnected_transition_resets_attempts(self):
        """Transitioning TO DISCONNECTED from another state resets attempts."""
        state = NtripConnectionState()
        # Start from CONNECTED so transition to DISCONNECTED is a real change
        state.set_state(NtripConnectionState.CONNECTED, "OK")
        state.reconnect_attempts = 3

        state.set_state(NtripConnectionState.DISCONNECTED, "Lost connection")

        assert state.reconnect_attempts == 0

    def test_disconnected_same_state_does_not_reset(self):
        """Already DISCONNECTED, setting DISCONNECTED again with a different
        message does NOT reset attempts (state_changed is False)."""
        state = NtripConnectionState()
        # Default state is DISCONNECTED; change message to make it enter the if-block
        state.set_state(NtripConnectionState.DISCONNECTED, "Initial")
        state.reconnect_attempts = 4

        state.set_state(NtripConnectionState.DISCONNECTED, "Different message")

        # reconnect_attempts should NOT have been reset because state_changed is False
        assert state.reconnect_attempts == 4


class TestStateQueryMethods:
    def test_gave_up_is_also_disconnected(self):
        """GAVE_UP state should return True for is_disconnected()."""
        state = NtripConnectionState()
        state.set_state(NtripConnectionState.GAVE_UP, "Max retries reached")

        assert state.has_given_up() is True
        assert state.is_disconnected() is True
        assert state.is_connected() is False
        assert state.is_connecting() is False

    def test_connection_age_increases(self):
        """After a brief sleep, get_connection_age() should return > 0."""
        state = NtripConnectionState()
        state.set_state(NtripConnectionState.CONNECTED, "OK")

        time.sleep(0.05)  # 50 ms
        age = state.get_connection_age()

        assert age > 0
        assert age < 5.0  # Sanity upper bound

    def test_increment_is_cumulative(self):
        """Multiple calls to increment_reconnect_attempts should accumulate."""
        state = NtripConnectionState()

        assert state.reconnect_attempts == 0
        assert state.increment_reconnect_attempts() == 1
        assert state.increment_reconnect_attempts() == 2
        assert state.increment_reconnect_attempts() == 3
        assert state.reconnect_attempts == 3
