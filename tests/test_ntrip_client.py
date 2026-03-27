from gnss_device import GnssDevice
from ntrip_client import NtripClient, NtripConnectionState
from rtk_constants import NTRIP_HEADER_SIZE_LIMIT, SNR_THRESHOLD_BAD, SNR_THRESHOLD_GOOD


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
        result = NtripClient._extract_rtcm_message_types(b'')
        assert result == []

    def test_too_short_data(self):
        result = NtripClient._extract_rtcm_message_types(b'\xD3\x00')
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
        result = NtripClient._extract_rtcm_message_types(data)
        assert 1077 in result

    def test_no_preamble(self):
        data = bytes([0x00, 0x00, 0x04, 0x43, 0x50, 0x00, 0x00, 0x00, 0x00, 0x00])
        result = NtripClient._extract_rtcm_message_types(data)
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
