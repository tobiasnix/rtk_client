import contextlib
from unittest.mock import MagicMock, patch

import pynmea2

from nmea_parser import NmeaParser
from rtk_constants import (
    DEFAULT_HDOP,
    FIX_QUALITY_DGPS,
    FIX_QUALITY_ESTIMATED,
    FIX_QUALITY_GPS,
    FIX_QUALITY_INVALID,
    FIX_QUALITY_RTK_FIXED,
    FIX_QUALITY_RTK_FLOAT,
)
from rtk_state import GnssState


class TestGetFixStatusString:
    def setup_method(self):
        self.state = GnssState(0.0, 0.0, 0.0)
        self.parser = NmeaParser(self.state)

    def test_rtk_fixed(self):
        assert self.parser._get_fix_status_string(FIX_QUALITY_RTK_FIXED) == "RTK Fixed"

    def test_rtk_float(self):
        assert self.parser._get_fix_status_string(FIX_QUALITY_RTK_FLOAT) == "RTK Float"

    def test_dgps(self):
        assert self.parser._get_fix_status_string(FIX_QUALITY_DGPS) == "DGPS"

    def test_gps(self):
        assert self.parser._get_fix_status_string(FIX_QUALITY_GPS) == "GPS (SPS)"

    def test_invalid(self):
        assert self.parser._get_fix_status_string(FIX_QUALITY_INVALID) == "No Fix / Invalid"

    def test_estimated(self):
        assert self.parser._get_fix_status_string(FIX_QUALITY_ESTIMATED) == "Estimated (DR)"

    def test_unknown(self):
        result = self.parser._get_fix_status_string(99)
        assert "Unknown" in result
        assert "99" in result


class TestCalculateSnrStats:
    def setup_method(self):
        self.state = GnssState(0.0, 0.0, 0.0)
        self.parser = NmeaParser(self.state)

    def test_empty_satellites(self):
        stats = self.parser._calculate_snr_stats({})
        assert stats["min"] == 0.0
        assert stats["max"] == 0.0
        assert stats["avg"] == 0.0
        assert stats["good_count"] == 0
        assert stats["bad_count"] == 0

    def test_all_zero_snr(self):
        sats = {
            "GP-1": {"snr": 0},
            "GP-2": {"snr": 0},
        }
        stats = self.parser._calculate_snr_stats(sats)
        assert stats["min"] == 0.0
        assert stats["good_count"] == 0

    def test_normal_snr_values(self):
        sats = {
            "GP-1": {"snr": 40},
            "GP-2": {"snr": 30},
            "GP-3": {"snr": 15},
        }
        stats = self.parser._calculate_snr_stats(sats)
        assert stats["min"] == 15.0
        assert stats["max"] == 40.0
        assert abs(stats["avg"] - 28.33) < 0.1
        assert stats["good_count"] == 1  # >= 35
        assert stats["bad_count"] == 1   # <= 20

    def test_all_good_snr(self):
        sats = {
            "GP-1": {"snr": 40},
            "GP-2": {"snr": 45},
            "GP-3": {"snr": 50},
        }
        stats = self.parser._calculate_snr_stats(sats)
        assert stats["good_count"] == 3
        assert stats["bad_count"] == 0

    def test_missing_snr_key(self):
        sats = {
            "GP-1": {"elevation": 45},  # no snr key
        }
        stats = self.parser._calculate_snr_stats(sats)
        assert stats["min"] == 0.0


# ---------------------------------------------------------------------------
# TestParse - The public parse() entry point
# ---------------------------------------------------------------------------


