from unittest.mock import MagicMock, patch

from gnss_device import GnssDevice
from ntrip_client import NtripClient
from ntrip_connection_state import NtripConnectionState
from rtcm_parser import extract_rtcm_message_types
from rtk_constants import (
    FIX_QUALITY_RTK_FIXED,
    MAX_NTRIP_RETRIES,
    NTRIP_HEADER_SIZE_LIMIT,
    NTRIP_INITIAL_RECONNECT_TIMEOUT,
    SNR_THRESHOLD_BAD,
    SNR_THRESHOLD_GOOD,
)
from rtk_state import GnssState


class TestNtripConnectionState:
    def test_initial_state(self):
        state = NtripConnectionState()
        assert state.current_state == NtripConnectionState.DISCONNECTED
        assert state.reconnect_attempts == 0

    def test_set_state_connected(self):
        state = NtripConnectionState()
        changed = state.set_state(NtripConnectionState.CONNECTED, "OK")
        assert changed is True
        assert state.is_connected() is True
        assert state.status_message == "OK"

    def test_set_state_resets_attempts_on_connect(self):
        state = NtripConnectionState()
        state.reconnect_attempts = 3
        state.set_state(NtripConnectionState.CONNECTED, "OK")
        assert state.reconnect_attempts == 0

    def test_set_same_state_no_change(self):
        state = NtripConnectionState()
        changed = state.set_state(NtripConnectionState.DISCONNECTED)
        # Same state, no message change -> no state change
        assert changed is False

    def test_gave_up(self):
        state = NtripConnectionState()
        state.set_state(NtripConnectionState.GAVE_UP, "Max retries")
        assert state.has_given_up() is True
        assert state.is_disconnected() is True  # gave_up counts as disconnected

    def test_increment_reconnect(self):
        state = NtripConnectionState()
        result = state.increment_reconnect_attempts()
        assert result == 1
        result = state.increment_reconnect_attempts()
        assert result == 2

    def test_connection_age(self):
        state = NtripConnectionState()
        age = state.get_connection_age()
        assert age >= 0


class TestExtractRtcmMessageTypes:
    def test_empty_data(self):
        result = extract_rtcm_message_types(b'')
        assert result == []

    def test_too_short_data(self):
        result = extract_rtcm_message_types(b'\xD3\x00')
        assert result == []

    def test_valid_rtcm3_message(self):
        # Construct a minimal RTCM3 frame:
        # Preamble: 0xD3
        # Length: 4 bytes payload (0x00, 0x04)
        # Payload: message type 1077 = 0x435 -> bytes: 0x43, 0x50 (12 bits: 0100 0011 0101 -> 0x43 << 4 | 0x5 = 1077 check)
        # Actually message type encoding: (data[3] << 4) | (data[4] >> 4)
        # For type 1077 (0x435): data[3] = 0x43, data[4] = 0x50 (upper 4 bits = 5)
        # Then 2 more payload bytes + 3 CRC bytes
        payload = bytes([0x43, 0x50, 0x00, 0x00])
        length = len(payload)
        header = bytes([0xD3, (length >> 8) & 0x03, length & 0xFF])
        crc = bytes([0x00, 0x00, 0x00])  # Dummy CRC
        data = header + payload + crc
        result = extract_rtcm_message_types(data)
        assert 1077 in result

    def test_no_preamble(self):
        data = bytes([0x00, 0x00, 0x04, 0x43, 0x50, 0x00, 0x00, 0x00, 0x00, 0x00])
        result = extract_rtcm_message_types(data)
        assert result == []


