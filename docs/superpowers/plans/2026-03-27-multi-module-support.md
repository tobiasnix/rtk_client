# Multi-GNSS-Module Support Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Decouple the RTK client from the Quectel LC29H(DA) so it works with any GNSS module that outputs standard NMEA and accepts RTCM3 input.

**Architecture:** Introduce a `ModuleProfile` abstraction that encapsulates hardware-specific behavior (config commands, firmware query, ACK parsing). Ship profiles for LC29H (existing behavior) and a `generic` profile (no proprietary commands, NMEA-only). `GnssDevice` delegates all hardware-specific logic to the active profile. A `--gnss-module` CLI flag selects the profile.

**Tech Stack:** Python 3.9+, pyserial, pynmea2, ABC (abstract base class from stdlib)

---

## File Structure

| File | Action | Responsibility |
|------|--------|---------------|
| `module_profiles.py` | **Create** | `ModuleProfile` ABC + `LC29HProfile` + `GenericProfile` implementations |
| `gnss_device.py` | **Modify** | Accept `ModuleProfile`, delegate `configure_module()` and ACK parsing |
| `rtk_config.py` | **Modify** | Add `--gnss-module` CLI arg, store `gnss_module` in Config |
| `rtk_controller.py` | **Modify** | Resolve profile from config, pass to GnssDevice |
| `status_display.py:240` | **Modify** | Replace hardcoded "LC29HDA" title with dynamic name from profile |
| `tests/test_module_profiles.py` | **Create** | Tests for profiles and profile selection |
| `tests/test_gnss_device.py` | **Modify** | Update mock tests to use profiles |
| `README.md` | **Modify** | Document `--gnss-module` flag, list supported modules |

---

### Task 1: Create ModuleProfile ABC and LC29HProfile

**Files:**
- Create: `module_profiles.py`
- Test: `tests/test_module_profiles.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_module_profiles.py
from module_profiles import LC29HProfile, GenericProfile, get_profile


class TestLC29HProfile:
    def test_name(self):
        p = LC29HProfile()
        assert p.name == "LC29H"

    def test_display_name(self):
        p = LC29HProfile()
        assert "LC29H" in p.display_name

    def test_firmware_command(self):
        p = LC29HProfile()
        cmd = p.firmware_command()
        assert cmd is not None
        assert cmd["cmd"] == "PQTMVERNO"
        assert cmd["expect_ack"] is False

    def test_parse_firmware_response(self):
        p = LC29HProfile()
        assert p.parse_firmware_response("$PQTMVERNO,V1.2.3,rest*XX") == "V1.2.3"

    def test_parse_firmware_response_invalid(self):
        p = LC29HProfile()
        assert p.parse_firmware_response("garbage") is None
        assert p.parse_firmware_response("") is None
        assert p.parse_firmware_response(None) is None

    def test_config_commands(self):
        p = LC29HProfile()
        cmds = p.config_commands()
        assert len(cmds) == 7
        assert all("cmd" in c and "expect_ack" in c for c in cmds)
        assert cmds[0]["cmd"] == "PAIR062,0,1"

    def test_check_ack_success(self):
        p = LC29HProfile()
        assert p.check_ack("$PAIR001,062,0*checksum", "PAIR062,0,1") is True

    def test_check_ack_failure(self):
        p = LC29HProfile()
        assert p.check_ack("$GNRMC,some,data", "PAIR062,0,1") is False
        assert p.check_ack("", "PAIR062,0,1") is False
        assert p.check_ack(None, "PAIR062,0,1") is False


class TestGenericProfile:
    def test_name(self):
        p = GenericProfile()
        assert p.name == "generic"

    def test_display_name(self):
        p = GenericProfile()
        assert "GNSS" in p.display_name

    def test_firmware_command_is_none(self):
        p = GenericProfile()
        assert p.firmware_command() is None

    def test_config_commands_empty(self):
        p = GenericProfile()
        assert p.config_commands() == []

    def test_check_ack_always_true(self):
        p = GenericProfile()
        assert p.check_ack("anything", "anything") is True


class TestGetProfile:
    def test_get_lc29h(self):
        p = get_profile("lc29h")
        assert isinstance(p, LC29HProfile)

    def test_get_generic(self):
        p = get_profile("generic")
        assert isinstance(p, GenericProfile)

    def test_get_unknown_returns_generic(self):
        p = get_profile("unknown_module_xyz")
        assert isinstance(p, GenericProfile)

    def test_case_insensitive(self):
        p = get_profile("LC29H")
        assert isinstance(p, LC29HProfile)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_module_profiles.py -v`
