# RTK Client

A terminal-based RTK GNSS client for real-time kinematic positioning with NTRIP correction data. Designed for use with the Quectel LC29H(DA) GNSS module.

## Features

- Real-time GNSS position display with RTK Fixed/Float status
- NTRIP client for receiving RTCM3 correction data
- Optional TLS/SSL encryption for NTRIP connections
- Multi-constellation support (GPS, GLONASS, Galileo, BeiDou, QZSS)
- Curses-based terminal UI with satellite tracking, SNR statistics, and live log
- Automatic NTRIP reconnection with exponential backoff
- Configurable via command-line arguments and environment variables

## Requirements

- Python 3.9+
- Serial GNSS receiver (e.g. Quectel LC29HDA) connected via USB
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
python3 rtk_client_final.py [OPTIONS]
```

### Options

| Option | Default | Description |
|--------|---------|-------------|
| `--port` | `/dev/ttyUSB0` | Serial port of GNSS receiver |
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

### Example

```bash
export NTRIP_USER=myuser
export NTRIP_PASS=mypass

python3 rtk_client_final.py \
  --port /dev/ttyUSB0 \
  --ntrip-server your-caster.com \
  --ntrip-port 2101 \
  --ntrip-mountpoint MOUNT1 \
  --ntrip-tls
```

### Keyboard Controls

| Key | Action |
|-----|--------|
| `q` | Quit application |
| `r` | Reset NTRIP connection |

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
                                  v
                            StatusDisplay (Curses UI)
```

The application runs 3 threads:
- **Main thread**: Curses UI event loop and display updates
- **GNSS reader**: Continuous serial port reading and NMEA parsing
- **NTRIP client**: Network communication, sends GGA positions, receives RTCM3 corrections

## Project Structure

```
rtk_client_final.py   - Application entry point
rtk_controller.py     - Component orchestrator
rtk_state.py          - Thread-safe shared state
gnss_device.py        - Serial communication with GNSS receiver
ntrip_client.py       - NTRIP protocol client
nmea_parser.py        - NMEA sentence parser
status_display.py     - Curses-based terminal UI
rtk_config.py         - CLI argument parsing
rtk_constants.py      - Shared constants
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

# Run tests (73 tests)
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

## License

All rights reserved.
