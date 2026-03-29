"""Comprehensive tests for the RtkController class."""

from unittest.mock import MagicMock, patch

from rtk_controller import RtkController


def _make_config():
    """Create a mock Config object with sensible defaults."""
    config = MagicMock()
    config.default_lat = 0.0
    config.default_lon = 0.0
    config.default_alt = 0.0
    config.serial_port = "/dev/test"
    config.baud_rate = 115200
    config.gnss_module = "generic"
    config.position_log = None
    config.position_log_interval = 5.0
    config.demo = False
    return config


def _build_controller(
    mock_get_profile,
    mock_gnss_cls,
    mock_parser_cls,
    mock_ntrip_cls,
    mock_load,
    mock_save,
    config=None,
):
    """Instantiate an RtkController with all heavy dependencies mocked out."""
    mock_profile = MagicMock()
    mock_profile.display_name = "Test Module"
    mock_get_profile.return_value = mock_profile

    if config is None:
        config = _make_config()

    controller = RtkController(config)

    # Convenience references to the mock *instances* created inside __init__
    gnss_device = mock_gnss_cls.return_value
    nmea_parser = mock_parser_cls.return_value
    ntrip_client = mock_ntrip_cls.return_value

    return controller, config, gnss_device, nmea_parser, ntrip_client


# Decorator stack shared by almost every test.  Innermost @patch maps to the
# first positional arg after ``self`` (mock_get_profile).
_PATCH_STACK = [
    patch("rtk_controller.save_state"),
    patch("rtk_controller.load_state", return_value=None),
    patch("rtk_controller.NtripClient"),
    patch("rtk_controller.NmeaParser"),
    patch("rtk_controller.GnssDevice"),
    patch("rtk_controller.get_profile"),
]


def _apply_patches(func):
    """Apply the common patch stack to a test function."""
    for p in _PATCH_STACK:
        func = p(func)
    return func


# ==========================================================================
# 1. TestRtkControllerStart
# ==========================================================================
class TestRtkControllerStart:
    @patch("rtk_controller.save_state")
    @patch("rtk_controller.load_state", return_value=None)
    @patch("rtk_controller.NtripClient")
    @patch("rtk_controller.NmeaParser")
    @patch("rtk_controller.GnssDevice")
    @patch("rtk_controller.get_profile")
    def test_start_success(
        self,
        mock_get_profile,
        mock_gnss_cls,
        mock_parser_cls,
        mock_ntrip_cls,
        mock_load,
        mock_save,
    ):
        controller, _, gnss_device, _, ntrip_client = _build_controller(
            mock_get_profile, mock_gnss_cls, mock_parser_cls,
            mock_ntrip_cls, mock_load, mock_save,
        )
        gnss_device.connect.return_value = True
        ntrip_client.is_running.return_value = True

        result = controller.start()

        assert result is True
        assert controller.is_running is True
        gnss_device.connect.assert_called_once()
        gnss_device.configure_module.assert_called_once()
        ntrip_client.start.assert_called_once()

    @patch("rtk_controller.save_state")
    @patch("rtk_controller.load_state", return_value=None)
    @patch("rtk_controller.NtripClient")
    @patch("rtk_controller.NmeaParser")
    @patch("rtk_controller.GnssDevice")
    @patch("rtk_controller.get_profile")
    def test_start_gnss_connect_fails(
        self,
        mock_get_profile,
        mock_gnss_cls,
        mock_parser_cls,
        mock_ntrip_cls,
        mock_load,
        mock_save,
    ):
        controller, _, gnss_device, _, _ = _build_controller(
            mock_get_profile, mock_gnss_cls, mock_parser_cls,
            mock_ntrip_cls, mock_load, mock_save,
        )
        gnss_device.connect.return_value = False

        result = controller.start()

        assert result is False
        assert controller.is_running is False

    @patch("rtk_controller.PositionLogger")
    @patch("rtk_controller.save_state")
    @patch("rtk_controller.load_state", return_value=None)
    @patch("rtk_controller.NtripClient")
    @patch("rtk_controller.NmeaParser")
    @patch("rtk_controller.GnssDevice")
    @patch("rtk_controller.get_profile")
    def test_start_with_position_logger(
        self,
        mock_get_profile,
        mock_gnss_cls,
        mock_parser_cls,
        mock_ntrip_cls,
        mock_load,
        mock_save,
        mock_pos_logger_cls,
    ):
        config = _make_config()
        config.position_log = "/tmp/test_positions.csv"
        config.position_log_interval = 2.0

        controller, _, gnss_device, _, ntrip_client = _build_controller(
            mock_get_profile, mock_gnss_cls, mock_parser_cls,
            mock_ntrip_cls, mock_load, mock_save, config=config,
        )
        gnss_device.connect.return_value = True
        ntrip_client.is_running.return_value = True

        result = controller.start()

        assert result is True
        mock_pos_logger_cls.assert_called_once()
        mock_pos_logger_cls.return_value.start.assert_called_once()

    @patch("rtk_controller.save_state")
    @patch("rtk_controller.load_state", return_value=None)
    @patch("rtk_controller.NtripClient")
    @patch("rtk_controller.NmeaParser")
    @patch("rtk_controller.GnssDevice")
    @patch("rtk_controller.get_profile")
    def test_start_ntrip_thread_failure(
        self,
        mock_get_profile,
        mock_gnss_cls,
        mock_parser_cls,
        mock_ntrip_cls,
        mock_load,
        mock_save,
    ):
        controller, _, gnss_device, _, ntrip_client = _build_controller(
            mock_get_profile, mock_gnss_cls, mock_parser_cls,
            mock_ntrip_cls, mock_load, mock_save,
        )
        gnss_device.connect.return_value = True
        # NTRIP fails to start
        ntrip_client.is_running.return_value = False

        result = controller.start()

        assert result is False
        # Cleanup should have occurred
        assert controller.is_running is False
        gnss_device.close.assert_called_once()


