# module_profiles.py - GNSS module profile abstraction for multi-module support

from abc import ABC, abstractmethod
from typing import Optional


class ModuleProfile(ABC):
    """Abstract base class for GNSS module profiles.

    Each profile encapsulates the proprietary command set and response parsing
    logic for a specific GNSS module family.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Machine-readable profile identifier (e.g. 'lc29h')."""

    @property
    @abstractmethod
    def display_name(self) -> str:
        """Human-readable module name (e.g. 'Quectel LC29H')."""

    @abstractmethod
    def firmware_command(self) -> Optional[str]:
        """Return the NMEA command to query firmware version, or None if unsupported."""

    @abstractmethod
    def parse_firmware_response(self, response: str) -> Optional[str]:
        """Extract the firmware version string from the module's response.

        Returns the parsed version string, or None if parsing fails.
        """

    @abstractmethod
    def config_commands(self) -> list[dict]:
        """Return a list of configuration command dicts.

        Each dict has:
          - 'cmd': the NMEA command string (without $ prefix or checksum)
          - 'ack': whether an ACK response is expected
        """

    @abstractmethod
    def check_ack(self, command: str, response: str) -> bool:
        """Check whether the response is a valid ACK for the given command.

        Args:
            command: The command that was sent (e.g. 'PAIR062,0,1').
            response: The raw response line from the module.

        Returns:
            True if the response is a successful ACK.
        """


class LC29HProfile(ModuleProfile):
    """Profile for Quectel LC29H (DA) GNSS modules.

    Uses PQTM commands for firmware queries and PAIR commands for configuration.
    """

    @property
    def name(self) -> str:
        return "lc29h"

    @property
    def display_name(self) -> str:
        return "Quectel LC29H"

    def firmware_command(self) -> Optional[str]:
        return "PQTMVERNO"

    def parse_firmware_response(self, response: str) -> Optional[str]:
        if not response or not response.startswith("$PQTMVERNO,"):
            return None
        parts = response.split(",")
        if len(parts) >= 2 and parts[1]:
            # Strip checksum suffix if present (e.g. "V1.0*3A")
            version = parts[1].split("*")[0]
            return version if version else None
        return None

    def config_commands(self) -> list[dict]:
        return [
            {"cmd": "PAIR062,0,1", "ack": True},  # GGA 1Hz
            {"cmd": "PAIR062,4,1", "ack": True},  # RMC 1Hz
            {"cmd": "PAIR062,2,1", "ack": True},  # GSA 1Hz
            {"cmd": "PAIR062,3,1", "ack": True},  # GSV 1Hz
            {"cmd": "PAIR062,5,1", "ack": True},  # VTG 1Hz
            {"cmd": "PAIR436,1", "ack": True},     # Enable RTCM input
            {"cmd": "PAIR513", "ack": True},        # Enable RTK mode
        ]

    def check_ack(self, command: str, response: str) -> bool:
        if not response:
            return False
        # Extract the command ID: strip the "PAIR" prefix to get the numeric part
        cmd_name = command.split(",")[0]
        cmd_id = cmd_name[4:] if cmd_name.startswith("PAIR") else cmd_name
        ack_prefix = f"$PAIR001,{cmd_id},0"
        return response.startswith(ack_prefix)


class GenericProfile(ModuleProfile):
    """Generic fallback profile for unknown GNSS modules.

    Does not send any proprietary commands. Useful for modules that only
    speak standard NMEA without vendor extensions.
    """

    @property
    def name(self) -> str:
        return "generic"

    @property
    def display_name(self) -> str:
        return "Generic GNSS"

    def firmware_command(self) -> Optional[str]:
        return None

    def parse_firmware_response(self, response: str) -> Optional[str]:
        return None

    def config_commands(self) -> list[dict]:
        return []

    def check_ack(self, command: str, response: str) -> bool:
        # Generic modules have no proprietary ACK mechanism
        return True


# ---------------------------------------------------------------------------
# Profile registry
# ---------------------------------------------------------------------------

_PROFILE_REGISTRY: dict[str, type[ModuleProfile]] = {
    "lc29h": LC29HProfile,
    "generic": GenericProfile,
}


def get_profile(module_name: str) -> ModuleProfile:
    """Look up a module profile by name (case-insensitive).

    Falls back to GenericProfile for unrecognised names.
    """
    key = module_name.strip().lower()
    profile_cls = _PROFILE_REGISTRY.get(key, GenericProfile)
    return profile_cls()


def list_profiles() -> list[str]:
    """Return sorted list of registered profile names."""
    return sorted(_PROFILE_REGISTRY.keys())
