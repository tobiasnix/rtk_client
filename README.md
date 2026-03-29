# RTK Client

A terminal-based RTK GNSS client for real-time kinematic positioning with NTRIP correction data. Supports multiple GNSS receivers including Quectel LC29H(DA) and any standard NMEA module.

## Features

- Real-time GNSS position display with RTK Fixed/Float status
- NTRIP client for receiving RTCM3 correction data
- Optional TLS/SSL encryption for NTRIP connections
- Multi-constellation support (GPS, GLONASS, Galileo, BeiDou, QZSS)
- Multi-module support (LC29H, generic NMEA, extensible via profiles)
- Curses-based terminal UI with satellite tracking, SNR statistics, and live log
- Automatic NTRIP reconnection with exponential backoff
- YAML configuration file support
- CSV position logging
- Serial port auto-discovery
- State persistence across restarts
- Configurable via config file, CLI arguments, and environment variables

## Requirements

- Python 3.9+
- Serial GNSS receiver (e.g. Quectel LC29HDA, u-blox, or any NMEA module) connected via USB
- Access to an NTRIP caster for RTK corrections

## Installation

```bash
pip install -r requirements.txt
```

For development (linting, tests):

```bash
pip install -r requirements-dev.txt
```

## Usage

```bash
python3 rtk_client.py [OPTIONS]
```

### Options

| Option | Default | Description |
|--------|---------|-------------|
| `--config` | *(none)* | Path to YAML config file |
| `--gnss-module` | `lc29h` | GNSS module type (`lc29h`, `generic`) |
| `--port` | `/dev/ttyUSB0` | Serial port (`auto` for auto-discovery) |
| `--baud` | `115200` | Baud rate for serial connection |
| `--ntrip-server` | `193.137.94.71` | NTRIP caster server address |
| `--ntrip-port` | `2101` | NTRIP caster server port |
| `--ntrip-mountpoint` | `PNM1` | NTRIP caster mountpoint |
| `--ntrip-user` | *(none)* | NTRIP username (or `NTRIP_USER` env var) |
| `--ntrip-pass` | *(none)* | NTRIP password (or `NTRIP_PASS` env var) |
| `--ntrip-tls` | off | Enable TLS/SSL for NTRIP connection |
| `--default-lat` | `40.109` | Fallback latitude (used when no fix) |
| `--default-lon` | `-7.154` | Fallback longitude |
| `--default-alt` | `476.68` | Fallback altitude (meters) |
| `--log-file` | `rtk_client.log` | Log file name |
| `--debug` | off | Enable debug level logging |
| `--position-log` | *(none)* | Log positions to CSV file |
| `--position-log-interval` | `5.0` | Position log interval in seconds |

### Configuration File

Instead of many CLI arguments, use a YAML config file:

```bash
python3 rtk_client.py --config config.yaml
```

See `config.example.yaml` for the full structure. CLI arguments override config file values.

### Examples

```bash
# With environment variables
export NTRIP_USER=myuser
export NTRIP_PASS=mypass
python3 rtk_client.py --port /dev/ttyUSB0 --ntrip-server caster.example.com

# With config file
python3 rtk_client.py --config config.yaml

# Auto-discover serial port + generic NMEA module
python3 rtk_client.py --port auto --gnss-module generic

# With position logging
python3 rtk_client.py --position-log positions.csv --position-log-interval 2.0
```

### Keyboard Controls

| Key | Action |
|-----|--------|
| `q` | Quit application |
| `r` | Reset NTRIP connection |
| `?` | Show help overlay |

## Supported Modules

| Module | `--gnss-module` | Notes |
|--------|----------------|-------|
| Quectel LC29H(DA) | `lc29h` (default) | Full support with PAIR/PQTM config commands |
| Any NMEA receiver | `generic` | No proprietary config, standard NMEA + RTCM3 only |

To add support for a new module, create a `ModuleProfile` subclass in `module_profiles.py`.

## Security

**Credentials** should be passed via environment variables rather than CLI arguments to avoid exposure in process listings and shell history:

```bash
export NTRIP_USER=your_username
export NTRIP_PASS=your_password
```

See `.env.example` for reference. CLI arguments (`--ntrip-user`, `--ntrip-pass`) are supported but not recommended for production.

**TLS/SSL** can be enabled with `--ntrip-tls` for encrypted NTRIP connections. This uses Python's `ssl.create_default_context()` with hostname verification.

## Architecture

```
Serial Port (GNSS Receiver)
    |
    v
GnssDevice --> NmeaParser --> GnssState <-- NtripClient (RTCM3)
                                  |
                            PositionLogger (CSV)
                                  |
                                  v
                            StatusDisplay (Curses UI)
```

The application runs up to 4 threads:
- **Main thread**: Curses UI event loop and display updates
- **GNSS reader**: Continuous serial port reading and NMEA parsing
- **NTRIP client**: Network communication, sends GGA positions, receives RTCM3 corrections
- **Position logger** (optional): Periodic CSV position recording

## Project Structure

```
rtk_client.py        - Application entry point
rtk_controller.py          - Component orchestrator
rtk_state.py               - Thread-safe shared state
gnss_device.py             - Serial communication with GNSS receiver
module_profiles.py         - GNSS module profiles (LC29H, generic)
ntrip_client.py            - NTRIP protocol client
ntrip_connection_state.py  - NTRIP connection state machine
rtcm_parser.py             - RTCM3 message type extraction
nmea_parser.py             - NMEA sentence parser
status_display.py          - Curses-based terminal UI
position_logger.py         - CSV position logging
state_persistence.py       - JSON state save/load
rtk_config.py              - CLI argument + YAML config parsing
rtk_constants.py           - Shared constants
```

## Logging

Log files use rotation (5 MB max, 3 backups):
- Default: `rtk_client.log` (configurable with `--log-file`)
- Debug level: add `--debug` flag
- Previous logs preserved as `rtk_client.log.1`, `.2`, `.3`

## Development

```bash
# Run linter
ruff check .

# Run tests (141 tests)
pytest -v
```

See [CHANGELOG.md](CHANGELOG.md) for version history.

## Troubleshooting

### "No Fix" status
- Ensure the GNSS antenna has a clear sky view
- Check the serial connection (`--port`, `--baud`)
- Wait for satellite acquisition (can take 30-60s cold start)

### NTRIP connection fails
- Verify credentials are set (`NTRIP_USER`, `NTRIP_PASS`)
- Check server/port/mountpoint configuration
- Try with `--debug` to see detailed connection logs
- If the caster requires TLS, add `--ntrip-tls`

### Terminal too small
- Minimum terminal size: 50 columns x 15 rows
- Resize your terminal and press any key to trigger redraw

### Serial port not found
- Use `--port auto` to auto-discover available ports
- Check device permissions (`sudo usermod -aG dialout $USER`)

## License

All rights reserved.
