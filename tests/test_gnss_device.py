from unittest.mock import MagicMock, patch

from gnss_device import GnssDevice
from rtk_state import GnssState


class TestGnssDeviceInit:
    def test_initial_state(self):
        state = GnssState(0.0, 0.0, 0.0)
        device = GnssDevice("/dev/ttyUSB0", 115200, state)
        assert device._port_name == "/dev/ttyUSB0"
        assert device._baudrate == 115200
        assert device.is_connected() is False


class TestGnssDeviceConnect:
    @patch("gnss_device.serial.Serial")
    def test_connect_success(self, mock_serial_class):
        mock_port = MagicMock()
        mock_port.is_open = True
        mock_serial_class.return_value = mock_port

        state = GnssState(0.0, 0.0, 0.0)
        device = GnssDevice("/dev/ttyUSB0", 115200, state)
        result = device.connect()

        assert result is True
        assert device.is_connected() is True
        mock_port.reset_input_buffer.assert_called_once()
        mock_port.reset_output_buffer.assert_called_once()

    @patch("gnss_device.serial.Serial")
    def test_connect_failure(self, mock_serial_class):
        import serial
        mock_serial_class.side_effect = serial.SerialException("Port not found")

        state = GnssState(0.0, 0.0, 0.0)
        device = GnssDevice("/dev/ttyFAKE", 115200, state)
        result = device.connect()

        assert result is False
        assert device.is_connected() is False

    @patch("gnss_device.serial.Serial")
    def test_connect_already_connected(self, mock_serial_class):
        mock_port = MagicMock()
        mock_port.is_open = True
        mock_serial_class.return_value = mock_port

        state = GnssState(0.0, 0.0, 0.0)
        device = GnssDevice("/dev/ttyUSB0", 115200, state)
        device.connect()
        result = device.connect()  # second call

        assert result is True
        # Serial() constructor should only be called once
        mock_serial_class.assert_called_once()


class TestGnssDeviceReadLine:
    @patch("gnss_device.serial.Serial")
    def test_read_line_with_data(self, mock_serial_class):
        mock_port = MagicMock()
        mock_port.is_open = True
        mock_port.in_waiting = 50
        mock_port.readline.return_value = b"$GNGGA,123456.00,4006.56,N,00709.27,W,1,08,1.0,100.0,M,-0.0,M,,*XX\r\n"
        mock_serial_class.return_value = mock_port

        state = GnssState(0.0, 0.0, 0.0)
        device = GnssDevice("/dev/ttyUSB0", 115200, state)
        device.connect()
        line = device.read_line()

        assert line is not None
        assert "$GNGGA" in line

    @patch("gnss_device.serial.Serial")
    def test_read_line_no_data(self, mock_serial_class):
        mock_port = MagicMock()
        mock_port.is_open = True
        mock_port.in_waiting = 0
        mock_serial_class.return_value = mock_port

        state = GnssState(0.0, 0.0, 0.0)
        device = GnssDevice("/dev/ttyUSB0", 115200, state)
        device.connect()
        line = device.read_line()

        assert line == ""

    def test_read_line_not_connected(self):
        state = GnssState(0.0, 0.0, 0.0)
        device = GnssDevice("/dev/ttyUSB0", 115200, state)
        line = device.read_line()
        assert line is None


class TestGnssDeviceWriteData:
    @patch("gnss_device.serial.Serial")
    def test_write_data(self, mock_serial_class):
        mock_port = MagicMock()
        mock_port.is_open = True
        mock_port.write.return_value = 10
        mock_serial_class.return_value = mock_port

        state = GnssState(0.0, 0.0, 0.0)
        device = GnssDevice("/dev/ttyUSB0", 115200, state)
        device.connect()
        result = device.write_data(b"\xD3\x00\x04\x43\x50\x00\x00\x00\x00\x00")

        assert result == 10

    @patch("gnss_device.serial.Serial")
    def test_write_empty_data(self, mock_serial_class):
        mock_port = MagicMock()
        mock_port.is_open = True
        mock_serial_class.return_value = mock_port

        state = GnssState(0.0, 0.0, 0.0)
        device = GnssDevice("/dev/ttyUSB0", 115200, state)
        device.connect()
        result = device.write_data(b"")

        assert result == 0

    def test_write_not_connected(self):
        state = GnssState(0.0, 0.0, 0.0)
        device = GnssDevice("/dev/ttyUSB0", 115200, state)
        result = device.write_data(b"data")
        assert result is None


class TestGnssDeviceClose:
    @patch("gnss_device.serial.Serial")
    def test_close(self, mock_serial_class):
        mock_port = MagicMock()
        mock_port.is_open = True
        mock_serial_class.return_value = mock_port

        state = GnssState(0.0, 0.0, 0.0)
        device = GnssDevice("/dev/ttyUSB0", 115200, state)
        device.connect()
        device.close()

        mock_port.close.assert_called_once()
        assert device._serial_port is None

    def test_close_not_connected(self):
        state = GnssState(0.0, 0.0, 0.0)
        device = GnssDevice("/dev/ttyUSB0", 115200, state)
        device.close()  # should not raise