class TestParse:
    def setup_method(self):
        self.state = GnssState(0.0, 0.0, 0.0)
        self.parser = NmeaParser(self.state)

    @patch("nmea_parser.pynmea2")
    def test_dispatches_gga(self, mock_pynmea2):
        """parse() dispatches GGA sentence to _parse_gga."""
        mock_msg = MagicMock(spec=pynmea2.types.talker.GGA)
        mock_pynmea2.parse.return_value = mock_msg
        mock_pynmea2.types.talker.GGA = pynmea2.types.talker.GGA
        mock_pynmea2.types.talker.GSV = pynmea2.types.talker.GSV
        mock_pynmea2.types.talker.GSA = pynmea2.types.talker.GSA
        mock_pynmea2.ParseError = pynmea2.ParseError

        with patch.object(self.parser, "_parse_gga") as mock_parse_gga:
            self.parser.parse("$GPGGA,dummy*00")
            mock_parse_gga.assert_called_once_with(mock_msg)

    @patch("nmea_parser.pynmea2")
    def test_dispatches_gsv(self, mock_pynmea2):
        """parse() dispatches GSV sentence to _parse_gsv."""
        mock_msg = MagicMock(spec=pynmea2.types.talker.GSV)
        mock_pynmea2.parse.return_value = mock_msg
        mock_pynmea2.types.talker.GGA = pynmea2.types.talker.GGA
        mock_pynmea2.types.talker.GSV = pynmea2.types.talker.GSV
        mock_pynmea2.types.talker.GSA = pynmea2.types.talker.GSA
        mock_pynmea2.ParseError = pynmea2.ParseError

        with patch.object(self.parser, "_parse_gsv") as mock_parse_gsv:
            self.parser.parse("$GPGSV,dummy*00")
            mock_parse_gsv.assert_called_once_with(mock_msg)

    @patch("nmea_parser.pynmea2")
    def test_dispatches_gsa(self, mock_pynmea2):
        """parse() dispatches GSA sentence to _parse_gsa."""
        mock_msg = MagicMock(spec=pynmea2.types.talker.GSA)
        mock_pynmea2.parse.return_value = mock_msg
        mock_pynmea2.types.talker.GGA = pynmea2.types.talker.GGA
        mock_pynmea2.types.talker.GSV = pynmea2.types.talker.GSV
        mock_pynmea2.types.talker.GSA = pynmea2.types.talker.GSA
        mock_pynmea2.ParseError = pynmea2.ParseError

        with patch.object(self.parser, "_parse_gsa") as mock_parse_gsa:
            self.parser.parse("$GPGSA,dummy*00")
            mock_parse_gsa.assert_called_once_with(mock_msg)

    @patch("nmea_parser.pynmea2")
    def test_handles_parse_error(self, mock_pynmea2):
        """parse() catches pynmea2.ParseError and returns without crashing."""
        mock_pynmea2.parse.side_effect = pynmea2.ParseError("bad sentence", "garbage data")
        mock_pynmea2.ParseError = pynmea2.ParseError

        # Should not raise
        self.parser.parse("$GARBAGE*FF")

    def test_handles_empty_string(self):
        """parse() returns early for empty string."""
        with patch.object(self.parser, "_parse_gga") as mock_gga:
            self.parser.parse("")
            mock_gga.assert_not_called()

    def test_handles_whitespace_only(self):
        """parse() returns early for whitespace-only string."""
        with patch.object(self.parser, "_parse_gga") as mock_gga:
            self.parser.parse("   \t\n  ")
            mock_gga.assert_not_called()

    @patch("nmea_parser.pynmea2")
    def test_ignores_non_gga_gsv_gsa_sentence(self, mock_pynmea2):
        """parse() ignores sentence types other than GGA/GSV/GSA (e.g., RMC)."""
        mock_msg = MagicMock()  # Not a GGA, GSV, or GSA instance
        mock_pynmea2.parse.return_value = mock_msg
        mock_pynmea2.types.talker.GGA = pynmea2.types.talker.GGA
        mock_pynmea2.types.talker.GSV = pynmea2.types.talker.GSV
        mock_pynmea2.types.talker.GSA = pynmea2.types.talker.GSA
        mock_pynmea2.ParseError = pynmea2.ParseError

        with patch.object(self.parser, "_parse_gga") as mock_gga, \
             patch.object(self.parser, "_parse_gsv") as mock_gsv, \
             patch.object(self.parser, "_parse_gsa") as mock_gsa:
            self.parser.parse("$GPRMC,dummy*00")
            mock_gga.assert_not_called()
            mock_gsv.assert_not_called()
            mock_gsa.assert_not_called()


# ---------------------------------------------------------------------------
# TestParseGga - The _parse_gga method
# ---------------------------------------------------------------------------


def _make_gga_msg(gps_qual=4, latitude=40.109, longitude=-7.154,
                  altitude="476.68", num_sats="12", horizontal_dil="0.8"):
    """Helper to create a mock GGA message with sensible defaults."""
    msg = MagicMock(spec=pynmea2.types.talker.GGA)
    msg.gps_qual = gps_qual
    msg.latitude = latitude
    msg.longitude = longitude
    msg.altitude = altitude
    msg.num_sats = num_sats
    msg.horizontal_dil = horizontal_dil
    return msg


