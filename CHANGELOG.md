# Changelog

## [0.3.0] - 2026-03-29

Bugfixes, demo mode, expanded test suite, and dev tooling.

### Added
- **Demo mode**: `--demo` flag runs the app with simulated GNSS data and NTRIP — no hardware needed
- `demo_device.py` replays `data/demo.nmea` in an endless loop (No Fix → GPS → RTK Float → RTK Fixed)
- `demo_ntrip.py` simulates NTRIP connection with fake RTCM stats
- `main.py` convenience entry point (delegates to `rtk_client.py`)
- mypy configuration in `pyproject.toml` (permissive mode)
- `.pre-commit-config.yaml` with ruff and mypy hooks
- 302 tests total (+161 new): status_display, nmea_parser, ntrip_client, rtk_controller, connection state, demo components
- Expanded ruff rules: `B` (bugbear), `UP` (pyupgrade), `SIM` (simplify)

### Fixed
- **Critical**: `NmeaParser.parse()` method was missing — caused `AttributeError` at runtime
- **Critical**: `curses.isendwin()` called as window method instead of module function — crashed on exit
- **Critical**: `StatusDisplay.close()` called `endwin()` which conflicted with `curses.wrapper()` — double cleanup crash
- Credential exposure: Authorization header and password redacted from debug logs
- `.env` added to `.gitignore` to prevent accidental credential commits
- Bare except rule (E722) removed from ruff ignore (no bare excepts existed)

### Changed
- Renamed `rtk_client_final.py` → `rtk_client.py`
- Modernized type annotations: `Dict`/`List` → `dict`/`list` across all source files
- `mypy>=1.0` and `pre-commit>=3.0` added to `requirements-dev.txt`

## [0.2.0] - 2026-03-27

Multi-GNSS-module support and 6 new features.

### Added
- **Multi-module support**: `ModuleProfile` abstraction with `--gnss-module` flag (`lc29h`, `generic`)
- **YAML config file**: `--config config.yaml` with CLI override support
- **Position logging**: `--position-log` CSV export with configurable interval
- **Serial auto-discovery**: `--port auto` detects GNSS receivers via `serial.tools.list_ports`
- **Help overlay**: `?` key shows keyboard shortcuts in the curses UI
- **State persistence**: Saves GNSS state to `.rtk_state.json` on shutdown, restores on startup
- `module_profiles.py` with `LC29HProfile` and `GenericProfile`
- `position_logger.py` for CSV position recording
- `state_persistence.py` for JSON state save/load
- `ntrip_connection_state.py` extracted from `ntrip_client.py`
- `rtcm_parser.py` extracted from `ntrip_client.py`
- `config.example.yaml` template
- `pyyaml` dependency added to `requirements.txt`
- 141 tests total (+25 new)

### Changed
- `GnssDevice` accepts `ModuleProfile` parameter, delegates config/ACK logic to profile
- `RtkController` resolves profile from config and passes to `GnssDevice`
- UI title is dynamic based on active module profile
- `NtripClient` split into 3 focused modules (was 670 lines)
- Application description changed from "LC29HDA" to generic "RTK GNSS Client"

## [0.1.0] - 2026-03-27

First documented release with professional development practices.

### Added
- README.md with usage, architecture, security, and troubleshooting docs
- requirements.txt and requirements-dev.txt for dependency management
- Unit test suite (73 tests) covering rtk_state, nmea_parser, ntrip_client, gnss_device, and integration
- Linting with ruff (pyproject.toml configuration)
- GitHub Actions CI pipeline (.github/workflows/ci.yml) for lint + test
- TLS/SSL support for NTRIP connections (`--ntrip-tls` flag)
- Environment variable support for credentials (`NTRIP_USER`, `NTRIP_PASS`)
- .env.example for credential documentation
- __version__.py (v0.1.0)
- CHANGELOG.md
- Log rotation (RotatingFileHandler, 5MB max, 3 backups)
- `NtripClient.is_running()` public method
- `RtkController.reset_ntrip_connection()` public method

### Fixed
- **Critical**: Duplicate `_parse_gsa()` method — first definition was dead code, only second was called
- **Critical**: `AttributeError` crash in status_display.py — `self.SNR_THRESHOLD_*` was never defined
- **Critical**: Deadlock in `increment_error_count()` and `set_ntrip_gave_up()` — changed `Lock` to `RLock`
- Deep copy in `get_state_snapshot()` — prevents race conditions with mutable objects
- Encapsulation violation: replaced direct `_thread` access with public API methods
- `StatusDisplay.close()` cleanup now actually called on shutdown
- Removed redundant `fileno()` check that introduced a race condition

### Changed
- Removed hardcoded default credentials from rtk_constants.py (now empty strings)
- SNR threshold constants consolidated to single definition in rtk_constants.py
- Magic numbers moved to rtk_constants.py (MAX_UI_MESSAGE_LENGTH, NTRIP_HEADER_SIZE_LIMIT)
- .gitignore: replaced `__*` with `__pycache__/` and `*.pyc` (was blocking `__version__.py`)
- Import ordering and whitespace cleaned up with ruff auto-fix
- Added return type hints to all public methods
