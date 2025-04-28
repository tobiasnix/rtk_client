import unittest
from unittest.mock import patch, MagicMock
import argparse
import time
from datetime import datetime, timezone

# Import classes from the refactored script
# Assuming the refactored script is named 'lc29hda_rtk.py'
from lc29hda_rtk import (
    Config,
    GnssState,
    NmeaParser,
    GnssDevice,
    NtripClient,
    StatusDisplay,
    RtkController,
    DEFAULT_LAT, DEFAULT_LON, DEFAULT_ALT,
    FIX_QUALITY_INVALID, FIX_QUALITY_GPS, FIX_QUALITY_RTK_FIXED
)

class TestConfig(unittest.TestCase):

    def test_config_defaults(self):
        """Test if default values are loaded correctly."""
        args = argparse.Namespace(
            port='/dev/ttyTEST', baud=115200,
            ntrip_server=None, ntrip_port=None, ntrip_mountpoint=None,
            ntrip_user=None, ntrip_pass=None,
            default_lat=None, default_lon=None, default_alt=None,
            log_file='test.log', debug=False
        )
        config = Config(args)
        self.assertEqual(config.serial_port, '/dev/ttyTEST')
        self.assertEqual(config.ntrip_server, '193.137.94.71') # Check default
        self.assertEqual(config.default_lat, DEFAULT_LAT)

    def test_config_overrides(self):
        """Test if command line arguments override defaults."""
        args = argparse.Namespace(
            port='/dev/ttyACM0', baud=9600,
            ntrip_server='test.server.com', ntrip_port=2102, ntrip_mountpoint='TEST',
            ntrip_user='testuser', ntrip_pass='testpass',
            default_lat=1.23, default_lon=4.56, default_alt=7.89,
            log_file='test.log', debug=True
        )
        config = Config(args)
        self.assertEqual(config.serial_port, '/dev/ttyACM0')
        self.assertEqual(config.baud_rate, 9600)
        self.assertEqual(config.ntrip_server, 'test.server.com')
        self.assertEqual(config.ntrip_port, 2102)
        self.assertEqual(config.ntrip_mountpoint, 'TEST')
        self.assertEqual(config.ntrip_username, 'testuser')
        self.assertEqual(config.ntrip_password, 'testpass')
        self.assertEqual(config.default_lat, 1.23)
        self.assertEqual(config.default_lon, 4.56)
        self.assertEqual(config.default_alt, 7.89)
        self.assertTrue(config.debug)

class TestGnssState(unittest.TestCase):

    def setUp(self):
        self.state = GnssState(DEFAULT_LAT, DEFAULT_LON, DEFAULT_ALT)

    def test_initial_state(self):
        self.assertEqual(self.state.fix_type, FIX_QUALITY_INVALID)
        self.assertEqual(self.state.rtk_status, "Unknown")
        self.assertFalse(self.state.have_position_lock)
        self.assertEqual(self.state.gps_error_count, 0)

    def test_update_state(self):
        self.state.update(fix_type=FIX_QUALITY_RTK_FIXED, rtk_status="RTK Fixed", hdop=0.8)
        snapshot = self.state.get_state_snapshot()
        self.assertEqual(snapshot['fix_type'], FIX_QUALITY_RTK_FIXED)
        self.assertEqual(snapshot['rtk_status'], "RTK Fixed")
        self.assertEqual(snapshot['hdop'], 0.8)

    def test_increment_error(self):
        self.state.increment_error_count("gps")
        self.state.increment_error_count("gps")
        self.state.increment_error_count("ntrip")
        snapshot = self.state.get_state_snapshot()
        self.assertEqual(snapshot['gps_error_count'], 2)
        self.assertEqual(snapshot['ntrip_error_count'], 1)

    def test_add_rtcm_type(self):
         self.state.add_rtcm_type(1077)
         self.state.add_rtcm_type(1005)
         snapshot = self.state.get_state_snapshot()
         self.assertIn(1077, snapshot['last_rtcm_message_types'])
         self.assertIn(1005, snapshot['last_rtcm_message_types'])
         self.assertEqual(len(snapshot['last_rtcm_message_types']), 2)