Expected: `ModuleNotFoundError: No module named 'module_profiles'`

- [ ] **Step 3: Implement module_profiles.py**

```python
# module_profiles.py - Hardware-specific GNSS module profiles
"""
Abstracts hardware-specific behavior so the RTK client can work with
different GNSS modules. Each profile defines:
- Configuration commands to send on startup
- How to query and parse firmware version
- How to validate command acknowledgments
"""

import logging
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class ModuleProfile(ABC):
    """Abstract base for GNSS module profiles."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Short identifier (used in --gnss-module flag)."""
        ...

    @property
    @abstractmethod
    def display_name(self) -> str:
        """Human-readable name for UI display."""
        ...

    @abstractmethod
    def firmware_command(self) -> Optional[Dict[str, Any]]:
        """Returns the command dict to query firmware, or None if unsupported.
        Format: {"cmd": "COMMAND", "expect_ack": bool}
        """
        ...

    @abstractmethod
    def parse_firmware_response(self, response: Optional[str]) -> Optional[str]:
        """Extracts version string from the firmware query response."""
        ...

    @abstractmethod
    def config_commands(self) -> List[Dict[str, Any]]:
        """Returns list of config commands to send on startup.
        Each item: {"cmd": "COMMAND", "expect_ack": bool}
        """
        ...

    @abstractmethod
    def check_ack(self, response: Optional[str], original_cmd: str) -> bool:
        """Returns True if response is a valid ACK for the given command."""
        ...


class LC29HProfile(ModuleProfile):
    """Profile for Quectel LC29H(DA) modules (PAIR/PQTM commands)."""

    @property
    def name(self) -> str:
        return "LC29H"

    @property
    def display_name(self) -> str:
        return "Quectel LC29H(DA)"

    def firmware_command(self) -> Optional[Dict[str, Any]]:
        return {"cmd": "PQTMVERNO", "expect_ack": False}

    def parse_firmware_response(self, response: Optional[str]) -> Optional[str]:
        if not response:
            return None
        if response.startswith("$PQTMVERNO,"):
            parts = response.split(",")
            if len(parts) >= 2 and parts[1]:
                return parts[1]
        return None

    def config_commands(self) -> List[Dict[str, Any]]:
        return [
            {"cmd": "PAIR062,0,1", "expect_ack": True},   # GGA 1Hz
            {"cmd": "PAIR062,4,1", "expect_ack": True},   # RMC 1Hz
            {"cmd": "PAIR062,2,1", "expect_ack": True},   # GSA 1Hz
            {"cmd": "PAIR062,3,1", "expect_ack": True},   # GSV 1Hz
            {"cmd": "PAIR062,5,1", "expect_ack": True},   # VTG 1Hz
            {"cmd": "PAIR436,1",   "expect_ack": True},   # Enable RTCM input
            {"cmd": "PAIR513",     "expect_ack": True},    # Enable RTK mode
        ]

    def check_ack(self, response: Optional[str], original_cmd: str) -> bool:
        if not response:
            return False
        cmd_id = original_cmd.split(",")[0]
        # PAIR commands: strip "PAIR" prefix for ACK matching
        if cmd_id.startswith("PAIR"):
            cmd_id = cmd_id[4:]
        ack_prefix = f"$PAIR001,{cmd_id},0"
        return response.startswith(ack_prefix)


class GenericProfile(ModuleProfile):
    """Generic profile — no proprietary commands, works with any NMEA receiver."""

    @property
    def name(self) -> str:
        return "generic"

    @property
    def display_name(self) -> str:
        return "Generic GNSS Receiver"

    def firmware_command(self) -> Optional[Dict[str, Any]]:
        return None  # No firmware query for unknown hardware

    def parse_firmware_response(self, response: Optional[str]) -> Optional[str]:
        return None

    def config_commands(self) -> List[Dict[str, Any]]:
        return []  # No proprietary config commands

    def check_ack(self, response: Optional[str], original_cmd: str) -> bool:
        return True  # No ACK to validate


# --- Profile Registry ---

_PROFILES: Dict[str, type] = {
    "lc29h": LC29HProfile,
    "generic": GenericProfile,
}


def get_profile(module_name: str) -> ModuleProfile:
    """Returns the profile for the given module name (case-insensitive).
    Falls back to GenericProfile for unknown modules."""
    cls = _PROFILES.get(module_name.lower())
    if cls is None:
        logger.warning(f"Unknown GNSS module '{module_name}', using generic profile.")
        return GenericProfile()
    return cls()


def list_profiles() -> List[str]:
    """Returns list of registered profile names."""
    return list(_PROFILES.keys())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_module_profiles.py -v`