# ==========================================================================
# 2. TestRtkControllerStop
# ==========================================================================
class TestRtkControllerStop:
    @patch("rtk_controller.save_state")
    @patch("rtk_controller.load_state", return_value=None)
    @patch("rtk_controller.NtripClient")
    @patch("rtk_controller.NmeaParser")
    @patch("rtk_controller.GnssDevice")
    @patch("rtk_controller.get_profile")
    def test_stop_running_controller(
        self,
        mock_get_profile,
        mock_gnss_cls,
        mock_parser_cls,
        mock_ntrip_cls,
        mock_load,
        mock_save,
    ):
        controller, _, gnss_device, _, ntrip_client = _build_controller(
            mock_get_profile, mock_gnss_cls, mock_parser_cls,
            mock_ntrip_cls, mock_load, mock_save,
        )
        # Simulate running state
        controller._running.set()

        controller.stop()

        assert controller.is_running is False
        ntrip_client.stop.assert_called_once()
        gnss_device.close.assert_called_once()

    @patch("rtk_controller.save_state")
    @patch("rtk_controller.load_state", return_value=None)
    @patch("rtk_controller.NtripClient")
    @patch("rtk_controller.NmeaParser")
    @patch("rtk_controller.GnssDevice")
    @patch("rtk_controller.get_profile")
    def test_stop_calls_save_state(
        self,
        mock_get_profile,
        mock_gnss_cls,
        mock_parser_cls,
        mock_ntrip_cls,
        mock_load,
        mock_save,
    ):
        controller, _, gnss_device, _, ntrip_client = _build_controller(
            mock_get_profile, mock_gnss_cls, mock_parser_cls,
            mock_ntrip_cls, mock_load, mock_save,
        )
        controller._running.set()

        controller.stop()

        mock_save.assert_called_once()
        # The argument should be a dict (state snapshot)
        saved_arg = mock_save.call_args[0][0]
        assert isinstance(saved_arg, dict)

    @patch("rtk_controller.save_state")
    @patch("rtk_controller.load_state", return_value=None)
    @patch("rtk_controller.NtripClient")
    @patch("rtk_controller.NmeaParser")
    @patch("rtk_controller.GnssDevice")
    @patch("rtk_controller.get_profile")
    def test_stop_with_position_logger(
        self,
        mock_get_profile,
        mock_gnss_cls,
        mock_parser_cls,
        mock_ntrip_cls,
        mock_load,
        mock_save,
    ):
        controller, _, gnss_device, _, ntrip_client = _build_controller(
            mock_get_profile, mock_gnss_cls, mock_parser_cls,
            mock_ntrip_cls, mock_load, mock_save,
        )
        controller._running.set()

        # Inject a mock position logger
        mock_pos_logger = MagicMock()
        controller._position_logger = mock_pos_logger

        controller.stop()

        mock_pos_logger.stop.assert_called_once()


