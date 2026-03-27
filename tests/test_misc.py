from __version__ import __title__, __version__
from rtk_constants import (
    MAX_LOG_MESSAGES,
    MAX_UI_MESSAGE_LENGTH,
    NTRIP_HEADER_SIZE_LIMIT,
    SNR_THRESHOLD_BAD,
    SNR_THRESHOLD_GOOD,
)


class TestVersion:
    def test_version_format(self):
        parts = __version__.split('.')
        assert len(parts) == 3
        assert all(p.isdigit() for p in parts)

    def test_title(self):
        assert __title__ == "RTK GNSS Client"


class TestConstantsConsistency:
    def test_snr_thresholds_order(self):
        assert SNR_THRESHOLD_GOOD > SNR_THRESHOLD_BAD

    def test_ui_message_length_positive(self):
        assert MAX_UI_MESSAGE_LENGTH > 0

    def test_log_messages_positive(self):
        assert MAX_LOG_MESSAGES > 0

    def test_header_limit_positive(self):
        assert NTRIP_HEADER_SIZE_LIMIT > 0