Expected: All 14 tests PASS

- [ ] **Step 5: Run ruff**

Run: `ruff check module_profiles.py tests/test_module_profiles.py`
Expected: All checks passed (fix if not)

- [ ] **Step 6: Commit**

```bash
git add module_profiles.py tests/test_module_profiles.py
git commit -m "feat: add ModuleProfile abstraction with LC29H and generic profiles"
```

---

### Task 2: Add --gnss-module CLI flag

**Files:**
- Modify: `rtk_config.py:24,34,38-39`
- Test: `tests/test_module_profiles.py` (extend)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_module_profiles.py`:

```python
class TestConfigIntegration:
    def test_config_stores_gnss_module(self):
        from unittest.mock import MagicMock
        from rtk_config import Config

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
        args.debug = False
        args.gnss_module = "lc29h"

        config = Config(args)
        assert config.gnss_module == "lc29h"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_module_profiles.py::TestConfigIntegration -v`
Expected: `AttributeError: 'Config' object has no attribute 'gnss_module'`

- [ ] **Step 3: Modify rtk_config.py**

In `Config.__init__`, after line 24 (`self.ntrip_tls`), add:

```python
        self.gnss_module: str = args.gnss_module
```

In `parse_arguments()`, after the serial port group (after line 39), add a new group:

```python
    # GNSS Module Arguments
    module_group = parser.add_argument_group('GNSS Module')
    module_group.add_argument('--gnss-module', default='lc29h',
                              help='GNSS module type (lc29h, generic)')
```

Also update the description (line 34):

```python
        description='RTK GNSS Client (Modular - Curses UI)',
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_module_profiles.py -v`
Expected: All 15 tests PASS

- [ ] **Step 5: Commit**

```bash
git add rtk_config.py tests/test_module_profiles.py
git commit -m "feat: add --gnss-module CLI flag (default: lc29h)"
```

---

### Task 3: Refactor GnssDevice to use ModuleProfile

**Files:**
- Modify: `gnss_device.py:14-19,75-140,214-295`
- Modify: `rtk_controller.py:27`
- Test: `tests/test_gnss_device.py` (extend)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_gnss_device.py`:

