# Changelog

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
