# tests/test_module_profiles.py - Tests for GNSS module profile abstraction

import pytest

from module_profiles import (
    GenericProfile,
    LC29HProfile,
    ModuleProfile,
    get_profile,
    list_profiles,
)

# ---------------------------------------------------------------------------
# LC29HProfile tests
# ---------------------------------------------------------------------------

class TestLC29HProfile:
    @pytest.fixture()
    def profile(self):
        return LC29HProfile()

    def test_name(self, profile):
        assert profile.name == "lc29h"

    def test_display_name(self, profile):
        assert profile.display_name == "Quectel LC29H"

    def test_is_module_profile(self, profile):
        assert isinstance(profile, ModuleProfile)

    # -- firmware_command --

    def test_firmware_command(self, profile):
        assert profile.firmware_command() == "PQTMVERNO"

    # -- parse_firmware_response --

    def test_parse_firmware_response_valid(self, profile):
        resp = "$PQTMVERNO,LC29HDANR12A02S_RSA*3B"
        assert profile.parse_firmware_response(resp) == "LC29HDANR12A02S_RSA"

    def test_parse_firmware_response_no_checksum(self, profile):
        resp = "$PQTMVERNO,FW_V2.0"
        assert profile.parse_firmware_response(resp) == "FW_V2.0"

    def test_parse_firmware_response_wrong_prefix(self, profile):
        assert profile.parse_firmware_response("$GPGGA,123456,4807.038,N") is None

    def test_parse_firmware_response_empty(self, profile):
        assert profile.parse_firmware_response("") is None

    def test_parse_firmware_response_none_like(self, profile):
        # Only the comma, no version payload
        assert profile.parse_firmware_response("$PQTMVERNO,") is None

    def test_parse_firmware_response_too_few_parts(self, profile):
        assert profile.parse_firmware_response("$PQTMVERNO") is None

    # -- config_commands --

    def test_config_commands_count(self, profile):
        cmds = profile.config_commands()
        assert len(cmds) == 7

    def test_config_commands_are_dicts(self, profile):
        for item in profile.config_commands():
            assert "cmd" in item
            assert "ack" in item

    def test_config_commands_all_expect_ack(self, profile):
        for item in profile.config_commands():
            assert item["ack"] is True

    def test_config_commands_first_is_gga(self, profile):
        assert profile.config_commands()[0]["cmd"] == "PAIR062,0,1"

    def test_config_commands_last_is_rtk(self, profile):
        assert profile.config_commands()[-1]["cmd"] == "PAIR513"

    def test_config_commands_contains_rtcm_enable(self, profile):
        cmds = [item["cmd"] for item in profile.config_commands()]
        assert "PAIR436,1" in cmds

    # -- check_ack --

    def test_check_ack_success(self, profile):
        assert profile.check_ack("PAIR062,0,1", "$PAIR001,062,0*3F") is True

    def test_check_ack_success_no_checksum(self, profile):
        assert profile.check_ack("PAIR062,0,1", "$PAIR001,062,0") is True

    def test_check_ack_failure_wrong_code(self, profile):
        # Code 3 means error
        assert profile.check_ack("PAIR062,0,1", "$PAIR001,062,3*3C") is False

    def test_check_ack_failure_empty_response(self, profile):
        assert profile.check_ack("PAIR062,0,1", "") is False

    def test_check_ack_failure_different_command(self, profile):
        assert profile.check_ack("PAIR062,0,1", "$PAIR001,436,0*3E") is False

    def test_check_ack_pair513(self, profile):
        assert profile.check_ack("PAIR513", "$PAIR001,513,0*00") is True

    def test_check_ack_pair436(self, profile):
        assert profile.check_ack("PAIR436,1", "$PAIR001,436,0") is True


# ---------------------------------------------------------------------------
# GenericProfile tests
# ---------------------------------------------------------------------------

class TestGenericProfile:
    @pytest.fixture()
    def profile(self):
        return GenericProfile()

    def test_name(self, profile):
        assert profile.name == "generic"

    def test_display_name(self, profile):
        assert profile.display_name == "Generic GNSS"

    def test_is_module_profile(self, profile):
        assert isinstance(profile, ModuleProfile)

    def test_firmware_command_is_none(self, profile):
        assert profile.firmware_command() is None

    def test_parse_firmware_response_always_none(self, profile):
        assert profile.parse_firmware_response("anything") is None

    def test_config_commands_empty(self, profile):
        assert profile.config_commands() == []

    def test_check_ack_always_true(self, profile):
        assert profile.check_ack("ANY_CMD", "") is True
        assert profile.check_ack("ANY_CMD", "whatever") is True
        assert profile.check_ack("", "") is True


# ---------------------------------------------------------------------------
# get_profile() registry tests
# ---------------------------------------------------------------------------

class TestGetProfile:
    def test_get_lc29h(self):
        p = get_profile("lc29h")
        assert isinstance(p, LC29HProfile)

    def test_get_generic(self):
        p = get_profile("generic")
        assert isinstance(p, GenericProfile)

    def test_unknown_falls_back_to_generic(self):
        p = get_profile("ublox_f9p")
        assert isinstance(p, GenericProfile)

    def test_case_insensitive_upper(self):
        p = get_profile("LC29H")
        assert isinstance(p, LC29HProfile)

    def test_case_insensitive_mixed(self):
        p = get_profile("Lc29h")
        assert isinstance(p, LC29HProfile)

    def test_whitespace_stripped(self):
        p = get_profile("  lc29h  ")
        assert isinstance(p, LC29HProfile)

    def test_empty_string_falls_back(self):
        p = get_profile("")
        assert isinstance(p, GenericProfile)


# ---------------------------------------------------------------------------
# list_profiles() tests
# ---------------------------------------------------------------------------

class TestListProfiles:
    def test_returns_list(self):
        result = list_profiles()
        assert isinstance(result, list)

    def test_contains_known_profiles(self):
        result = list_profiles()
        assert "lc29h" in result
        assert "generic" in result

    def test_is_sorted(self):
        result = list_profiles()
        assert result == sorted(result)
