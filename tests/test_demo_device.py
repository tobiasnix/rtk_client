from unittest.mock import patch

import pytest

from demo_device import DemoGnssDevice
from rtk_state import GnssState


@pytest.fixture
def state():
    return GnssState(0.0, 0.0, 0.0)


class TestDemoGnssDeviceConnect:
    def test_connect_valid_file(self, tmp_path, state):
        nmea = tmp_path / "test.nmea"
        nmea.write_text("$GPGGA,dummy*00\n")
        device = DemoGnssDevice(nmea_file=str(nmea), state=state)

        result = device.connect()

        assert result is True
        assert device.is_connected() is True

    def test_connect_missing_file(self, state):
        device = DemoGnssDevice(nmea_file="/no/such/file.nmea", state=state)

        result = device.connect()

        assert result is False
        assert device.is_connected() is False


class TestDemoGnssDeviceReadLine:
    @patch("demo_device.time.sleep")
    def test_read_line_returns_nmea(self, mock_sleep, tmp_path, state):
        nmea = tmp_path / "test.nmea"
        nmea.write_text("$GPGGA,120000,4006.5638,N,00709.2701,W,4,12,0.8,476.68,M,49.5,M,,*47\n")
        device = DemoGnssDevice(nmea_file=str(nmea), state=state)
        device.connect()

        line = device.read_line()

        assert line is not None
        assert len(line) > 0
        assert line.startswith("$")
        mock_sleep.assert_called()

    @patch("demo_device.time.sleep")
    def test_read_line_loops_at_eof(self, mock_sleep, tmp_path, state):
        nmea = tmp_path / "test.nmea"
        nmea.write_text("$GPGGA,line1*00\n$GPRMC,line2*00\n")
        device = DemoGnssDevice(nmea_file=str(nmea), state=state)
        device.connect()

        line1 = device.read_line()
        line2 = device.read_line()
        line3 = device.read_line()  # Should loop back to first line

        assert line1 == "$GPGGA,line1*00"
        assert line2 == "$GPRMC,line2*00"
        assert line3 == line1

    @patch("demo_device.time.sleep")
    def test_read_line_not_connected(self, mock_sleep, state):
        device = DemoGnssDevice(nmea_file="/no/matter.nmea", state=state)

        result = device.read_line()

        assert result is None


class TestDemoGnssDeviceWriteData:
    def test_write_data_returns_length(self, tmp_path, state):
        nmea = tmp_path / "test.nmea"
        nmea.write_text("$GPGGA,dummy*00\n")
        device = DemoGnssDevice(nmea_file=str(nmea), state=state)
        device.connect()

        data = b"\xd3\x00\x13some_rtcm_data"
        result = device.write_data(data)

        assert result == len(data)

    def test_write_data_not_connected(self, state):
        device = DemoGnssDevice(nmea_file="/no/matter.nmea", state=state)

        result = device.write_data(b"\xd3\x00\x13data")

        assert result is None


class TestDemoGnssDeviceClose:
    def test_close_sets_disconnected(self, tmp_path, state):
        nmea = tmp_path / "test.nmea"
        nmea.write_text("$GPGGA,dummy*00\n")
        device = DemoGnssDevice(nmea_file=str(nmea), state=state)
        device.connect()
        assert device.is_connected() is True

        device.close()

        assert device.is_connected() is False


class TestDemoGnssDeviceConfigure:
    def test_configure_module_does_not_raise(self, state):
        device = DemoGnssDevice(state=state)

        device.configure_module()  # Should not raise


class TestDemoGnssDeviceChecksum:
    def test_calculate_checksum(self):
        # XOR of 'G','P','G','G','A' = 0x47^0x50^0x47^0x47^0x41
        result = DemoGnssDevice._calculate_checksum("$GPGGA")
        expected = 0
        for c in "GPGGA":
            expected ^= ord(c)
        assert result == f"{expected:02X}"

    def test_calculate_checksum_strips_dollar_and_star(self):
        result = DemoGnssDevice._calculate_checksum("$GPGGA,data*1A")
        expected = 0
        for c in "GPGGA,data":
            expected ^= ord(c)
        assert result == f"{expected:02X}"