class TestParseGga:
    def setup_method(self):
        self.state = GnssState(0.0, 0.0, 0.0)
        self.parser = NmeaParser(self.state)

    def test_rtk_fixed_quality(self):
        """RTK Fixed (gps_qual=4): position updated, rtk_status, have_position_lock."""
        msg = _make_gga_msg(gps_qual=4, latitude=40.5, longitude=-7.2, altitude="100.0")
        self.parser._parse_gga(msg)

        snap = self.state.get_state_snapshot()
        assert snap["rtk_status"] == "RTK Fixed"
        assert snap["have_position_lock"] is True
        assert snap["position"]["lat"] == 40.5
        assert snap["position"]["lon"] == -7.2
        assert snap["position"]["alt"] == 100.0
        assert snap["fix_type"] == FIX_QUALITY_RTK_FIXED

    def test_gps_fix_quality(self):
        """GPS fix (gps_qual=1): rtk_status is 'GPS (SPS)'."""
        msg = _make_gga_msg(gps_qual=1)
        self.parser._parse_gga(msg)

        snap = self.state.get_state_snapshot()
        assert snap["rtk_status"] == "GPS (SPS)"
        assert snap["have_position_lock"] is True

    def test_no_fix_quality(self):
        """No fix (gps_qual=0): have_position_lock is False."""
        msg = _make_gga_msg(gps_qual=0)
        self.parser._parse_gga(msg)

        snap = self.state.get_state_snapshot()
        assert snap["have_position_lock"] is False
        assert snap["rtk_status"] == "No Fix / Invalid"

    def test_empty_gps_qual_defaults_to_invalid(self):
        """Empty gps_qual defaults to FIX_QUALITY_INVALID."""
        msg = _make_gga_msg(gps_qual="")
        self.parser._parse_gga(msg)

        snap = self.state.get_state_snapshot()
        assert snap["fix_type"] == FIX_QUALITY_INVALID
        assert snap["have_position_lock"] is False

    def test_altitude_valid_float(self):
        """Altitude parsing: valid float value is stored."""
        msg = _make_gga_msg(gps_qual=4, altitude="123.45")
        self.parser._parse_gga(msg)

        snap = self.state.get_state_snapshot()
        assert snap["position"]["alt"] == 123.45

    def test_altitude_empty_uses_default(self):
        """Altitude parsing: empty string uses current/default altitude."""
        msg = _make_gga_msg(gps_qual=4, altitude="")
        self.parser._parse_gga(msg)

        snap = self.state.get_state_snapshot()
        # Default alt is 0.0 (from GnssState(0.0, 0.0, 0.0))
        assert snap["position"]["alt"] == 0.0

    def test_altitude_non_numeric_uses_default(self):
        """Altitude parsing: non-numeric string uses current/default altitude."""
        msg = _make_gga_msg(gps_qual=4, altitude="not_a_number")
        self.parser._parse_gga(msg)

        snap = self.state.get_state_snapshot()
        assert snap["position"]["alt"] == 0.0

    def test_num_sats_parsed(self):
        """Num sats is parsed correctly from the GGA message."""
        msg = _make_gga_msg(num_sats="15")
        self.parser._parse_gga(msg)

        snap = self.state.get_state_snapshot()
        assert snap["num_satellites_used"] == 15

    def test_hdop_parsed(self):
        """HDOP is parsed correctly from the GGA message."""
        msg = _make_gga_msg(horizontal_dil="1.23")
        self.parser._parse_gga(msg)

        snap = self.state.get_state_snapshot()
        assert snap["hdop"] == 1.23

    def test_hdop_empty_uses_default(self):
        """HDOP empty string uses DEFAULT_HDOP."""
        msg = _make_gga_msg(horizontal_dil="")
        self.parser._parse_gga(msg)

        snap = self.state.get_state_snapshot()
        assert snap["hdop"] == DEFAULT_HDOP

    def test_first_fix_time_recorded_on_first_valid_fix(self):
        """first_fix_time_sec is recorded on first valid fix."""
        assert self.state.first_fix_time_sec is None

        msg = _make_gga_msg(gps_qual=4)
        self.parser._parse_gga(msg)

        snap = self.state.get_state_snapshot()
        assert snap["first_fix_time_sec"] is not None
        assert snap["first_fix_time_sec"] >= 0.0

    def test_first_fix_time_not_overwritten_on_subsequent_fix(self):
        """first_fix_time_sec is not overwritten on subsequent fixes."""
        msg = _make_gga_msg(gps_qual=4)
        self.parser._parse_gga(msg)

        snap1 = self.state.get_state_snapshot()
        first_ttff = snap1["first_fix_time_sec"]
        assert first_ttff is not None

        # Second fix should not overwrite
        msg2 = _make_gga_msg(gps_qual=4)
        self.parser._parse_gga(msg2)

        snap2 = self.state.get_state_snapshot()
        assert snap2["first_fix_time_sec"] == first_ttff

    def test_status_change_logged_to_ui(self):
        """Status change is logged to UI when rtk_status changes."""
        # Initial state has rtk_status="Unknown"
        msg = _make_gga_msg(gps_qual=4)
        self.parser._parse_gga(msg)

        snap = self.state.get_state_snapshot()
        # Check that a UI log message was added about the fix status change
        ui_messages = list(snap["ui_log_messages"])
        assert len(ui_messages) >= 1
        assert any("RTK Fixed" in m for m in ui_messages)

    def test_status_change_not_logged_when_unchanged(self):
        """No UI log when rtk_status does not change between consecutive GGA messages."""
        msg1 = _make_gga_msg(gps_qual=4)
        self.parser._parse_gga(msg1)

        # Clear the ui_log_messages
        self.state.ui_log_messages.clear()

        # Same fix quality again
        msg2 = _make_gga_msg(gps_qual=4)
        self.parser._parse_gga(msg2)

        snap = self.state.get_state_snapshot()
        ui_messages = list(snap["ui_log_messages"])
        # No new status-change message should have been added
        fix_status_msgs = [m for m in ui_messages if "Fix status" in m]
        assert len(fix_status_msgs) == 0

    def test_num_sats_empty_defaults_to_zero(self):
        """Empty num_sats defaults to 0."""
        msg = _make_gga_msg(num_sats="")
        self.parser._parse_gga(msg)

        snap = self.state.get_state_snapshot()
        assert snap["num_satellites_used"] == 0

    def test_rtk_float_quality(self):
        """RTK Float (gps_qual=5): rtk_status is 'RTK Float'."""
        msg = _make_gga_msg(gps_qual=5)
        self.parser._parse_gga(msg)

        snap = self.state.get_state_snapshot()
        assert snap["rtk_status"] == "RTK Float"
        assert snap["have_position_lock"] is True


