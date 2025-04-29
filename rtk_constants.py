# rtk_constants.py - Shared constants for the RTK client application

from typing import Dict

# --- Serial/NTRIP Defaults ---
DEFAULT_SERIAL_PORT = "/dev/ttyUSB0"
DEFAULT_BAUD_RATE = 115200
DEFAULT_NTRIP_SERVER = "193.137.94.71" # Example server
DEFAULT_NTRIP_PORT = 2101
DEFAULT_NTRIP_MOUNTPOINT = "PNM1" # Example mountpoint
DEFAULT_NTRIP_USERNAME = "user"
DEFAULT_NTRIP_PASSWORD = "password"

# --- Fallback Position ---
DEFAULT_LAT = 40.10939918 # Fallback Latitude
DEFAULT_LON = -7.15450152 # Fallback Longitude
DEFAULT_ALT = 476.68    # Fallback Altitude
DEFAULT_HDOP = 99.99    # Default/Invalid HDOP value according to Spec V1.4

# --- Timing and Intervals ---
NTRIP_TIMEOUT = 10.0  # seconds for connection/response
NTRIP_GGA_INTERVAL = 10.0 # seconds between sending GGA
NTRIP_MAX_RECONNECT_TIMEOUT = 60.0 # seconds max backoff
NTRIP_INITIAL_RECONNECT_TIMEOUT = 5.0 # seconds initial backoff
NTRIP_DATA_TIMEOUT = 60.0 # seconds - reconnect if no data received
SERIAL_TIMEOUT = 1.0 # seconds for serial read/write
STATUS_UPDATE_INTERVAL = 1.0 # seconds for UI refresh

# --- NMEA Fix Quality Indicators ---
FIX_QUALITY_INVALID = 0
FIX_QUALITY_GPS = 1
FIX_QUALITY_DGPS = 2
FIX_QUALITY_PPS = 3
FIX_QUALITY_RTK_FIXED = 4
FIX_QUALITY_RTK_FLOAT = 5
FIX_QUALITY_ESTIMATED = 6

# --- RTCM3 Message Types ---
RTCM_MSG_TYPE_GPS_MSM7 = 1077
RTCM_MSG_TYPE_GLONASS_MSM7 = 1087
RTCM_MSG_TYPE_GALILEO_MSM7 = 1097
RTCM_MSG_TYPE_BDS_MSM7 = 1127
RTCM_MSG_TYPE_QZSS_MSM7 = 1117
RTCM_MSG_TYPE_ARP_1005 = 1005
RTCM_MSG_TYPE_ARP_1006 = 1006 # Alternative with height

# Important RTCM types to check for RTK operation
IMPORTANT_RTCM_TYPES: Dict[int, str] = {
    RTCM_MSG_TYPE_GPS_MSM7: "GPS MSM7 (1077)",
    RTCM_MSG_TYPE_GLONASS_MSM7: "GLONASS MSM7 (1087)",
    RTCM_MSG_TYPE_GALILEO_MSM7: "Galileo MSM7 (1097)",
    RTCM_MSG_TYPE_BDS_MSM7: "BDS MSM7 (1127)",
    RTCM_MSG_TYPE_ARP_1005: "ARP (1005/1006)", # Base station position
}

# --- Logging ---
LOG_FORMAT = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
DEFAULT_LOG_FILENAME = "rtk_client.log"
MAX_LOG_MESSAGES = 10 # Max messages in UI buffer
