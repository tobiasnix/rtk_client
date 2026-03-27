import threading

from rtk_state import GnssState


class TestGnssStateInit:
    def test_default_position(self):
        state = GnssState(40.0, -7.0, 100.0)
        assert state.default_lat == 40.0
        assert state.default_lon == -7.0
        assert state.default_alt == 100.0

    def test_initial_values(self):
        state = GnssState(0.0, 0.0, 0.0)
        assert state.fix_type == 0
        assert state.num_satellites_used == 0
        assert state.ntrip_connected is False
        assert state.gps_error_count == 0
        assert state.ntrip_error_count == 0
        assert state.have_position_lock is False


class TestGnssStateUpdate:
    def test_update_single_field(self):
        state = GnssState(0.0, 0.0, 0.0)
        state.update(fix_type=4)
        assert state.fix_type == 4

    def test_update_multiple_fields(self):
        state = GnssState(0.0, 0.0, 0.0)
        state.update(fix_type=5, num_satellites_used=12, ntrip_connected=True)
        assert state.fix_type == 5
        assert state.num_satellites_used == 12
        assert state.ntrip_connected is True

    def test_update_nonexistent_key_ignored(self):
        state = GnssState(0.0, 0.0, 0.0)
        # Should not raise, just log a warning
        state.update(nonexistent_field=42)
        assert not hasattr(state, 'nonexistent_field')


class TestGetStateSnapshot:
    def test_snapshot_returns_dict(self):
        state = GnssState(0.0, 0.0, 0.0)
        snapshot = state.get_state_snapshot()
        assert isinstance(snapshot, dict)

    def test_snapshot_excludes_lock(self):
        state = GnssState(0.0, 0.0, 0.0)
        snapshot = state.get_state_snapshot()
        assert '_lock' not in snapshot

    def test_snapshot_is_deep_copy(self):
        state = GnssState(0.0, 0.0, 0.0)
        state.update(position={"lat": 1.0, "lon": 2.0, "alt": 3.0})
        snapshot = state.get_state_snapshot()

        # Modify the snapshot's position dict
        snapshot['position']['lat'] = 99.0

        # Original state should be unchanged
        assert state.position['lat'] == 1.0

    def test_snapshot_satellites_deep_copy(self):
        state = GnssState(0.0, 0.0, 0.0)
        state.update(satellites_info={
            "GP-1": {"prn": "1", "snr": 40, "active": True}
        })
        snapshot = state.get_state_snapshot()

        # Modify the snapshot
        snapshot['satellites_info']['GP-1']['snr'] = 0

        # Original should be unchanged
        assert state.satellites_info['GP-1']['snr'] == 40


class TestAddUiLogMessage:
    def test_adds_message_with_timestamp(self):
        state = GnssState(0.0, 0.0, 0.0)
        state.add_ui_log_message("Test message")
        assert len(state.ui_log_messages) == 1
        assert "Test message" in state.ui_log_messages[0]

    def test_truncates_long_messages(self):
        state = GnssState(0.0, 0.0, 0.0)
        long_msg = "A" * 200
        state.add_ui_log_message(long_msg)
        assert len(state.ui_log_messages[0]) <= 70

    def test_buffer_limit(self):
        state = GnssState(0.0, 0.0, 0.0)
        for i in range(150):
            state.add_ui_log_message(f"Message {i}")
        assert len(state.ui_log_messages) == 100  # MAX_LOG_MESSAGES


class TestIncrementErrorCount:
    def test_increment_gps_error(self):
        state = GnssState(0.0, 0.0, 0.0)
        state.increment_error_count("gps")
        assert state.gps_error_count == 1
        state.increment_error_count("gps")
        assert state.gps_error_count == 2

    def test_increment_ntrip_error(self):
        state = GnssState(0.0, 0.0, 0.0)
        state.increment_error_count("ntrip")
        assert state.ntrip_error_count == 1

    def test_unknown_error_type(self):
        state = GnssState(0.0, 0.0, 0.0)
        state.increment_error_count("unknown")
        assert state.gps_error_count == 0
        assert state.ntrip_error_count == 0


class TestNtripState:
    def test_set_ntrip_connected(self):
        state = GnssState(0.0, 0.0, 0.0)
        state.set_ntrip_connected(True, "Connected")
        assert state.ntrip_connected is True
        assert state.ntrip_status_message == "Connected"

    def test_set_ntrip_disconnected(self):
        state = GnssState(0.0, 0.0, 0.0)
        state.set_ntrip_connected(True, "Connected")
        state.set_ntrip_connected(False, "Lost connection")
        assert state.ntrip_connected is False

    def test_increment_ntrip_reconnects(self):
        state = GnssState(0.0, 0.0, 0.0)
        result = state.increment_ntrip_reconnects()
        assert result == 1
        result = state.increment_ntrip_reconnects()
        assert result == 2

    def test_reset_ntrip_reconnects(self):
        state = GnssState(0.0, 0.0, 0.0)
        state.increment_ntrip_reconnects()
        state.increment_ntrip_reconnects()
        state.reset_ntrip_reconnects()
        assert state.ntrip_reconnect_attempts == 0

    def test_set_ntrip_gave_up(self):
        state = GnssState(0.0, 0.0, 0.0)
        state.set_ntrip_gave_up(True, "Max retries")
        assert state.ntrip_connection_gave_up is True
        assert state.ntrip_status_message == "Max retries"


class TestThreadSafety:
    def test_concurrent_updates(self):
        state = GnssState(0.0, 0.0, 0.0)
        errors = []

        def updater(n):
            try:
                for _ in range(100):
                    state.update(num_satellites_used=n)
                    state.get_state_snapshot()
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=updater, args=(i,)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