# ---------------------------------------------------------------------------
# TestParseGsv - The _parse_gsv method
# ---------------------------------------------------------------------------


def _make_gsv_msg(talker="GP", num_messages=1, msg_num=1, num_sv_in_view=1,
                  sats=None):
    """Helper to create a mock GSV message.

    sats: list of dicts with keys prn, elev, azim, snr (up to 4 per message).
    """
    msg = MagicMock()
    msg.talker = talker
    msg.num_messages = str(num_messages)
    msg.msg_num = str(msg_num)
    msg.num_sv_in_view = str(num_sv_in_view)

    if sats is None:
        sats = [{"prn": "1", "elev": "45", "azim": "180", "snr": "35"}]

    # Set satellite fields for indices 1-4
    for i in range(1, 5):
        idx = i - 1
        if idx < len(sats):
            sat = sats[idx]
            setattr(msg, f"sv_prn_num_{i}", sat.get("prn", ""))
            setattr(msg, f"elevation_deg_{i}", sat.get("elev", ""))
            setattr(msg, f"azimuth_{i}", sat.get("azim", ""))
            setattr(msg, f"snr_{i}", sat.get("snr", ""))
        else:
            # Remove attributes so hasattr returns False for missing sats
            # MagicMock auto-creates attrs on access, so use spec or delattr
            # Instead, we configure hasattr to return False by using a side_effect
            # Simplest: don't set the attributes and override __contains__
            pass

    # For indices beyond the provided sats, ensure hasattr returns False
    # We achieve this by making the mock NOT auto-create those attributes
    # Use a custom spec to limit attributes
    existing_attrs = set()
    for i in range(1, 5):
        idx = i - 1
        if idx < len(sats):
            for prefix in ["sv_prn_num_", "elevation_deg_", "azimuth_", "snr_"]:
                existing_attrs.add(f"{prefix}{i}")

    # Store original hasattr behavior and override
    real_attrs = {
        "talker", "num_messages", "msg_num", "num_sv_in_view"
    } | existing_attrs

    def custom_hasattr(name):
        return name in real_attrs

    # Override the mock's attribute access for missing sat fields
    for i in range(1, 5):
        idx = i - 1
        if idx >= len(sats):
            for prefix in ["sv_prn_num_", "elevation_deg_", "azimuth_", "snr_"]:
                attr_name = f"{prefix}{i}"
                # Delete auto-created attributes
                with contextlib.suppress(AttributeError):
                    delattr(msg, attr_name)

    # Use a wrapper to make hasattr work correctly for satellite fields
    orig_getattr = type(msg).__getattribute__

    def controlled_getattr(self_mock, name):
        if name.startswith(("sv_prn_num_", "elevation_deg_", "azimuth_", "snr_")) and name not in existing_attrs:
            raise AttributeError(name)
        return orig_getattr(self_mock, name)

    type(msg).__getattribute__ = controlled_getattr

    return msg