class TestCalculateChecksum:
    """Tests NMEA checksum calculation using GnssDevice's static method
    (same algorithm as NtripClient's instance method)."""

    def test_simple_sentence(self):
        result = GnssDevice._calculate_checksum("GNGGA,123456.00,4006.56,N,00709.27,W,1,08,1.0,100.0,M,-0.0,M,,")
        assert len(result) == 2
        assert all(c in '0123456789ABCDEF' for c in result)

    def test_strips_dollar_sign(self):
        r1 = GnssDevice._calculate_checksum("$GPGGA,data")
        r2 = GnssDevice._calculate_checksum("GPGGA,data")
        assert r1 == r2

    def test_strips_after_asterisk(self):
        r1 = GnssDevice._calculate_checksum("GPGGA,data*XX")
        r2 = GnssDevice._calculate_checksum("GPGGA,data")
        assert r1 == r2

    def test_known_checksum(self):
        sentence = "PAIR062,0,1"
        expected = 0
        for c in sentence:
            expected ^= ord(c)
        result = GnssDevice._calculate_checksum(sentence)
        assert result == f"{expected:02X}"


class TestConstants:
    def test_snr_thresholds_defined(self):
        assert SNR_THRESHOLD_GOOD == 35
        assert SNR_THRESHOLD_BAD == 20

    def test_header_size_limit(self):
        assert NTRIP_HEADER_SIZE_LIMIT == 8192


class TestNtripConnectionStateTransitions:
    def test_connecting_state(self):
        state = NtripConnectionState()
        state.set_state(NtripConnectionState.CONNECTING, "Connecting...")
        assert state.is_connecting() is True
        assert state.is_connected() is False

    def test_full_lifecycle(self):
        state = NtripConnectionState()
        # disconnected -> connecting -> connected -> disconnected -> gave_up
        state.set_state(NtripConnectionState.CONNECTING)
        assert state.is_connecting()
        state.set_state(NtripConnectionState.CONNECTED, "OK")
        assert state.is_connected()
        assert state.reconnect_attempts == 0
        state.set_state(NtripConnectionState.DISCONNECTED, "Lost")
        assert state.is_disconnected()
        state.increment_reconnect_attempts()
        state.set_state(NtripConnectionState.GAVE_UP, "Max retries")
        assert state.has_given_up()



def _make_config(**overrides):
    """Create a mock Config object with sensible defaults for tests."""
    cfg = MagicMock()
    cfg.ntrip_server = overrides.get("ntrip_server", "test.server.com")
    cfg.ntrip_port = overrides.get("ntrip_port", 2101)
    cfg.ntrip_mountpoint = overrides.get("ntrip_mountpoint", "TEST")
    cfg.ntrip_username = overrides.get("ntrip_username", "user")
    cfg.ntrip_password = overrides.get("ntrip_password", "pass")
    cfg.ntrip_tls = overrides.get("ntrip_tls", False)
    cfg.default_lat = overrides.get("default_lat", 40.0)
    cfg.default_lon = overrides.get("default_lon", -7.0)
    cfg.default_alt = overrides.get("default_alt", 100.0)
    return cfg


def _make_client(**config_overrides):
    """Create an NtripClient with mocked dependencies."""
    config = _make_config(**config_overrides)
    state = GnssState(0.0, 0.0, 0.0)
    gnss_device = MagicMock()
    client = NtripClient(config, state, gnss_device)
    return client, config, state, gnss_device


# ==========================================================================
# 1. TestNtripClientInit
# ==========================================================================
class TestNtripClientInit:
    def test_initial_socket_and_running_and_state(self):
        client, _, _, _ = _make_client()
        assert client._socket is None
        assert not client._running.is_set()
        assert client._connection_state.current_state == NtripConnectionState.DISCONNECTED

    def test_stats_dict_initialized(self):
        client, _, _, _ = _make_client()
        stats = client._stats
        assert stats["total_bytes_received"] == 0
        assert stats["last_data_time"] is None
        assert stats["rtcm_message_counter"] == 0
        assert stats["data_rates"] == []
        assert stats["rtcm_message_types"] == []
        assert stats["last_rtcm_data"] is None

    def test_thread_is_none_initially(self):
        client, _, _, _ = _make_client()
        assert client._thread is None


