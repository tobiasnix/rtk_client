# tests/test_config.py - Tests for YAML config file support

from unittest.mock import patch

import pytest
import yaml

from rtk_config import _load_config_file


class TestLoadConfigFile:
    """Tests for _load_config_file()."""

    def test_load_valid_full_config(self, tmp_path):
        """Test loading a valid YAML config with all keys."""
        config_data = {
            "port": "/dev/ttyACM0",
            "baud": 115200,
            "gnss_module": "generic",
            "ntrip": {
                "server": "caster.example.com",
                "port": 2101,
                "mountpoint": "MOUNT1",
                "user": "myuser",
                "pass": "mypass",
                "tls": True,
            },
            "position": {
                "lat": 48.123,
                "lon": 11.456,
                "alt": 520.0,
            },
            "log_file": "my.log",
            "debug": True,
        }
        config_file = tmp_path / "config.yaml"
        config_file.write_text(yaml.dump(config_data))

        result = _load_config_file(str(config_file))

        assert result["port"] == "/dev/ttyACM0"
        assert result["baud"] == 115200
        assert result["gnss_module"] == "generic"
        assert result["ntrip_server"] == "caster.example.com"
        assert result["ntrip_port"] == 2101
        assert result["ntrip_mountpoint"] == "MOUNT1"
        assert result["ntrip_user"] == "myuser"
        assert result["ntrip_pass"] == "mypass"
        assert result["ntrip_tls"] is True
        assert result["default_lat"] == 48.123
        assert result["default_lon"] == 11.456
        assert result["default_alt"] == 520.0
        assert result["log_file"] == "my.log"
        assert result["debug"] is True

    def test_load_partial_config(self, tmp_path):
        """Test loading a YAML config with only some keys set."""
        config_data = {
            "port": "/dev/ttyUSB1",
            "ntrip": {
                "server": "my.caster.net",
            },
        }
        config_file = tmp_path / "partial.yaml"
        config_file.write_text(yaml.dump(config_data))

        result = _load_config_file(str(config_file))

        assert result["port"] == "/dev/ttyUSB1"
        assert result["ntrip_server"] == "my.caster.net"
        # Keys not in the YAML should not appear
        assert "baud" not in result
        assert "ntrip_port" not in result
        assert "default_lat" not in result

    def test_missing_config_file_raises(self):
        """Test that a missing config file raises FileNotFoundError."""
        with pytest.raises(FileNotFoundError, match="Config file not found"):
            _load_config_file("/nonexistent/path/config.yaml")

    def test_empty_config_file(self, tmp_path):
        """Test that an empty YAML file returns an empty dict."""
        config_file = tmp_path / "empty.yaml"
        config_file.write_text("")

        result = _load_config_file(str(config_file))

        assert result == {}

    def test_position_only_config(self, tmp_path):
        """Test loading a config with only position keys."""
        config_data = {
            "position": {
                "lat": 52.52,
                "lon": 13.405,
                "alt": 34.0,
            },
        }
        config_file = tmp_path / "pos.yaml"
        config_file.write_text(yaml.dump(config_data))

        result = _load_config_file(str(config_file))

        assert result["default_lat"] == 52.52
        assert result["default_lon"] == 13.405
        assert result["default_alt"] == 34.0
        assert len(result) == 3


class TestParseArgumentsWithConfig:
    """Tests for parse_arguments() with --config flag."""

    def test_cli_args_override_yaml(self, tmp_path):
        """Test that explicit CLI args override YAML config values."""
        config_data = {
            "port": "/dev/ttyACM0",
            "baud": 9600,
            "ntrip": {
                "server": "yaml.caster.com",
            },
        }
        config_file = tmp_path / "config.yaml"
        config_file.write_text(yaml.dump(config_data))

        from rtk_config import parse_arguments

        with patch(
            "sys.argv",
            [
                "prog",
                "--config", str(config_file),
                "--port", "/dev/ttyUSB99",
            ],
        ):
            args = parse_arguments()

        # CLI-provided value should win
        assert args.port == "/dev/ttyUSB99"
        # YAML values should apply where CLI was not explicit
        assert args.baud == 9600
        assert args.ntrip_server == "yaml.caster.com"

    def test_yaml_values_applied_as_defaults(self, tmp_path):
        """Test that YAML values are used when CLI args are not provided."""
        config_data = {
            "port": "/dev/ttyACM1",
            "gnss_module": "generic",
            "debug": True,
            "ntrip": {
                "server": "yaml.server.net",
                "port": 2102,
                "mountpoint": "YAML_MP",
                "user": "yaml_user",
                "pass": "yaml_pass",
                "tls": True,
            },
            "position": {
                "lat": 50.0,
                "lon": 10.0,
                "alt": 100.0,
            },
            "log_file": "yaml.log",
        }
        config_file = tmp_path / "config.yaml"
        config_file.write_text(yaml.dump(config_data))

        from rtk_config import parse_arguments

        with patch("sys.argv", ["prog", "--config", str(config_file)]):
            args = parse_arguments()

        assert args.port == "/dev/ttyACM1"
        assert args.gnss_module == "generic"
        assert args.debug is True
        assert args.ntrip_server == "yaml.server.net"
        assert args.ntrip_port == 2102
        assert args.ntrip_mountpoint == "YAML_MP"
        assert args.ntrip_user == "yaml_user"
        assert args.ntrip_pass == "yaml_pass"
        assert args.ntrip_tls is True
        assert args.default_lat == 50.0
        assert args.default_lon == 10.0
        assert args.default_alt == 100.0
        assert args.log_file == "yaml.log"

    def test_missing_config_file_in_parse_raises(self, tmp_path):
        """Test that referencing a nonexistent config file raises an error."""
        from rtk_config import parse_arguments

        with patch("sys.argv", ["prog", "--config", "/nonexistent/config.yaml"]), pytest.raises(FileNotFoundError):
            parse_arguments()

    def test_no_config_flag_uses_defaults(self):
        """Test that without --config, default values are used."""
        from rtk_config import parse_arguments
        from rtk_constants import DEFAULT_BAUD_RATE, DEFAULT_SERIAL_PORT

        with patch("sys.argv", ["prog"]):
            args = parse_arguments()

        assert args.config is None
        assert args.port == DEFAULT_SERIAL_PORT
        assert args.baud == DEFAULT_BAUD_RATE
