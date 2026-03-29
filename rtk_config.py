# rtk_config.py - Configuration handling for the RTK client

import argparse
import logging
import os
from pathlib import Path
from typing import Optional

import yaml  # type: ignore[import-untyped]

from rtk_constants import *  # Import constants

logger = logging.getLogger(__name__)

class Config:
    """Holds all configuration parameters."""
    def __init__(self, args: argparse.Namespace):
        self.serial_port: str = args.port
        if self.serial_port == 'auto':
            from gnss_device import discover_gnss_ports
            ports = discover_gnss_ports()
            if ports:
                self.serial_port = ports[0]
                logger.info(f"Auto-discovered serial port: {self.serial_port}")
            else:
                logger.warning("No serial ports found during auto-discovery, using default.")
                self.serial_port = DEFAULT_SERIAL_PORT
        self.baud_rate: int = args.baud
        self.ntrip_server: str = args.ntrip_server or DEFAULT_NTRIP_SERVER
        self.ntrip_port: int = args.ntrip_port or DEFAULT_NTRIP_PORT
        self.ntrip_mountpoint: str = args.ntrip_mountpoint or DEFAULT_NTRIP_MOUNTPOINT
        self.ntrip_username: str = args.ntrip_user or os.environ.get('NTRIP_USER', DEFAULT_NTRIP_USERNAME)
        self.ntrip_password: str = args.ntrip_pass or os.environ.get('NTRIP_PASS', DEFAULT_NTRIP_PASSWORD)
        self.default_lat: float = args.default_lat or DEFAULT_LAT
        self.default_lon: float = args.default_lon or DEFAULT_LON
        self.default_alt: float = args.default_alt or DEFAULT_ALT
        self.ntrip_tls: bool = args.ntrip_tls
        self.gnss_module: str = args.gnss_module
        self.debug: bool = args.debug
        self.position_log: Optional[str] = getattr(args, 'position_log', None)
        self.position_log_interval: float = getattr(args, 'position_log_interval', 5.0)
        demo_val = getattr(args, 'demo', False)
        self.demo: bool = bool(demo_val)
        self.demo_file: Optional[str] = demo_val if isinstance(demo_val, str) else None

        # Note: Log level setup is handled in main.py after Config is created
        logger.info("Configuration loaded.")
        safe_dict = {k: ("***" if "password" in k and v else v) for k, v in self.__dict__.items()}
        logger.debug(f"Config details: {safe_dict}")


def _load_config_file(path: str) -> dict:
    """Reads a YAML config file and returns a flat dict mapping CLI arg names to values.

    Nested keys are flattened using the following mapping:
    - ntrip.<key> -> ntrip_<key>
    - position.lat -> default_lat, position.lon -> default_lon, position.alt -> default_alt
    - Top-level keys map directly (port, baud, gnss_module, log_file, debug)

    Raises:
        FileNotFoundError: If the config file does not exist.
        yaml.YAMLError: If the file contains invalid YAML.
    """
    config_path = Path(path)
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")

    with open(config_path) as f:
        data = yaml.safe_load(f)

    if data is None:
        return {}

    result: dict = {}

    # Mapping for position sub-keys to CLI arg names
    position_key_map = {
        "lat": "default_lat",
        "lon": "default_lon",
        "alt": "default_alt",
    }

    for key, value in data.items():
        if key == "ntrip" and isinstance(value, dict):
            for sub_key, sub_value in value.items():
                result[f"ntrip_{sub_key}"] = sub_value
        elif key == "position" and isinstance(value, dict):
            for sub_key, sub_value in value.items():
                mapped = position_key_map.get(sub_key)
                if mapped:
                    result[mapped] = sub_value
        else:
            result[key] = value

    return result


def parse_arguments() -> argparse.Namespace:
    """Parses command line arguments."""
    parser = argparse.ArgumentParser(
        description='RTK GNSS Client (Modular - Curses UI)',
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    # Config file
    parser.add_argument('--config', default=None, help='Path to YAML config file')

    # Serial Port Arguments
    parser.add_argument('--port', default=DEFAULT_SERIAL_PORT, help='Serial port of GNSS receiver')
    parser.add_argument('--baud', type=int, default=DEFAULT_BAUD_RATE, help='Baud rate for serial connection')

    # GNSS Module Arguments
    module_group = parser.add_argument_group('GNSS Module')
    module_group.add_argument('--gnss-module', default='lc29h',
                              help='GNSS module type (lc29h, generic)')

    # NTRIP Arguments
    ntrip_group = parser.add_argument_group('NTRIP Caster Configuration')
    ntrip_group.add_argument('--ntrip-server', default=DEFAULT_NTRIP_SERVER, help='NTRIP caster server address')
    ntrip_group.add_argument('--ntrip-port', type=int, default=DEFAULT_NTRIP_PORT, help='NTRIP caster server port')
    ntrip_group.add_argument('--ntrip-mountpoint', default=DEFAULT_NTRIP_MOUNTPOINT, help='NTRIP caster mountpoint')
    ntrip_group.add_argument('--ntrip-user', default=None, help='NTRIP username (or set NTRIP_USER env var)')
    ntrip_group.add_argument('--ntrip-pass', default=None, help='NTRIP password (or set NTRIP_PASS env var)')
    ntrip_group.add_argument('--ntrip-tls', action='store_true', default=DEFAULT_NTRIP_TLS, help='Enable TLS/SSL for NTRIP connection')

    # Fallback Position Arguments
    pos_group = parser.add_argument_group('Fallback Position (Used for GGA when no fix)')
    pos_group.add_argument('--default-lat', type=float, default=DEFAULT_LAT, help='Default latitude')
    pos_group.add_argument('--default-lon', type=float, default=DEFAULT_LON, help='Default longitude')
    pos_group.add_argument('--default-alt', type=float, default=DEFAULT_ALT, help='Default altitude (meters)')

    # Logging Arguments
    log_group = parser.add_argument_group('Logging Configuration')
    log_group.add_argument('--log-file', default=DEFAULT_LOG_FILENAME, help='Log file name')
    log_group.add_argument('--debug', action='store_true', help='Enable debug level logging to file')
    log_group.add_argument('--position-log', default=None, help='Log positions to CSV file')
    log_group.add_argument('--position-log-interval', type=float, default=5.0, help='Position log interval in seconds')

    # Demo mode
    parser.add_argument('--demo', nargs='?', const=True, default=False,
                        metavar='NMEA_FILE',
                        help='Run in demo mode (optionally with a custom NMEA file)')

    args = parser.parse_args()

    # If a config file is specified, load it and apply values as defaults
    # (CLI args that were explicitly provided take precedence)
    if args.config is not None:
        yaml_config = _load_config_file(args.config)
        defaults = vars(parser.parse_args([]))  # get pure defaults (no CLI input)
        for key, value in yaml_config.items():
            if hasattr(args, key) and getattr(args, key) == defaults.get(key):
                setattr(args, key, value)

    return args