def _make_simple_gsv_msg(talker="GP", num_messages=1, msg_num=1,
                         num_sv_in_view=1, sats=None):
    """Simpler helper that sets all 4 satellite slots, using empty strings for missing ones."""
    msg = MagicMock()
    msg.talker = talker
    msg.num_messages = str(num_messages)
    msg.msg_num = str(msg_num)
    msg.num_sv_in_view = str(num_sv_in_view)

    if sats is None:
        sats = [{"prn": "1", "elev": "45", "azim": "180", "snr": "35"}]

    for i in range(1, 5):
        idx = i - 1
        if idx < len(sats):
            sat = sats[idx]
            setattr(msg, f"sv_prn_num_{i}", sat.get("prn", ""))
            setattr(msg, f"elevation_deg_{i}", sat.get("elev", ""))
            setattr(msg, f"azimuth_{i}", sat.get("azim", ""))
            setattr(msg, f"snr_{i}", sat.get("snr", ""))
        else:
            setattr(msg, f"sv_prn_num_{i}", "")
            setattr(msg, f"elevation_deg_{i}", "")
            setattr(msg, f"azimuth_{i}", "")
            setattr(msg, f"snr_{i}", "")

    return msg


class TestParseGsv:
    def setup_method(self):
        self.state = GnssState(0.0, 0.0, 0.0)
        self.parser = NmeaParser(self.state)

    def test_single_complete_gsv_sequence(self):
        """Single complete GSV sequence (1 of 1) updates state."""
        msg = _make_simple_gsv_msg(
            talker="GP", num_messages=1, msg_num=1, num_sv_in_view=2,
            sats=[
                {"prn": "5", "elev": "45", "azim": "180", "snr": "35"},
                {"prn": "10", "elev": "30", "azim": "90", "snr": "28"},
            ]
        )
        self.parser._parse_gsv(msg)

        snap = self.state.get_state_snapshot()
        assert "GP-5" in snap["satellites_info"]
        assert "GP-10" in snap["satellites_info"]
        assert snap["satellites_info"]["GP-5"]["snr"] == 35
        assert snap["satellites_info"]["GP-10"]["snr"] == 28
        assert snap["num_satellites_in_view"] == 2

    def test_multi_sentence_gsv_first_clears_last_commits(self):
        """Multi-sentence GSV: first message clears temp, last commits to state."""
        # First message of 2
        msg1 = _make_simple_gsv_msg(
            talker="GP", num_messages=2, msg_num=1, num_sv_in_view=5,
            sats=[
                {"prn": "1", "elev": "10", "azim": "100", "snr": "20"},
                {"prn": "2", "elev": "20", "azim": "200", "snr": "25"},
                {"prn": "3", "elev": "30", "azim": "300", "snr": "30"},
                {"prn": "4", "elev": "40", "azim": "45", "snr": "40"},
            ]
        )
        self.parser._parse_gsv(msg1)

        # State should NOT be updated yet (not last sentence)
        snap_mid = self.state.get_state_snapshot()
        assert len(snap_mid["satellites_info"]) == 0

        # Second (last) message of 2
        msg2 = _make_simple_gsv_msg(
            talker="GP", num_messages=2, msg_num=2, num_sv_in_view=5,
            sats=[
                {"prn": "5", "elev": "50", "azim": "50", "snr": "45"},
            ]
        )
        self.parser._parse_gsv(msg2)

        # Now state should be updated with all 5 satellites
        snap = self.state.get_state_snapshot()
        assert len(snap["satellites_info"]) == 5
        assert snap["num_satellites_in_view"] == 5

    def test_talker_gp_maps_to_gps(self):
        """GP talker maps to 'GPS' system name."""
        msg = _make_simple_gsv_msg(
            talker="GP", num_messages=1, msg_num=1, num_sv_in_view=1,
            sats=[{"prn": "1", "elev": "45", "azim": "180", "snr": "35"}]
        )
        self.parser._parse_gsv(msg)

        snap = self.state.get_state_snapshot()
        assert snap["satellites_info"]["GP-1"]["system"] == "GPS"

    def test_talker_ga_maps_to_galileo(self):
        """GA talker maps to 'Galileo' system name."""
        msg = _make_simple_gsv_msg(
            talker="GA", num_messages=1, msg_num=1, num_sv_in_view=1,
            sats=[{"prn": "1", "elev": "45", "azim": "180", "snr": "35"}]
        )
        self.parser._parse_gsv(msg)

        snap = self.state.get_state_snapshot()
        assert snap["satellites_info"]["GA-1"]["system"] == "Galileo"

    def test_talker_gb_maps_to_beidou(self):
        """GB talker maps to 'BeiDou' system name."""
        msg = _make_simple_gsv_msg(
            talker="GB", num_messages=1, msg_num=1, num_sv_in_view=1,
            sats=[{"prn": "11", "elev": "50", "azim": "270", "snr": "30"}]
        )
        self.parser._parse_gsv(msg)

        snap = self.state.get_state_snapshot()
        assert snap["satellites_info"]["GB-11"]["system"] == "BeiDou"

    def test_talker_gl_maps_to_glonass(self):
        """GL talker maps to 'GLONASS' system name."""
        msg = _make_simple_gsv_msg(
            talker="GL", num_messages=1, msg_num=1, num_sv_in_view=1,
            sats=[{"prn": "71", "elev": "60", "azim": "120", "snr": "32"}]
        )
        self.parser._parse_gsv(msg)

        snap = self.state.get_state_snapshot()
        assert snap["satellites_info"]["GL-71"]["system"] == "GLONASS"

    def test_missing_satellite_fields_graceful_skip(self):
        """Missing satellite fields: satellite is gracefully skipped."""
        msg = _make_simple_gsv_msg(
            talker="GP", num_messages=1, msg_num=1, num_sv_in_view=1,
            sats=[
                {"prn": "5", "elev": "45", "azim": "180", "snr": "35"},
            ]
        )
        # Simulate missing PRN for slot 2 (empty string, will be skipped)
        # Slot 2 already has empty strings from _make_simple_gsv_msg
        self.parser._parse_gsv(msg)

        snap = self.state.get_state_snapshot()
        assert "GP-5" in snap["satellites_info"]
        # Only 1 satellite should be present (slot 2-4 have empty PRNs)
        assert len(snap["satellites_info"]) == 1

    def test_zero_snr_satellite_counted_but_not_in_system_count(self):
        """Zero SNR: satellite is recorded but not counted in system satellite counts."""
        msg = _make_simple_gsv_msg(
            talker="GP", num_messages=1, msg_num=1, num_sv_in_view=2,
            sats=[
                {"prn": "1", "elev": "45", "azim": "180", "snr": "35"},
                {"prn": "2", "elev": "30", "azim": "90", "snr": "0"},
            ]
        )
        self.parser._parse_gsv(msg)

        snap = self.state.get_state_snapshot()
        # Both satellites should be in satellites_info
        assert "GP-1" in snap["satellites_info"]
        assert "GP-2" in snap["satellites_info"]
        # Only 1 should be counted in system counts (snr > 0)
        assert snap["satellite_systems"]["GPS"] == 1

    def test_invalid_sentence_num_zero_returns_early(self):
        """Invalid sequence number (sentence_num=0): early return, no state update."""
        msg = _make_simple_gsv_msg(
            talker="GP", num_messages=1, msg_num=0, num_sv_in_view=1,
            sats=[{"prn": "1", "elev": "45", "azim": "180", "snr": "35"}]
        )
        self.parser._parse_gsv(msg)

        snap = self.state.get_state_snapshot()
        assert len(snap["satellites_info"]) == 0

    def test_snr_stats_updated_on_last_sentence(self):
        """SNR stats are calculated and updated when last GSV sentence is processed."""
        msg = _make_simple_gsv_msg(
            talker="GP", num_messages=1, msg_num=1, num_sv_in_view=2,
            sats=[
                {"prn": "1", "elev": "45", "azim": "180", "snr": "40"},
                {"prn": "2", "elev": "30", "azim": "90", "snr": "20"},
            ]
        )
        self.parser._parse_gsv(msg)

        snap = self.state.get_state_snapshot()
        assert snap["snr_stats"]["min"] == 20.0
        assert snap["snr_stats"]["max"] == 40.0
        assert snap["snr_stats"]["avg"] == 30.0

    def test_first_sentence_clears_previous_sequence_data(self):
        """First sentence of a new sequence clears data from the previous sequence."""
        # Process a complete first sequence
        msg1 = _make_simple_gsv_msg(
            talker="GP", num_messages=1, msg_num=1, num_sv_in_view=1,
            sats=[{"prn": "99", "elev": "10", "azim": "10", "snr": "10"}]
        )
        self.parser._parse_gsv(msg1)

        # Start a new sequence (msg 1 of 2) - should clear temp data
        msg2 = _make_simple_gsv_msg(
            talker="GP", num_messages=2, msg_num=1, num_sv_in_view=1,
            sats=[{"prn": "1", "elev": "45", "azim": "180", "snr": "35"}]
        )
        self.parser._parse_gsv(msg2)

        # The internal sequence data should NOT contain the old satellite
        assert "GP-99" not in self.parser._current_gsv_sequence_sats
        assert "GP-1" in self.parser._current_gsv_sequence_sats