```python
from module_profiles import LC29HProfile, GenericProfile


class TestGnssDeviceWithProfile:
    @patch("gnss_device.serial.Serial")
    def test_configure_with_lc29h_profile(self, mock_serial_class):
        mock_port = MagicMock()
        mock_port.is_open = True
        mock_port.readline.return_value = b"$PQTMVERNO,V1.2.3*XX\r\n"
        mock_serial_class.return_value = mock_port

        state = GnssState(0.0, 0.0, 0.0)
        profile = LC29HProfile()
        device = GnssDevice("/dev/ttyUSB0", 115200, state, profile=profile)
        device.connect()
        result = device.configure_module()

        # Should have sent firmware query + 7 config commands = 8 writes
        assert mock_port.write.call_count == 8
        assert state.firmware_version == "V1.2.3"

    @patch("gnss_device.serial.Serial")
    def test_configure_with_generic_profile(self, mock_serial_class):
        mock_port = MagicMock()
        mock_port.is_open = True
        mock_serial_class.return_value = mock_port

        state = GnssState(0.0, 0.0, 0.0)
        profile = GenericProfile()
        device = GnssDevice("/dev/ttyUSB0", 115200, state, profile=profile)
        device.connect()
        result = device.configure_module()

        # Generic profile: no firmware query, no config commands
        assert mock_port.write.call_count == 0
        assert result is True

    @patch("gnss_device.serial.Serial")
    def test_default_profile_is_lc29h(self, mock_serial_class):
        """Backward compatibility: no profile arg defaults to LC29H."""
        mock_port = MagicMock()
        mock_port.is_open = True
        mock_port.readline.return_value = b"$PAIR001,062,0*XX\r\n"
        mock_serial_class.return_value = mock_port

        state = GnssState(0.0, 0.0, 0.0)
        device = GnssDevice("/dev/ttyUSB0", 115200, state)
        assert device._profile.name == "LC29H"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_gnss_device.py::TestGnssDeviceWithProfile -v`
Expected: `TypeError: GnssDevice.__init__() got an unexpected keyword argument 'profile'`

- [ ] **Step 3: Refactor gnss_device.py**

Change `__init__` signature:

```python
from module_profiles import ModuleProfile, LC29HProfile

class GnssDevice:
    """Handles serial communication with the GNSS module."""
    def __init__(self, port: str, baudrate: int, state: GnssState,
                 profile: Optional[ModuleProfile] = None):
        self._port_name = port
        self._baudrate = baudrate
        self._serial_port: Optional[serial.Serial] = None
        self._state = state
        self._profile: ModuleProfile = profile or LC29HProfile()
```

Replace `send_command` ACK check (lines 112-123) — make ACK validation use the profile:

```python
            if expect_ack:
                if self._profile.check_ack(response, original_command_name):
                    logger.debug(f"Command {original_command_name} acknowledged successfully.")
                elif response:
                    logger.warning(f"Command {original_command_name} received non-ACK response: {response}")
                else:
                    logger.warning(f"No response received for expected ACK to {original_command_name}")
```

Replace entire `configure_module()` body:

```python
    def configure_module(self) -> bool:
        """Sends configuration commands via the active module profile."""
        logger.info(f"Configuring GNSS module ({self._profile.display_name})...")
        self._state.add_ui_log_message(f"Configuring {self._profile.display_name}...")
        time.sleep(0.5)

        # --- Firmware Query ---
        fw_cmd = self._profile.firmware_command()
        fw_version = "Unknown"
        if fw_cmd:
            response = self.send_command(fw_cmd["cmd"], expect_ack=fw_cmd["expect_ack"])
            parsed = self._profile.parse_firmware_response(response)
            if parsed:
                fw_version = parsed
                logger.info(f"Detected firmware: {fw_version}")
            elif response:
                logger.warning(f"Could not parse firmware response: {response}")
                fw_version = "Parse Error"
            else:
                logger.warning("No response to firmware query.")
                fw_version = "No Response"
        else:
            logger.info("Module profile has no firmware query.")
            fw_version = "N/A"

        self._state.update(firmware_version=fw_version)
        if fw_version not in ("Unknown", "No Response", "N/A"):
            self._state.add_ui_log_message(f"Firmware: {fw_version}")

        # --- Configuration Commands ---
        commands = self._profile.config_commands()
        if not commands:
            logger.info("Module profile has no configuration commands.")
            self._state.add_ui_log_message("No module config needed.")
            return True

        logger.info(f"Sending {len(commands)} configuration commands...")
        success_count = 0

        for item in commands:
            cmd_str = item["cmd"]
            expect_ack = item["expect_ack"]
            response = self.send_command(cmd_str, expect_ack=expect_ack)

            if expect_ack:
                if self._profile.check_ack(response, cmd_str):
                    success_count += 1
                else:
                    logger.error(f"Config command {cmd_str} failed.")
            elif response:
                success_count += 1
            else:
                logger.error(f"Config command {cmd_str} got no response.")

            time.sleep(0.15)

        config_success = (success_count == len(commands))
        log_func = logger.info if config_success else logger.warning
        log_func(f"Module config: {success_count}/{len(commands)} acknowledged.")
        self._state.add_ui_log_message(f"Config sent ({success_count}/{len(commands)} Ack).")
        return config_success
```