# ==========================================================================
# 3. TestReadGnssDataLoop
# ==========================================================================
class TestReadGnssDataLoop:
    @patch("rtk_controller.save_state")
    @patch("rtk_controller.load_state", return_value=None)
    @patch("rtk_controller.NtripClient")
    @patch("rtk_controller.NmeaParser")
    @patch("rtk_controller.GnssDevice")
    @patch("rtk_controller.get_profile")
    def test_reads_and_parses_lines(
        self,
        mock_get_profile,
        mock_gnss_cls,
        mock_parser_cls,
        mock_ntrip_cls,
        mock_load,
        mock_save,
    ):
        controller, _, gnss_device, nmea_parser, _ = _build_controller(
            mock_get_profile, mock_gnss_cls, mock_parser_cls,
            mock_ntrip_cls, mock_load, mock_save,
        )
        gnss_device.is_connected.return_value = True

        # Feed one NMEA line, then stop the loop
        def read_line_side_effect():
            # Clear the running flag so the loop exits after this iteration
            controller._running.clear()
            return "$GPGGA,123456,4807.038,N,01131.000,E,1,08,0.9,545.4,M,47.0,M,,*47"

        gnss_device.read_line.side_effect = read_line_side_effect

        controller._running.set()
        controller._read_gnss_data_loop()

        nmea_parser.parse.assert_called_once()
        parsed_line = nmea_parser.parse.call_args[0][0]
        assert "$GPGGA" in parsed_line

    @patch("rtk_controller.save_state")
    @patch("rtk_controller.load_state", return_value=None)
    @patch("rtk_controller.NtripClient")
    @patch("rtk_controller.NmeaParser")
    @patch("rtk_controller.GnssDevice")
    @patch("rtk_controller.get_profile")
    def test_handles_disconnected_device(
        self,
        mock_get_profile,
        mock_gnss_cls,
        mock_parser_cls,
        mock_ntrip_cls,
        mock_load,
        mock_save,
    ):
        controller, _, gnss_device, _, _ = _build_controller(
            mock_get_profile, mock_gnss_cls, mock_parser_cls,
            mock_ntrip_cls, mock_load, mock_save,
        )

        call_count = 0

        def is_connected_side_effect():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # First iteration: disconnected -> triggers reconnect attempt
                return False
            # Second iteration: stop the loop
            controller._running.clear()
            return True

        gnss_device.is_connected.side_effect = is_connected_side_effect
        gnss_device.connect.return_value = True
        gnss_device.read_line.return_value = ""

        # Replace the Event.wait to avoid actual sleeping
        original_wait = controller._running.wait
        controller._running.wait = lambda timeout=None: original_wait(timeout=0)

        controller._running.set()
        controller._read_gnss_data_loop()

        # The device should have attempted a reconnect
        gnss_device.connect.assert_called_once()

    @patch("rtk_controller.save_state")
    @patch("rtk_controller.load_state", return_value=None)
    @patch("rtk_controller.NtripClient")
    @patch("rtk_controller.NmeaParser")
    @patch("rtk_controller.GnssDevice")
    @patch("rtk_controller.get_profile")
    def test_handles_serial_error(
        self,
        mock_get_profile,
        mock_gnss_cls,
        mock_parser_cls,
        mock_ntrip_cls,
        mock_load,
        mock_save,
    ):
        controller, _, gnss_device, nmea_parser, _ = _build_controller(
            mock_get_profile, mock_gnss_cls, mock_parser_cls,
            mock_ntrip_cls, mock_load, mock_save,
        )
        gnss_device.is_connected.return_value = True

        call_count = 0

        def read_line_side_effect():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # First read: serial error (None)
                return None
            # Second read: stop the loop
            controller._running.clear()
            return ""

        gnss_device.read_line.side_effect = read_line_side_effect

        # Replace the Event.wait to avoid actual sleeping
        original_wait = controller._running.wait
        controller._running.wait = lambda timeout=None: original_wait(timeout=0)

        controller._running.set()
        controller._read_gnss_data_loop()

        # parse should NOT have been called (None and "" are not valid lines)
        nmea_parser.parse.assert_not_called()


# ==========================================================================
# 4. TestRtkControllerMisc
# ==========================================================================
class TestRtkControllerMisc:
    @patch("rtk_controller.save_state")
    @patch("rtk_controller.load_state", return_value=None)
    @patch("rtk_controller.NtripClient")
    @patch("rtk_controller.NmeaParser")
    @patch("rtk_controller.GnssDevice")
    @patch("rtk_controller.get_profile")
    def test_reset_ntrip_delegates(
        self,
        mock_get_profile,
        mock_gnss_cls,
        mock_parser_cls,
        mock_ntrip_cls,
        mock_load,
        mock_save,
    ):
        controller, _, _, _, ntrip_client = _build_controller(
            mock_get_profile, mock_gnss_cls, mock_parser_cls,
            mock_ntrip_cls, mock_load, mock_save,
        )
        ntrip_client.reset_connection.return_value = True

        result = controller.reset_ntrip_connection()

        assert result is True
        ntrip_client.reset_connection.assert_called_once()

    @patch("rtk_controller.save_state")
    @patch("rtk_controller.load_state", return_value=None)
    @patch("rtk_controller.NtripClient")
    @patch("rtk_controller.NmeaParser")
    @patch("rtk_controller.GnssDevice")
    @patch("rtk_controller.get_profile")
    def test_get_current_state_returns_snapshot(
        self,
        mock_get_profile,
        mock_gnss_cls,
        mock_parser_cls,
        mock_ntrip_cls,
        mock_load,
        mock_save,
    ):
        controller, _, _, _, _ = _build_controller(
            mock_get_profile, mock_gnss_cls, mock_parser_cls,
            mock_ntrip_cls, mock_load, mock_save,
        )

        state = controller.get_current_state()

        assert isinstance(state, dict)
        # Verify expected keys from GnssState are present
        assert "default_lat" in state
        assert "default_lon" in state
        assert "fix_type" in state
        assert "ntrip_connected" in state
        assert "position" in state
        assert "status" in state