# ---------------------------------------------------------------------------
# TestParseGsa - The _parse_gsa method
# ---------------------------------------------------------------------------


def _make_gsa_msg(talker="GP", active_prns=None):
    """Helper to create a mock GSA message.

    active_prns: list of PRN strings for active satellites (up to 12).
    """
    msg = MagicMock()
    msg.talker = talker

    if active_prns is None:
        active_prns = []

    for i in range(1, 13):
        field_name = f"sv_id{i:02}"
        idx = i - 1
        if idx < len(active_prns):
            setattr(msg, field_name, active_prns[idx])
        else:
            setattr(msg, field_name, "")

    return msg


class TestParseGsa:
    def setup_method(self):
        self.state = GnssState(0.0, 0.0, 0.0)
        self.parser = NmeaParser(self.state)

    def _populate_gsv_satellites(self, sats_dict):
        """Helper to populate satellites_info in state from a dict.

        sats_dict: dict of key -> sat_info, e.g. {"GP-5": {"prn": "5", "snr": 35, ...}}
        """
        self.state.satellites_info = sats_dict
        # Also populate the parser's internal GSV sequence data
        self.parser._current_gsv_sequence_sats = dict(sats_dict)

    def test_marks_satellites_active_based_on_prn(self):
        """GSA marks satellites as active based on PRN."""
        self._populate_gsv_satellites({
            "GP-5": {"prn": "5", "snr": 35, "elevation": 45, "azimuth": 180,
                     "system": "GPS", "active": False},
            "GP-10": {"prn": "10", "snr": 28, "elevation": 30, "azimuth": 90,
                      "system": "GPS", "active": False},
            "GP-15": {"prn": "15", "snr": 20, "elevation": 15, "azimuth": 270,
                      "system": "GPS", "active": False},
        })

        msg = _make_gsa_msg(talker="GP", active_prns=["5", "10"])
        self.parser._parse_gsa(msg)

        assert self.state.satellites_info["GP-5"]["active"] is True
        assert self.state.satellites_info["GP-10"]["active"] is True
        assert self.state.satellites_info["GP-15"]["active"] is False

    def test_gn_talker_cross_constellation_lookup(self):
        """GN talker performs cross-constellation lookup to find the right satellite."""
        self._populate_gsv_satellites({
            "GP-5": {"prn": "5", "snr": 35, "elevation": 45, "azimuth": 180,
                     "system": "GPS", "active": False},
            "GA-10": {"prn": "10", "snr": 28, "elevation": 30, "azimuth": 90,
                      "system": "Galileo", "active": False},
        })

        # GN talker with PRN "5" should match GP-5 (lookup by prn value)
        msg = _make_gsa_msg(talker="GN", active_prns=["5"])
        self.parser._parse_gsa(msg)

        assert self.state.satellites_info["GP-5"]["active"] is True

    def test_gn_talker_matches_galileo_satellite(self):
        """GN talker can match Galileo satellites via cross-constellation lookup."""
        self._populate_gsv_satellites({
            "GP-5": {"prn": "5", "snr": 35, "elevation": 45, "azimuth": 180,
                     "system": "GPS", "active": False},
            "GA-10": {"prn": "10", "snr": 28, "elevation": 30, "azimuth": 90,
                      "system": "Galileo", "active": False},
        })

        msg = _make_gsa_msg(talker="GN", active_prns=["10"])
        self.parser._parse_gsa(msg)

        assert self.state.satellites_info["GA-10"]["active"] is True

    def test_satellite_not_in_gsv_data_no_crash(self):
        """Satellite referenced by GSA but not in GSV data does not crash."""
        self._populate_gsv_satellites({
            "GP-5": {"prn": "5", "snr": 35, "elevation": 45, "azimuth": 180,
                     "system": "GPS", "active": False},
        })

        # PRN 99 is not in GSV data
        msg = _make_gsa_msg(talker="GP", active_prns=["99"])
        # Should not raise
        self.parser._parse_gsa(msg)

        # GP-5 should remain inactive (not referenced)
        assert self.state.satellites_info["GP-5"]["active"] is False

    def test_deactivation_of_previously_active_satellites(self):
        """Previously active satellites are deactivated if not in current GSA."""
        self._populate_gsv_satellites({
            "GP-5": {"prn": "5", "snr": 35, "elevation": 45, "azimuth": 180,
                     "system": "GPS", "active": True},
            "GP-10": {"prn": "10", "snr": 28, "elevation": 30, "azimuth": 90,
                      "system": "GPS", "active": True},
            "GP-15": {"prn": "15", "snr": 20, "elevation": 15, "azimuth": 270,
                      "system": "GPS", "active": False},
        })

        # Only PRN 10 is now active
        msg = _make_gsa_msg(talker="GP", active_prns=["10"])
        self.parser._parse_gsa(msg)

        # GP-5 was active, should now be deactivated
        assert self.state.satellites_info["GP-5"]["active"] is False
        # GP-10 stays active
        assert self.state.satellites_info["GP-10"]["active"] is True
        # GP-15 was already inactive, stays inactive
        assert self.state.satellites_info["GP-15"]["active"] is False

    def test_gsa_does_not_affect_other_constellation_satellites(self):
        """A GP GSA message does not deactivate Galileo satellites."""
        self._populate_gsv_satellites({
            "GP-5": {"prn": "5", "snr": 35, "elevation": 45, "azimuth": 180,
                     "system": "GPS", "active": True},
            "GA-1": {"prn": "1", "snr": 30, "elevation": 60, "azimuth": 120,
                     "system": "Galileo", "active": True},
        })

        # GP GSA with no active PRNs
        msg = _make_gsa_msg(talker="GP", active_prns=[])
        self.parser._parse_gsa(msg)

        # GP-5 should be deactivated (GP talker matches)
        assert self.state.satellites_info["GP-5"]["active"] is False
        # GA-1 should remain active (GP GSA does not affect GA satellites)
        assert self.state.satellites_info["GA-1"]["active"] is True

    def test_gn_talker_deactivates_across_constellations(self):
        """GN talker deactivates satellites across all constellations when not in active list."""
        self._populate_gsv_satellites({
            "GP-5": {"prn": "5", "snr": 35, "elevation": 45, "azimuth": 180,
                     "system": "GPS", "active": True},
            "GA-1": {"prn": "1", "snr": 30, "elevation": 60, "azimuth": 120,
                     "system": "Galileo", "active": True},
        })

        # GN GSA with only GP-5 active (via cross-constellation lookup)
        msg = _make_gsa_msg(talker="GN", active_prns=["5"])
        self.parser._parse_gsa(msg)

        assert self.state.satellites_info["GP-5"]["active"] is True
        # GA-1 should be deactivated (GN talker is relevant to all)
        assert self.state.satellites_info["GA-1"]["active"] is False

    def test_empty_gsa_message(self):
        """GSA message with no active PRNs deactivates all relevant satellites."""
        self._populate_gsv_satellites({
            "GP-5": {"prn": "5", "snr": 35, "elevation": 45, "azimuth": 180,
                     "system": "GPS", "active": True},
        })

        msg = _make_gsa_msg(talker="GP", active_prns=[])
        self.parser._parse_gsa(msg)

        assert self.state.satellites_info["GP-5"]["active"] is False
