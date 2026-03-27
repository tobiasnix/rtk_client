from unittest.mock import MagicMock, patch

from rtk_config import Config
from rtk_controller import RtkController
from rtk_state import GnssState


class TestRtkControllerInit:
    @patch("rtk_config.parse_arguments")
    def test_controller_creates_components(self, mock_parse):
        args = MagicMock()
        args.port = "/dev/ttyUSB0"
        args.baud = 115200
        args.ntrip_server = "127.0.0.1"
        args.ntrip_port = 2101
        args.ntrip_mountpoint = "TEST"
        args.ntrip_user = "user"
        args.ntrip_pass = "pass"
        args.ntrip_tls = False
        args.default_lat = 40.0
        args.default_lon = -7.0
        args.default_alt = 100.0
        args.gnss_module = "lc29h"
        args.debug = False

        config = Config(args)
        controller = RtkController(config)

        assert controller.state is not None
        assert isinstance(controller.state, GnssState)
        assert controller.is_running is False

    @patch("rtk_config.parse_arguments")
    def test_controller_state_defaults(self, mock_parse):
        args = MagicMock()
        args.port = "/dev/ttyUSB0"
        args.baud = 115200
        args.ntrip_server = "127.0.0.1"
        args.ntrip_port = 2101
        args.ntrip_mountpoint = "TEST"
        args.ntrip_user = None
        args.ntrip_pass = None
        args.ntrip_tls = False
        args.default_lat = 40.0
        args.default_lon = -7.0
        args.default_alt = 100.0
        args.gnss_module = "lc29h"
        args.debug = False

        config = Config(args)
        controller = RtkController(config)
        state = controller.get_current_state()

        assert state['default_lat'] == 40.0
        assert state['default_lon'] == -7.0
        assert state['fix_type'] == 0
        assert state['ntrip_connected'] is False


class TestRtkControllerStop:
    @patch("rtk_config.parse_arguments")
    def test_stop_when_not_started(self, mock_parse):
        args = MagicMock()
        args.port = "/dev/ttyUSB0"
        args.baud = 115200
        args.ntrip_server = "127.0.0.1"
        args.ntrip_port = 2101
        args.ntrip_mountpoint = "TEST"
        args.ntrip_user = None
        args.ntrip_pass = None
        args.ntrip_tls = False
        args.default_lat = 0.0
        args.default_lon = 0.0
        args.default_alt = 0.0
        args.gnss_module = "lc29h"
        args.debug = False

        config = Config(args)
        controller = RtkController(config)
        controller.stop()  # should not raise
        assert controller.is_running is False
