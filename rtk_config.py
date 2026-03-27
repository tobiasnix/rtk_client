# rtk_config.py - Configuration handling for the RTK client

import argparse
import logging
import os

from rtk_constants import *  # Import constants

logger = logging.getLogger(__name__)

class Config:
    """Holds all configuration parameters."""
    def __init__(self, args: argparse.Namespace):
        self.serial_port: str = args.port
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
        self.debug: bool = args.debug

        # Note: Log level setup is handled in main.py after Config is created
        logger.info("Configuration loaded.")
        logger.debug(f"Config details: {self.__dict__}")

def parse_arguments() -> argparse.Namespace:
    """Parses command line arguments."""
    parser = argparse.ArgumentParser(
        description='LC29HDA RTK GNSS Client (Modular - Curses UI)',
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    # Serial Port Arguments
    parser.add_argument('--port', default=DEFAULT_SERIAL_PORT, help='Serial port of GNSS receiver')
    parser.add_argument('--baud', type=int, default=DEFAULT_BAUD_RATE, help='Baud rate for serial connection')

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

    return parser.parse_args()