- [ ] **Step 4: Wire profile through RtkController**

In `rtk_controller.py`, change the import and constructor:

```python
from module_profiles import get_profile
```

In `__init__`, replace the GnssDevice line:

```python
        self._profile = get_profile(config.gnss_module)
        self._gnss_device = GnssDevice(config.serial_port, config.baud_rate, self._state, profile=self._profile)
```

- [ ] **Step 5: Run all tests**

Run: `python3 -m pytest tests/ -v`
Expected: All tests PASS (existing + new)

- [ ] **Step 6: Run ruff**

Run: `ruff check .`
Fix any issues with `ruff check --fix .`

- [ ] **Step 7: Commit**

```bash
git add gnss_device.py rtk_controller.py tests/test_gnss_device.py
git commit -m "refactor: decouple GnssDevice from LC29H via ModuleProfile"
```

---

### Task 4: Update UI title and README

**Files:**
- Modify: `status_display.py:240`
- Modify: `README.md`
- Modify: `rtk_controller.py` (expose profile name to state)

- [ ] **Step 1: Pass profile display name to state**

In `rtk_controller.py`, after creating the profile, store the name:

```python
        self._state.update(module_name=self._profile.display_name)
```

In `rtk_state.py`, add to `__init__`:

```python
        self.module_name: str = "GNSS Receiver"
```

- [ ] **Step 2: Update status_display.py title**

Replace line 240:

```python
        module = state.get('module_name', 'RTK GNSS')
        title = f" {module} RTK Status - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} "
```

- [ ] **Step 3: Update README.md**

Replace the first line description and add the module flag to the options table:

```markdown
A terminal-based RTK GNSS client for real-time kinematic positioning with NTRIP correction data. Supports multiple GNSS receivers including Quectel LC29H(DA) and any standard NMEA module.
```

Add to options table:

```markdown
| `--gnss-module` | `lc29h` | GNSS module type (`lc29h`, `generic`) |
```

Add a "Supported Modules" section:

```markdown
## Supported Modules

| Module | `--gnss-module` | Notes |
|--------|----------------|-------|
| Quectel LC29H(DA) | `lc29h` (default) | Full support with PAIR/PQTM commands |
| Any NMEA receiver | `generic` | No proprietary config, NMEA + RTCM3 only |

Adding a new module: create a `ModuleProfile` subclass in `module_profiles.py`.
```

- [ ] **Step 4: Run all tests + ruff**

Run: `ruff check . && python3 -m pytest tests/ -v`
Expected: All pass

- [ ] **Step 5: Commit**

```bash
git add status_display.py rtk_state.py rtk_controller.py README.md
git commit -m "feat: dynamic UI title from module profile, update docs"
```

---

### Task 5: Final integration verification

- [ ] **Step 1: Run full test suite**

Run: `python3 -m pytest tests/ -v`
Expected: All tests PASS

- [ ] **Step 2: Verify ruff passes**

Run: `ruff check .`
Expected: All checks passed

- [ ] **Step 3: Verify backward compatibility**

The default behavior (no `--gnss-module` flag) must be identical to current behavior — LC29H profile is used by default.

- [ ] **Step 4: Tag release**

```bash
git tag -a v0.2.0 -m "v0.2.0 - Multi-GNSS-module support"
git push && git push --tags
```