class TestNmeaParser(unittest.TestCase):

    def setUp(self):
        self.state = GnssState(DEFAULT_LAT, DEFAULT_LON, DEFAULT_ALT)
        self.parser = NmeaParser(self.state)

    def test_parse_invalid_sentence(self):
        initial_epochs = self.state.get_state_snapshot()['epochs_since_start']
        self.parser.parse("")
        self.parser.parse("Invalid sentence")
        self.assertEqual(self.state.get_state_snapshot()['epochs_since_start'], initial_epochs)

    def test_parse_gga_no_fix(self):
        # Example GGA with fix quality 0
        gga_sentence = "$GNGGA,123519.00,4807.038,N,01131.000,E,0,08,0.9,545.4,M,46.9,M,,*47"
        self.parser.parse(gga_sentence)
        snapshot = self.state.get_state_snapshot()
        self.assertEqual(snapshot['fix_type'], FIX_QUALITY_INVALID)
        self.assertEqual(snapshot['rtk_status'], "No Fix / Invalid")
        self.assertFalse(snapshot['have_position_lock']) # Should not gain lock
        self.assertEqual(snapshot['num_satellites_used'], 8) # Still reports sats seen

    def test_parse_gga_gps_fix(self):
        gga_sentence = "$GNGGA,100000.00,5109.026216,N,00006.273795,W,1,15,0.7,45.6,M,47.9,M,,*77"
        self.parser.parse(gga_sentence)
        snapshot = self.state.get_state_snapshot()
        self.assertEqual(snapshot['fix_type'], FIX_QUALITY_GPS)
        self.assertEqual(snapshot['rtk_status'], "GPS (SPS)")
        self.assertTrue(snapshot['have_position_lock'])
        self.assertAlmostEqual(snapshot['position']['lat'], 51.1504369333, places=6)
        self.assertAlmostEqual(snapshot['position']['lon'], -0.10456325, places=6)
        self.assertAlmostEqual(snapshot['position']['alt'], 45.6)
        self.assertEqual(snapshot['num_satellites_used'], 15)
        self.assertAlmostEqual(snapshot['hdop'], 0.7)
        self.assertIsNotNone(snapshot['last_fix_time'])
        self.assertIsNotNone(snapshot['first_fix_time_sec'])

    def test_parse_gga_rtk_fixed(self):
        gga_sentence = "$GNGGA,100001.00,5109.026220,N,00006.273790,W,4,18,0.6,45.7,M,47.9,M,,*7F"
        self.parser.parse(gga_sentence)
        snapshot = self.state.get_state_snapshot()
        self.assertEqual(snapshot['fix_type'], FIX_QUALITY_RTK_FIXED)
        self.assertEqual(snapshot['rtk_status'], "RTK Fixed")
        self.assertTrue(snapshot['have_position_lock'])
        self.assertEqual(snapshot['num_satellites_used'], 18)
        self.assertAlmostEqual(snapshot['hdop'], 0.6)
        self.assertIsNotNone(snapshot['last_rtk_fix_time']) # Check RTK time is set
        self.assertEqual(snapshot['fix_type_counter']['RTK Fixed'], 1)

    def test_parse_gsv(self):
         # Example GSV sequence (simplified)
         gsv1 = "$GPGSV,3,1,10,01,50,180,35,02,40,045,38,03,30,300,32,04,20,135,30,1*6A" # Talker GP, Signal ID 1 (L1 C/A)
         gsv2 = "$GPGSV,3,2,10,05,15,270,,06,10,090,28,,,,,,,,1*51"
         gsv3 = "$GPGSV,3,3,10,07,05,225,25,08,00,000,,,,,1*59"
         glgsv = "$GLGSV,1,1,02,70,60,030,40,71,30,150,35,1*61" # Talker GL, Signal ID 1 (L1OF)

         self.parser.parse(gsv1)
         self.parser.parse(gsv2)
         self.parser.parse(gsv3) # End of GP sequence
         self.parser.parse(glgsv) # GL sequence

         snapshot = self.state.get_state_snapshot()
         self.assertEqual(snapshot['num_satellites_in_view'], 10) # From last GSV num_sv_in_view
         self.assertIn('GP-1', snapshot['satellites_info'])
         self.assertIn('GL-70', snapshot['satellites_info'])
         self.assertEqual(snapshot['satellites_info']['GP-1']['snr'], 35)
         self.assertEqual(snapshot['satellites_info']['GL-70']['snr'], 40)
         self.assertEqual(snapshot['satellite_systems']['GPS'], 8) # Count from GPGSV
         self.assertEqual(snapshot['satellite_systems']['GLONASS'], 2) # Count from GLGSV
         self.assertGreater(snapshot['snr_stats']['avg'], 0)


