import time

import pytest

from demo_ntrip import DemoNtripClient
from rtk_state import GnssState


@pytest.fixture
def state():
    return GnssState(0.0, 0.0, 0.0)


@pytest.fixture
def fast_client(state):
    """A DemoNtripClient with a very short connect delay for fast tests."""
    client = DemoNtripClient(state)
    client._connect_delay = 0.1
    return client


class TestDemoNtripClientStartStop:
    def test_start_makes_running(self, fast_client):
        fast_client.start()
        try:
            assert fast_client.is_running() is True
        finally:
            fast_client.stop()

    def test_stop_makes_not_running(self, fast_client):
        fast_client.start()
        assert fast_client.is_running() is True

        fast_client.stop()

        assert fast_client.is_running() is False

    def test_stop_when_not_started_is_noop(self, state):
        client = DemoNtripClient(state)

        client.stop()  # Should not raise

        assert client.is_running() is False


class TestDemoNtripClientConnection:
    def test_ntrip_connected_after_delay(self, fast_client, state):
        fast_client.start()
        try:
            # Wait for the connect delay (0.1s) plus a margin
            time.sleep(0.4)
            assert state.ntrip_connected is True
        finally:
            fast_client.stop()

    def test_stats_accumulate(self, fast_client, state):
        fast_client.start()
        try:
            # Wait for connect delay + at least one stats pump cycle
            time.sleep(1.5)
            assert state.ntrip_total_bytes > 0
            assert state.rtcm_message_counter > 0
        finally:
            fast_client.stop()


class TestDemoNtripClientReset:
    def test_reset_when_running_returns_true(self, fast_client):
        fast_client.start()
        try:
            result = fast_client.reset_connection()
            assert result is True
        finally:
            fast_client.stop()

    def test_reset_when_not_running_returns_false(self, state):
        client = DemoNtripClient(state)

        result = client.reset_connection()

        assert result is False