# ==========================================================================
# 2. TestCreateGgaSentence
# ==========================================================================
class TestCreateGgaSentence:
    def test_with_position_lock_correct_lat_lon(self):
        """When the GNSS state has a position lock the GGA sentence should
        contain the actual position with correct N/S and E/W directions."""
        client, config, state, _ = _make_client(default_lat=38.5, default_lon=-9.1, default_alt=50.0)
        # Simulate a position lock
        state.update(
            have_position_lock=True,
            fix_type=FIX_QUALITY_RTK_FIXED,
            position={"lat": 41.15, "lon": 8.61, "alt": 120.0},
            num_satellites_used=12,
            hdop=0.8,
        )
        gga = client._create_gga_sentence()
        assert gga is not None
        # Should use actual position (41.15 N, 8.61 E) instead of defaults
        assert ",N," in gga
        assert ",E," in gga
        # Fix quality should be 4 (RTK fixed)
        parts = gga.split(",")
        # fix_quality is field index 6 (0-based) in the comma-separated body
        assert parts[6] == "4"

    def test_without_position_lock_uses_defaults(self):
        client, config, state, _ = _make_client(default_lat=40.0, default_lon=-7.0, default_alt=100.0)
        # No position lock
        state.update(have_position_lock=False)
        gga = client._create_gga_sentence()
        assert gga is not None
        # Should use config defaults -> lat 40.0 N, lon 7.0 W
        assert ",N," in gga
        assert ",W," in gga
        parts = gga.split(",")
        assert parts[6] == "0"  # FIX_QUALITY_INVALID

    def test_checksum_is_valid_two_char_hex(self):
        client, _, _, _ = _make_client()
        gga = client._create_gga_sentence()
        assert gga is not None
        # Extract checksum after '*'
        star_idx = gga.index("*")
        checksum = gga[star_idx + 1 : star_idx + 3]
        assert len(checksum) == 2
        assert all(c in "0123456789ABCDEF" for c in checksum)

    def test_sentence_starts_and_ends_correctly(self):
        client, _, _, _ = _make_client()
        gga = client._create_gga_sentence()
        assert gga is not None
        assert gga.startswith("$GNGGA")
        assert gga.endswith("\r\n")

    def test_negative_longitude_produces_west(self):
        client, _, state, _ = _make_client(default_lat=40.0, default_lon=-7.5, default_alt=100.0)
        # No position lock, so defaults are used
        gga = client._create_gga_sentence()
        assert gga is not None
        assert ",W," in gga

    def test_positive_longitude_produces_east(self):
        client, _, state, _ = _make_client(default_lat=40.0, default_lon=7.5, default_alt=100.0)
        gga = client._create_gga_sentence()
        assert gga is not None
        assert ",E," in gga

    def test_negative_latitude_produces_south(self):
        client, _, state, _ = _make_client(default_lat=-33.9, default_lon=18.4, default_alt=0.0)
        gga = client._create_gga_sentence()
        assert gga is not None
        assert ",S," in gga


# ==========================================================================
# 3. TestCalculateChecksumNtrip
# ==========================================================================
class TestCalculateChecksumNtrip:
    def test_known_sentence(self):
        client, _, _, _ = _make_client()
        sentence = "GNGGA,123456.00,4006.00,N,00700.00,W,1,08,1.00,100.0,M,-0.0,M,,"
        expected = 0
        for c in sentence:
            expected ^= ord(c)
        result = client._calculate_checksum(sentence)
        assert result == f"{expected:02X}"

    def test_strips_dollar_prefix(self):
        client, _, _, _ = _make_client()
        r1 = client._calculate_checksum("$GPGGA,data")
        r2 = client._calculate_checksum("GPGGA,data")
        assert r1 == r2

    def test_strips_content_after_asterisk(self):
        client, _, _, _ = _make_client()
        r1 = client._calculate_checksum("GPGGA,data*XX")
        r2 = client._calculate_checksum("GPGGA,data")
        assert r1 == r2