# --- Mock Classes ---
class MockSerial:
    """Simplified mock for serial.Serial."""
    def __init__(self, port, baudrate, timeout):
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self.is_open = True
        self._write_buffer = bytearray()
        self._read_buffer = bytearray()

    def write(self, data):
        if not self.is_open: raise serial.SerialException("Port not open")
        self._write_buffer.extend(data)
        # print(f"MockSerial Write: {data}") # Debug
        return len(data)

    def readline(self):
        if not self.is_open: raise serial.SerialException("Port not open")
        # Simulate receiving data (e.g., acknowledge commands)
        if b'PAIR864' in self._write_buffer:
             self._write_buffer = bytearray() # Clear write buffer after processing
             return b'$PAIR001,864,0*31\r\n' # Simulate ACK
        if b'PQTMVERNO' in self._write_buffer:
             self._write_buffer = bytearray()
             return b'$PQTMVERNO,TESTFW-V1,2024/01/01,00:00:00*CS\r\n' # Simulate FW response
        if b'PAIR513' in self._write_buffer:
             self._write_buffer = bytearray()
             return b'$PAIR001,513,0*3C\r\n' # Simulate ACK

        # Simulate receiving NMEA data
        time.sleep(0.05) # Simulate time delay
        return b'$GNGGA,100001.00,5109.026220,N,00006.273790,W,4,18,0.6,45.7,M,47.9,M,,*7F\r\n'


    def close(self):
        self.is_open = False

    def flush(self):
        pass

class TestGnssDevice(unittest.TestCase):

    @patch('serial.Serial', MockSerial) # Patch serial.Serial with our Mock
    def setUp(self):
        self.state = GnssState(DEFAULT_LAT, DEFAULT_LON, DEFAULT_ALT)
        self.device = GnssDevice('/dev/mock', 115200, self.state)

    def test_connect(self):
        self.assertTrue(self.device.is_connected())

    def test_send_command_ack(self):
        response = self.device.send_command("PAIR864,0,0,115200")
        self.assertEqual(response, '$PAIR001,864,0*31')

    def test_configure_module(self):
         # Doesn't assert much, just runs through the commands using the mock
         self.device.configure_module()
         snapshot = self.state.get_state_snapshot()
         self.assertEqual(snapshot['firmware_version'], 'TESTFW-V1') # Check if FW was parsed

    def test_read_line(self):
         line = self.device.read_line()
         # Check if it matches the mock NMEA data
         self.assertTrue(line.startswith('$GNGGA'))

    def tearDown(self):
        self.device.close()


# Add more tests for NtripClient, StatusDisplay, RtkController
# These would typically involve more complex mocking (e.g., mock sockets)

if __name__ == '__main__':
    unittest.main(argv=['first-arg-is-ignored'], exit=False)
