import csv
import os
import tempfile

from position_logger import PositionLogger
from rtk_state import GnssState


class TestPositionLoggerHeader:
    def test_header_written_on_creation(self):
        state = GnssState(0.0, 0.0, 0.0)
        with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False) as f:
            fname = f.name
        try:
            PositionLogger(state, fname, interval=1.0)
            with open(fname, newline='') as f:
                reader = csv.reader(f)
                header = next(reader)
            assert header == [
                'timestamp', 'lat', 'lon', 'alt',
                'fix_type', 'rtk_status', 'num_sats', 'hdop'
            ]
        finally:
            os.unlink(fname)

    def test_only_header_when_no_logging(self):
        state = GnssState(0.0, 0.0, 0.0)
        with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False) as f:
            fname = f.name
        try:
            PositionLogger(state, fname, interval=1.0)
            with open(fname, newline='') as f:
                rows = list(csv.reader(f))
            assert len(rows) == 1  # header only
        finally:
            os.unlink(fname)


class TestPositionLoggerLogging:
    def test_position_logged_with_lock(self):
        state = GnssState(0.0, 0.0, 0.0)
        state.update(
            have_position_lock=True,
            position={"lat": 48.123, "lon": 11.456, "alt": 500.0},
            fix_type=4,
            rtk_status="RTK Fixed",
            num_satellites_used=12,
            hdop=0.8,
        )
        with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False) as f:
            fname = f.name
        try:
            pl = PositionLogger(state, fname, interval=0.1)
            pl.start()
            # Give the logger thread time to write at least one entry
            import time
            time.sleep(0.3)
            pl.stop()

            with open(fname, newline='') as f:
                rows = list(csv.reader(f))
            # At least header + 1 data row
            assert len(rows) >= 2
            data_row = rows[1]
            assert len(data_row) == 8
            # Check position values
            assert float(data_row[1]) == 48.123
            assert float(data_row[2]) == 11.456
            assert float(data_row[3]) == 500.0
            assert data_row[4] == '4'
            assert data_row[5] == 'RTK Fixed'
            assert data_row[6] == '12'
            assert float(data_row[7]) == 0.8
        finally:
            os.unlink(fname)

    def test_no_data_logged_without_position_lock(self):
        state = GnssState(0.0, 0.0, 0.0)
        # have_position_lock defaults to False
        with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False) as f:
            fname = f.name
        try:
            pl = PositionLogger(state, fname, interval=0.1)
            pl.start()
            import time
            time.sleep(0.3)
            pl.stop()

            with open(fname, newline='') as f:
                rows = list(csv.reader(f))
            assert len(rows) == 1  # header only
        finally:
            os.unlink(fname)


class TestPositionLoggerCsvFormat:
    def test_csv_format_is_valid(self):
        state = GnssState(0.0, 0.0, 0.0)
        state.update(
            have_position_lock=True,
            position={"lat": 52.520, "lon": 13.405, "alt": 34.0},
            fix_type=5,
            rtk_status="RTK Float",
            num_satellites_used=8,
            hdop=1.2,
        )
        with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False) as f:
            fname = f.name
        try:
            pl = PositionLogger(state, fname, interval=0.1)
            pl.start()
            import time
            time.sleep(0.3)
            pl.stop()

            with open(fname, newline='') as f:
                reader = csv.DictReader(f)
                rows = list(reader)
            assert len(rows) >= 1
            row = rows[0]
            # Verify all expected columns exist
            assert 'timestamp' in row
            assert 'lat' in row
            assert 'lon' in row
            assert 'alt' in row
            assert 'fix_type' in row
            assert 'rtk_status' in row
            assert 'num_sats' in row
            assert 'hdop' in row
            # Verify timestamp is ISO format (contains 'T')
            assert 'T' in row['timestamp']
        finally:
            os.unlink(fname)