# ==========================================================================
# 4. TestSendGga
# ==========================================================================
class TestSendGga:
    def test_successful_send(self):
        """When connected with a valid socket, sendall should be called."""
        client, _, state, _ = _make_client()
        mock_socket = MagicMock()
        client._socket = mock_socket
        client._connection_state.set_state(NtripConnectionState.CONNECTED, "OK")

        client._send_gga()

        mock_socket.sendall.assert_called_once()
        sent_data = mock_socket.sendall.call_args[0][0]
        assert isinstance(sent_data, bytes)
        assert sent_data.startswith(b"$GNGGA")

    def test_socket_error_disconnects(self):
        """On OSError the client should disconnect and increment error count."""
        client, _, state, _ = _make_client()
        mock_socket = MagicMock()
        mock_socket.sendall.side_effect = OSError("send failed")
        client._socket = mock_socket
        client._connection_state.set_state(NtripConnectionState.CONNECTED, "OK")

        initial_errors = state.ntrip_error_count
        client._send_gga()

        # Should have transitioned away from CONNECTED
        assert not client._connection_state.is_connected()
        assert state.ntrip_error_count == initial_errors + 1

    def test_no_socket_no_crash(self):
        """When there is no socket _send_gga should return silently."""
        client, _, _, _ = _make_client()
        client._socket = None
        # Should not raise
        client._send_gga()

    def test_not_connected_state_no_send(self):
        """When socket exists but state is not CONNECTED, sendall must not be called."""
        client, _, _, _ = _make_client()
        mock_socket = MagicMock()
        client._socket = mock_socket
        # State is DISCONNECTED by default
        client._send_gga()
        mock_socket.sendall.assert_not_called()


# ==========================================================================
# 5. TestHandleRtcmData
# ==========================================================================
class TestHandleRtcmData:
    def test_valid_rtcm_forwarded(self):
        """Valid data should be forwarded via gnss_device.write_data."""
        client, _, state, gnss_device = _make_client()
        data = b"\xD3\x00\x04\x43\x50\x00\x00" + b"\x00" * 3
        gnss_device.write_data.return_value = len(data)

        client._handle_rtcm_data(data)

        gnss_device.write_data.assert_called_once_with(data)

    def test_stats_updated(self):
        """total_bytes_received should be incremented after successful write."""
        client, _, state, gnss_device = _make_client()
        data = b"\xAB" * 50
        gnss_device.write_data.return_value = len(data)

        assert client._stats["total_bytes_received"] == 0
        client._handle_rtcm_data(data)
        assert client._stats["total_bytes_received"] == len(data)
        assert client._stats["last_data_time"] is not None
        assert client._stats["rtcm_message_counter"] == 1

    def test_empty_data_early_return(self):
        """Empty data should cause an early return without calling write_data."""
        client, _, _, gnss_device = _make_client()
        client._handle_rtcm_data(b"")
        gnss_device.write_data.assert_not_called()

    def test_write_data_returns_none_no_crash(self):
        """If write_data returns None the method should log but not crash."""
        client, _, _, gnss_device = _make_client()
        gnss_device.write_data.return_value = None
        data = b"\xAB" * 10

        # Should not raise
        client._handle_rtcm_data(data)
        # Stats should NOT be updated because bytes_sent is None
        assert client._stats["total_bytes_received"] == 0

    def test_multiple_calls_accumulate_stats(self):
        """Successive calls should accumulate bytes and counter."""
        client, _, _, gnss_device = _make_client()
        data1 = b"\x01" * 30
        data2 = b"\x02" * 70
        gnss_device.write_data.side_effect = [len(data1), len(data2)]

        client._handle_rtcm_data(data1)
        client._handle_rtcm_data(data2)

        assert client._stats["total_bytes_received"] == 100
        assert client._stats["rtcm_message_counter"] == 2


# ==========================================================================
# 6. TestCheckRetryLimit
# ==========================================================================
class TestCheckRetryLimit:
    def test_under_limit_returns_false(self):
        client, _, _, _ = _make_client()
        # Default reconnect_attempts is 0, which is < MAX_NTRIP_RETRIES (5)
        assert client._check_retry_limit() is False

    def test_at_limit_returns_true_and_gave_up(self):
        client, _, _, _ = _make_client()
        # Set attempts to the limit
        client._connection_state.reconnect_attempts = MAX_NTRIP_RETRIES
        result = client._check_retry_limit()
        assert result is True
        assert client._connection_state.has_given_up() is True

    def test_already_gave_up_returns_true_no_duplicate(self):
        client, _, _, _ = _make_client()
        client._connection_state.reconnect_attempts = MAX_NTRIP_RETRIES
        # First call triggers gave up
        client._check_retry_limit()
        assert client._connection_state.has_given_up()
        # Second call should still return True but not crash or duplicate
        result = client._check_retry_limit()
        assert result is True
        assert client._connection_state.has_given_up()

    def test_one_below_limit_returns_false(self):
        client, _, _, _ = _make_client()
        client._connection_state.reconnect_attempts = MAX_NTRIP_RETRIES - 1
        assert client._check_retry_limit() is False


