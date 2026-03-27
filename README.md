# RTK Client

A terminal-based RTK GNSS client for real-time kinematic positioning with NTRIP correction data. Designed for use with the Quectel LC29H(DA) GNSS module.

## Features

- Real-time GNSS position display with RTK Fixed/Float status
- NTRIP client for receiving RTCM3 correction data
- Multi-constellation support (GPS, GLONASS, Galileo, BeiDou, QZSS)
- Curses-based terminal UI with satellite tracking, SNR statistics, and live log
- Automatic NTRIP reconnection with exponential backoff
- Configurable via command-line arguments

## Requirements

- Python 3
- Serial GNSS receiver (e.g. Quectel LC29HDA) connected via USB
- Access to an NTRIP caster for RTK corrections

## Installation

```bash
pip install -r requirements.txt
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
| `--ntrip-user` | `user` | NTRIP username |
| `--ntrip-pass` | `password` | NTRIP password |
| `--default-lat` | `40.109` | Fallback latitude (used when no fix) |
| `--default-lon` | `-7.154` | Fallback longitude |
| `--default-alt` | `476.68` | Fallback altitude (meters) |
| `--log-file` | `rtk_client.log` | Log file name |
| `--debug` | off | Enable debug level logging |

### Example

```bash
python3 rtk_client_final.py \
  --port /dev/ttyUSB0 \
  --ntrip-server your-caster.com \
  --ntrip-port 2101 \
  --ntrip-mountpoint MOUNT1 \
  --ntrip-user myuser \
  --ntrip-pass mypass
```

### Keyboard Controls

| Key | Action |
|-----|--------|
| `q` | Quit application |
| `r` | Reset NTRIP connection |

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

## License

All rights reserved.
