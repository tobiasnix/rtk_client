from nmea_parser import NmeaParser
from rtk_constants import (
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