# ==========================================================================
# 7. TestResetConnection
# ==========================================================================
class TestResetConnection:
    def test_when_running_resets_state(self):
        client, _, _, _ = _make_client()
        client._running.set()
        # Simulate some prior state
        client._connection_state.reconnect_attempts = 3
        client._connection_state.set_state(NtripConnectionState.CONNECTING, "Connecting...")
        mock_socket = MagicMock()
        client._socket = mock_socket

        result = client.reset_connection()

        assert result is True
        assert client._connection_state.reconnect_attempts == 0
        assert client._connection_state.current_state == NtripConnectionState.DISCONNECTED
        assert client._reconnect_timeout == NTRIP_INITIAL_RECONNECT_TIMEOUT
        assert client._next_reconnect_time is None

    def test_when_not_running_returns_false(self):
        client, _, _, _ = _make_client()
        # _running is not set (default)
        result = client.reset_connection()
        assert result is False

    def test_gave_up_cleared_after_reset(self):
        client, _, _, _ = _make_client()
        client._running.set()
        # Simulate gave up state
        client._connection_state.set_state(NtripConnectionState.GAVE_UP, "Max retries")
        assert client._connection_state.has_given_up()

        result = client.reset_connection()

        assert result is True
        assert not client._connection_state.has_given_up()
        assert client._connection_state.current_state == NtripConnectionState.DISCONNECTED


# ==========================================================================
# 8. TestStartStop
# ==========================================================================
class TestStartStop:
    @patch("ntrip_client.threading.Thread")
    def test_start_sets_running_and_creates_thread(self, MockThread):
        client, _, _, _ = _make_client()
        mock_thread_instance = MagicMock()
        mock_thread_instance.is_alive.return_value = False
        MockThread.return_value = mock_thread_instance

        client.start()

        assert client._running.is_set()
        MockThread.assert_called_once()
        mock_thread_instance.start.assert_called_once()
        assert client._thread is mock_thread_instance

    @patch("ntrip_client.threading.Thread")
    def test_start_when_already_running_no_second_thread(self, MockThread):
        client, _, _, _ = _make_client()
        # Simulate already running
        mock_thread_instance = MagicMock()
        mock_thread_instance.is_alive.return_value = True
        client._thread = mock_thread_instance

        client.start()

        # Thread constructor should NOT have been called again
        MockThread.assert_not_called()

    def test_stop_clears_running(self):
        client, _, _, _ = _make_client()
        client._running.set()
        # Create a fake thread that is not alive so join doesn't block
        mock_thread = MagicMock()
        mock_thread.is_alive.return_value = False
        client._thread = mock_thread

        client.stop()

        assert not client._running.is_set()

    def test_stop_when_already_stopped_is_noop(self):
        client, _, _, _ = _make_client()
        # _running is not set by default
        # Should not raise
        client.stop()
        assert not client._running.is_set()

    @patch("ntrip_client.threading.Thread")
    def test_start_clears_gave_up_state(self, MockThread):
        """Starting when in GAVE_UP state should clear it."""
        client, _, _, _ = _make_client()
        mock_thread_instance = MagicMock()
        mock_thread_instance.is_alive.return_value = False
        MockThread.return_value = mock_thread_instance

        client._connection_state.set_state(NtripConnectionState.GAVE_UP, "Max retries")
        assert client._connection_state.has_given_up()

        client.start()

        assert not client._connection_state.has_given_up()
        assert client._connection_state.current_state == NtripConnectionState.DISCONNECTED
