"""Tests for the StatusDisplay curses-based TUI class."""

import curses
from collections import Counter, deque
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from rtk_state import GnssState  # noqa: I001
from status_display import StatusDisplay

# ---------------------------------------------------------------------------
# Helper factories
# ---------------------------------------------------------------------------

def _make_mock_config():
    """Create a mock Config with NTRIP attributes."""
    config = MagicMock()
    config.ntrip_server = "test.server.com"
    config.ntrip_port = 2101
    config.ntrip_mountpoint = "TEST"
    return config


def _make_state_dict(**overrides):
    """Create a state snapshot dict with sensible defaults."""
    base = {
        "module_name": "Test Module",
        "start_time": datetime(2026, 1, 1, tzinfo=timezone.utc),
        "firmware_version": "v1.0",
        "position": {"lat": 40.0, "lon": -7.0, "alt": 100.0},
        "last_fix_time": datetime.now(timezone.utc),
        "first_fix_time_sec": 5.0,
        "rtk_status": "RTK Fixed",
        "fix_type": 4,
        "num_satellites_used": 12,
        "num_satellites_in_view": 20,
        "hdop": 1.2,
        "satellite_systems": Counter({"GPS": 8, "Galileo": 4}),
        "ntrip_connected": True,
        "ntrip_status_message": "Connected",
        "ntrip_connection_gave_up": False,
        "ntrip_reconnect_attempts": 0,
        "ntrip_last_data_time": datetime.now(timezone.utc),
        "ntrip_data_rates": deque([100, 200, 150]),
        "ntrip_total_bytes": 50000,
        "last_rtcm_message_types": deque([1077, 1087, 1097]),
        "satellites_info": {},
        "ui_log_messages": deque(),
        "have_position_lock": True,
    }
    base.update(overrides)
    return base


def _make_mock_win(max_y=24, max_x=80):
    """Create a mock curses window with getmaxyx/getbegyx support."""
    win = MagicMock()
    win.getmaxyx.return_value = (max_y, max_x)
    win.getbegyx.return_value = (0, 0)
    win.addstr = MagicMock()
    return win


def _make_display():
    """Create a StatusDisplay instance with mocked state and config."""
    state = GnssState(0.0, 0.0, 0.0)
    config = _make_mock_config()
    return StatusDisplay(state, config)


# ===========================================================================
# TestStatusDisplayInit
# ===========================================================================

class TestStatusDisplayInit:
    """Verify constructor sets expected default attributes."""

    def test_initial_window_handles_are_none(self):
        display = _make_display()
        assert display._stdscr is None
        assert display._header_win is None
        assert display._info_win is None
        assert display._sat_win is None
        assert display._msg_win is None

    def test_initial_flags(self):
        display = _make_display()
        assert display._needs_redraw is True
        assert display._first_draw is True

    def test_layout_constants(self):
        display = _make_display()
        assert display.HEADER_HEIGHT == 3
        assert display.MSG_HEIGHT == 7
        assert display.MIN_WIDTH == 50
        assert display.MIN_HEIGHT == 15

    def test_stores_state_and_config(self):
        state = GnssState(1.0, 2.0, 3.0)
        config = _make_mock_config()
        display = StatusDisplay(state, config)
        assert display._state is state
        assert display._config is config


# ===========================================================================
# TestAddstrSafe
# ===========================================================================

class TestAddstrSafe:
    """Tests for _addstr_safe boundary handling and truncation."""

    def test_returns_false_for_none_window(self):
        display = _make_display()
        result = display._addstr_safe(None, 0, 0, "hello")
        assert result is False

    def test_returns_true_on_valid_write(self):
        display = _make_display()
        win = _make_mock_win(max_y=10, max_x=40)
        result = display._addstr_safe(win, 1, 2, "test text")
        assert result is True
        win.addstr.assert_called_once_with(1, 2, "test text", curses.A_NORMAL)

    def test_returns_false_when_y_out_of_bounds(self):
        display = _make_display()
        win = _make_mock_win(max_y=10, max_x=40)
        assert display._addstr_safe(win, 10, 0, "text") is False
        assert display._addstr_safe(win, -1, 0, "text") is False

    def test_returns_false_when_x_out_of_bounds(self):
        display = _make_display()
        win = _make_mock_win(max_y=10, max_x=40)
        assert display._addstr_safe(win, 0, 40, "text") is False
        assert display._addstr_safe(win, 0, -1, "text") is False

    def test_truncates_text_to_available_width(self):
        display = _make_display()
        # Window is 10 wide; writing at x=5 leaves available = 10 - 5 - 1 = 4
        win = _make_mock_win(max_y=10, max_x=10)
        result = display._addstr_safe(win, 0, 5, "abcdefghij")
        assert result is True
        win.addstr.assert_called_once_with(0, 5, "abcd", curses.A_NORMAL)

    def test_returns_false_when_available_width_zero(self):
        display = _make_display()
        # Window 10 wide, x = 9 => available = 10 - 9 - 1 = 0
        win = _make_mock_win(max_y=10, max_x=10)
        result = display._addstr_safe(win, 0, 9, "text")
        assert result is False

    def test_returns_false_on_curses_error(self):
        display = _make_display()
        win = _make_mock_win(max_y=10, max_x=80)
        win.addstr.side_effect = curses.error("addstr failed")
        result = display._addstr_safe(win, 0, 0, "hello")
        assert result is False

    def test_custom_attribute_passed_through(self):
        display = _make_display()
        win = _make_mock_win(max_y=10, max_x=80)
        display._addstr_safe(win, 1, 1, "bold text", curses.A_BOLD)
        win.addstr.assert_called_once_with(1, 1, "bold text", curses.A_BOLD)


# ===========================================================================
# TestGetColor
# ===========================================================================

class TestGetColor:
    """Tests for _get_color color attribute lookup."""

    @patch("status_display.curses")
    def test_returns_normal_when_no_colors(self, mock_curses):
        mock_curses.has_colors.return_value = False
        mock_curses.A_NORMAL = curses.A_NORMAL
        display = _make_display()
        assert display._get_color("green") == curses.A_NORMAL

    @patch("status_display.curses")
    def test_known_colors_with_bold(self, mock_curses):
        mock_curses.has_colors.return_value = True
        mock_curses.A_BOLD = curses.A_BOLD
        mock_curses.A_NORMAL = curses.A_NORMAL
        mock_curses.A_DIM = curses.A_DIM
        mock_curses.color_pair.side_effect = lambda n: n * 256  # deterministic mapping

        display = _make_display()
        # green is pair(1) | BOLD
        assert display._get_color("green") == (1 * 256) | curses.A_BOLD
        # yellow is pair(2) | BOLD
        assert display._get_color("yellow") == (2 * 256) | curses.A_BOLD
        # red is pair(3) | BOLD
        assert display._get_color("red") == (3 * 256) | curses.A_BOLD

    @patch("status_display.curses")
    def test_known_colors_without_bold(self, mock_curses):
        mock_curses.has_colors.return_value = True
        mock_curses.A_BOLD = curses.A_BOLD
        mock_curses.A_NORMAL = curses.A_NORMAL
        mock_curses.A_DIM = curses.A_DIM
        mock_curses.color_pair.side_effect = lambda n: n * 256

        display = _make_display()
        # cyan is pair(4), no bold
        assert display._get_color("cyan") == 4 * 256
        # white is pair(5), no bold
        assert display._get_color("white") == 5 * 256

    @patch("status_display.curses")
    def test_unknown_color_returns_normal(self, mock_curses):
        mock_curses.has_colors.return_value = True
        mock_curses.A_NORMAL = curses.A_NORMAL
        mock_curses.A_BOLD = curses.A_BOLD
        mock_curses.A_DIM = curses.A_DIM
        mock_curses.color_pair.side_effect = lambda n: n * 256

        display = _make_display()
        assert display._get_color("purple") == curses.A_NORMAL
        assert display._get_color("UNKNOWN") == curses.A_NORMAL

    @patch("status_display.curses")
    def test_special_attributes(self, mock_curses):
        mock_curses.has_colors.return_value = True
        mock_curses.A_BOLD = curses.A_BOLD
        mock_curses.A_NORMAL = curses.A_NORMAL
        mock_curses.A_DIM = curses.A_DIM
        mock_curses.color_pair.side_effect = lambda n: n * 256

        display = _make_display()
        assert display._get_color("bold") == curses.A_BOLD
        assert display._get_color("normal") == curses.A_NORMAL
        assert display._get_color("dim") == curses.A_DIM


# ===========================================================================
# TestSetupCurses
# ===========================================================================

class TestSetupCurses:
    """Tests for _setup_curses initialization logic."""

    @patch("status_display.curses")
    def test_basic_setup_calls(self, mock_curses):
        mock_curses.has_colors.return_value = False
        display = _make_display()
        stdscr = MagicMock()

        display._setup_curses(stdscr)

        mock_curses.curs_set.assert_called_once_with(0)
        stdscr.nodelay.assert_called_once_with(True)
        stdscr.timeout.assert_called_once_with(1000)
        assert display._stdscr is stdscr

    @patch("status_display.curses")
    def test_sets_up_colors_when_available(self, mock_curses):
        mock_curses.has_colors.return_value = True
        mock_curses.can_change_color.return_value = True
        mock_curses.COLOR_GREEN = curses.COLOR_GREEN
        mock_curses.COLOR_YELLOW = curses.COLOR_YELLOW
        mock_curses.COLOR_RED = curses.COLOR_RED
        mock_curses.COLOR_CYAN = curses.COLOR_CYAN
        mock_curses.COLOR_WHITE = curses.COLOR_WHITE
        mock_curses.COLOR_BLUE = curses.COLOR_BLUE
        mock_curses.COLOR_MAGENTA = curses.COLOR_MAGENTA
        mock_curses.COLOR_BLACK = curses.COLOR_BLACK

        display = _make_display()
        stdscr = MagicMock()

        display._setup_curses(stdscr)

        mock_curses.start_color.assert_called_once()
        mock_curses.use_default_colors.assert_called_once()
        # 7 color pairs should be initialized
        assert mock_curses.init_pair.call_count == 7
        # Verify bg = -1 when can_change_color is True
        for c in mock_curses.init_pair.call_args_list:
            assert c[0][2] == -1  # bg argument

    @patch("status_display.curses")
    def test_uses_black_bg_when_cannot_change_color(self, mock_curses):
        mock_curses.has_colors.return_value = True
        mock_curses.can_change_color.return_value = False
        mock_curses.COLOR_GREEN = curses.COLOR_GREEN
        mock_curses.COLOR_YELLOW = curses.COLOR_YELLOW
        mock_curses.COLOR_RED = curses.COLOR_RED
        mock_curses.COLOR_CYAN = curses.COLOR_CYAN
        mock_curses.COLOR_WHITE = curses.COLOR_WHITE
        mock_curses.COLOR_BLUE = curses.COLOR_BLUE
        mock_curses.COLOR_MAGENTA = curses.COLOR_MAGENTA
        mock_curses.COLOR_BLACK = curses.COLOR_BLACK

        display = _make_display()
        stdscr = MagicMock()

        display._setup_curses(stdscr)

        mock_curses.start_color.assert_called_once()
        mock_curses.use_default_colors.assert_not_called()
        # bg should be COLOR_BLACK
        for c in mock_curses.init_pair.call_args_list:
            assert c[0][2] == curses.COLOR_BLACK


# ===========================================================================
# TestCreateWindows
# ===========================================================================

class TestCreateWindows:
    """Tests for _create_windows layout and window creation."""

    @patch("status_display.curses")
    def test_returns_false_when_no_stdscr(self, mock_curses):
        display = _make_display()
        display._stdscr = None
        assert display._create_windows() is False

    @patch("status_display.curses")
    def test_creates_four_windows_on_success(self, mock_curses):
        display = _make_display()
        stdscr = MagicMock()
        stdscr.getmaxyx.return_value = (30, 80)
        display._stdscr = stdscr

        mock_win = MagicMock()
        mock_curses.newwin.return_value = mock_win

        result = display._create_windows()

        assert result is True
        assert mock_curses.newwin.call_count == 4
        assert display._header_win is not None
        assert display._info_win is not None
        assert display._sat_win is not None
        assert display._msg_win is not None
        assert display._last_terminal_size == (30, 80)

    @patch("status_display.curses")
    def test_shows_warning_when_terminal_too_small(self, mock_curses):
        mock_curses.A_BOLD = curses.A_BOLD
        mock_curses.error = curses.error

        display = _make_display()
        stdscr = MagicMock()
        # Below MIN_HEIGHT=15 and MIN_WIDTH=50
        stdscr.getmaxyx.return_value = (10, 30)
        display._stdscr = stdscr

        mock_win = MagicMock()
        mock_curses.newwin.return_value = mock_win

        result = display._create_windows()

        # Should still attempt to create windows (continues anyway)
        assert result is True
        # Warning should be drawn on stdscr
        stdscr.addstr.assert_called_once()
        warning_text = stdscr.addstr.call_args[0][2]
        assert "Terminal too small" in warning_text

    @patch("status_display.curses")
    def test_returns_false_and_clears_windows_on_exception(self, mock_curses):
        display = _make_display()
        stdscr = MagicMock()
        stdscr.getmaxyx.return_value = (30, 80)
        display._stdscr = stdscr

        mock_curses.newwin.side_effect = Exception("newwin failed")

        result = display._create_windows()

        assert result is False
        assert display._header_win is None
        assert display._info_win is None
        assert display._sat_win is None
        assert display._msg_win is None


# ===========================================================================
# TestDrawHeader
# ===========================================================================

class TestDrawHeader:
    """Tests for _draw_header title rendering."""

    def test_returns_early_when_win_is_none(self):
        display = _make_display()
        display._header_win = None
        state = _make_state_dict()
        # Should not raise
        display._draw_header(state)

    @patch("status_display.curses")
    def test_draws_title_with_module_name(self, mock_curses):
        mock_curses.has_colors.return_value = True
        mock_curses.A_BOLD = curses.A_BOLD
        mock_curses.A_NORMAL = curses.A_NORMAL
        mock_curses.A_DIM = curses.A_DIM
        mock_curses.color_pair.side_effect = lambda n: n * 256
        mock_curses.error = curses.error

        display = _make_display()
        win = _make_mock_win(max_y=5, max_x=80)
        display._header_win = win

        state = _make_state_dict(module_name="MyGNSS")
        display._draw_header(state)

        win.erase.assert_called_once()
        win.box.assert_called_once()
        win.noutrefresh.assert_called_once()
        # The addstr call on the window should contain the module name
        # _addstr_safe calls win.addstr internally
        assert win.addstr.called
        title_text = win.addstr.call_args[0][2]
        assert "MyGNSS" in title_text

    @patch("status_display.curses")
    def test_skips_content_when_window_too_small(self, mock_curses):
        mock_curses.has_colors.return_value = True
        mock_curses.A_BOLD = curses.A_BOLD
        mock_curses.A_NORMAL = curses.A_NORMAL
        mock_curses.A_DIM = curses.A_DIM
        mock_curses.color_pair.side_effect = lambda n: n * 256
        mock_curses.error = curses.error

        display = _make_display()
        # Window too small: max_y < 3 or max_x < 20
        win = _make_mock_win(max_y=2, max_x=10)
        display._header_win = win

        state = _make_state_dict()
        display._draw_header(state)

        win.erase.assert_called_once()
        win.box.assert_called_once()
        # addstr should NOT be called for title since window is too small
        win.addstr.assert_not_called()


# ===========================================================================
# TestDrawSatPanel
# ===========================================================================

class TestDrawSatPanel:
    """Tests for _draw_sat_panel satellite table rendering."""

    @patch("status_display.curses")
    def test_shows_no_data_message_when_empty(self, mock_curses):
        mock_curses.has_colors.return_value = True
        mock_curses.A_BOLD = curses.A_BOLD
        mock_curses.A_NORMAL = curses.A_NORMAL
        mock_curses.A_DIM = curses.A_DIM
        mock_curses.color_pair.side_effect = lambda n: n * 256
        mock_curses.error = curses.error

        display = _make_display()
        win = _make_mock_win(max_y=20, max_x=40)
        display._sat_win = win

        state = _make_state_dict(satellites_info={})
        display._draw_sat_panel(state)

        # Find the "No satellite data available" message in addstr calls
        addstr_texts = [c[0][2] for c in win.addstr.call_args_list]
        assert any("No satellite data" in t for t in addstr_texts)
        win.noutrefresh.assert_called()

    @patch("status_display.curses")
    def test_renders_satellites_sorted_by_system_then_prn(self, mock_curses):
        mock_curses.has_colors.return_value = True
        mock_curses.A_BOLD = curses.A_BOLD
        mock_curses.A_NORMAL = curses.A_NORMAL
        mock_curses.A_DIM = curses.A_DIM
        mock_curses.color_pair.side_effect = lambda n: n * 256
        mock_curses.error = curses.error

        display = _make_display()
        win = _make_mock_win(max_y=30, max_x=60)
        display._sat_win = win

        sat_info = {
            "G10": {"prn": "10", "system": "GPS", "snr": 40, "elevation": 45,
                     "azimuth": 120, "active": True},
            "E5": {"prn": "5", "system": "Galileo", "snr": 35, "elevation": 30,
                    "azimuth": 90, "active": False},
            "G3": {"prn": "3", "system": "GPS", "snr": 30, "elevation": 60,
                    "azimuth": 200, "active": True},
        }
        state = _make_state_dict(satellites_info=sat_info)
        display._draw_sat_panel(state)

        # Collect all system abbreviation strings written via addstr.
        # Sort key is (system_name_str, prn_int), so alphabetically:
        #   "GPS" (0x47,0x50,0x53) < "Galileo" (0x47,0x61,0x6c)
        # Expected order: GPS:3, GPS:10, Galileo:5
        addstr_texts = [c[0][2] for c in win.addstr.call_args_list]
        sys_abbrs = [t.strip() for t in addstr_texts if t.strip() in ("GPS", "GAL", "GLO", "BDS")]
        # GPS entries should appear before GAL
        assert len(sys_abbrs) == 3, f"Expected 3 system abbrs, got {sys_abbrs}"
        assert sys_abbrs == ["GPS", "GPS", "GAL"], (
            f"Expected GPS entries before GAL; got {sys_abbrs}"
        )

    @patch("status_display.curses")
    def test_system_abbreviation_mapping(self, mock_curses):
        mock_curses.has_colors.return_value = True
        mock_curses.A_BOLD = curses.A_BOLD
        mock_curses.A_NORMAL = curses.A_NORMAL
        mock_curses.A_DIM = curses.A_DIM
        mock_curses.color_pair.side_effect = lambda n: n * 256
        mock_curses.error = curses.error

        display = _make_display()
        win = _make_mock_win(max_y=30, max_x=60)
        display._sat_win = win

        sat_info = {
            "R1": {"prn": "1", "system": "GLONASS", "snr": 30, "elevation": 45,
                    "azimuth": 90, "active": False},
            "C5": {"prn": "5", "system": "BeiDou", "snr": 25, "elevation": 30,
                    "azimuth": 180, "active": True},
        }
        state = _make_state_dict(satellites_info=sat_info)
        display._draw_sat_panel(state)

        addstr_texts = [c[0][2].strip() for c in win.addstr.call_args_list]
        assert "GLO" in addstr_texts, "GLONASS should abbreviate to GLO"
        assert "BDS" in addstr_texts, "BeiDou should abbreviate to BDS"

    def test_returns_early_when_win_is_none(self):
        display = _make_display()
        display._sat_win = None
        state = _make_state_dict()
        # Should not raise
        display._draw_sat_panel(state)


# ===========================================================================
# TestDrawMsgPanel
# ===========================================================================

class TestDrawMsgPanel:
    """Tests for _draw_msg_panel message color coding and overflow."""

    @patch("status_display.curses")
    def test_color_coding_for_error_warn_success(self, mock_curses):
        mock_curses.has_colors.return_value = True
        mock_curses.A_BOLD = curses.A_BOLD
        mock_curses.A_NORMAL = curses.A_NORMAL
        mock_curses.A_DIM = curses.A_DIM
        mock_curses.color_pair.side_effect = lambda n: n * 256
        mock_curses.error = curses.error

        display = _make_display()
        win = _make_mock_win(max_y=10, max_x=80)
        display._msg_win = win

        messages = deque([
            "[12:00:00] Connection error occurred",
            "[12:00:01] Warning: retry needed",
            "[12:00:02] Connected successfully",
        ])
        state = _make_state_dict(ui_log_messages=messages)
        display._draw_msg_panel(state)

        # Verify messages were written - each message triggers _addstr_safe
        msg_calls = [c for c in win.addstr.call_args_list if len(c[0]) >= 3]
        written_texts = [c[0][2] for c in msg_calls]

        # Verify all three messages appear
        assert any("error" in t.lower() for t in written_texts)
        assert any("retry" in t.lower() for t in written_texts)
        assert any("connected" in t.lower() or "success" in t.lower() for t in written_texts)

    @patch("status_display.curses")
    def test_hidden_messages_indicator(self, mock_curses):
        mock_curses.has_colors.return_value = True
        mock_curses.A_BOLD = curses.A_BOLD
        mock_curses.A_NORMAL = curses.A_NORMAL
        mock_curses.A_DIM = curses.A_DIM
        mock_curses.color_pair.side_effect = lambda n: n * 256
        mock_curses.error = curses.error

        display = _make_display()
        # Small window: max_y=5 means available_lines = 5 - 2 - 1 = 2
        win = _make_mock_win(max_y=5, max_x=80)
        display._msg_win = win

        # Put more messages than can fit
        messages = deque([f"[12:00:{i:02d}] msg {i}" for i in range(10)])
        state = _make_state_dict(ui_log_messages=messages)
        display._draw_msg_panel(state)

        # Should show [+N] indicator for hidden messages
        all_texts = [c[0][2] for c in win.addstr.call_args_list if len(c[0]) >= 3]
        assert any("[+" in t and "]" in t for t in all_texts), \
            f"Expected [+N] indicator in output; got: {all_texts}"

    @patch("status_display.curses")
    def test_returns_early_when_win_is_none(self, mock_curses):
        display = _make_display()
        display._msg_win = None
        state = _make_state_dict()
        # Should not raise
        display._draw_msg_panel(state)

    @patch("status_display.curses")
    def test_empty_messages(self, mock_curses):
        mock_curses.has_colors.return_value = True
        mock_curses.A_BOLD = curses.A_BOLD
        mock_curses.A_NORMAL = curses.A_NORMAL
        mock_curses.A_DIM = curses.A_DIM
        mock_curses.color_pair.side_effect = lambda n: n * 256
        mock_curses.error = curses.error

        display = _make_display()
        win = _make_mock_win(max_y=10, max_x=80)
        display._msg_win = win

        state = _make_state_dict(ui_log_messages=deque())
        display._draw_msg_panel(state)

        win.erase.assert_called_once()
        win.box.assert_called_once()
        win.noutrefresh.assert_called_once()


# ===========================================================================
# TestUpdateDisplay
# ===========================================================================

class TestUpdateDisplay:
    """Tests for update_display orchestration logic."""

    @patch("status_display.curses")
    def test_first_draw_sets_up_and_creates_windows(self, mock_curses):
        mock_curses.has_colors.return_value = False
        mock_curses.error = curses.error

        display = _make_display()
        assert display._first_draw is True

        stdscr = MagicMock()
        stdscr.getmaxyx.return_value = (30, 80)
        mock_curses.newwin.return_value = MagicMock()

        display.update_display(stdscr)

        # After first draw, _first_draw should be False
        assert display._first_draw is False
        assert display._stdscr is stdscr
        # setup_curses was called (curs_set)
        mock_curses.curs_set.assert_called_once_with(0)
        # Windows should have been created (4 newwin calls)
        assert mock_curses.newwin.call_count == 4

    @patch("status_display.curses")
    def test_detects_terminal_resize(self, mock_curses):
        mock_curses.has_colors.return_value = False
        mock_curses.error = curses.error
        mock_curses.doupdate = MagicMock()

        display = _make_display()
        display._first_draw = False
        display._needs_redraw = False

        stdscr = MagicMock()
        stdscr.getmaxyx.return_value = (40, 100)
        display._stdscr = stdscr
        display._last_terminal_size = (30, 80)  # Different from current

        # Set up windows so draw methods don't fail
        mock_win = MagicMock()
        mock_win.getmaxyx.return_value = (20, 50)
        mock_win.getbegyx.return_value = (0, 0)
        display._header_win = mock_win
        display._info_win = mock_win
        display._sat_win = mock_win
        display._msg_win = mock_win

        # get_state_snapshot needs to return a dict
        display._state = MagicMock()
        display._state.get_state_snapshot.return_value = _make_state_dict()
        display._state.add_ui_log_message = MagicMock()

        mock_curses.newwin.return_value = mock_win

        display.update_display(stdscr)

        mock_curses.resizeterm.assert_called_once_with(40, 100)

    @patch("status_display.curses")
    def test_normal_update_calls_draw_methods_and_doupdate(self, mock_curses):
        mock_curses.has_colors.return_value = False
        mock_curses.error = curses.error
        mock_curses.doupdate = MagicMock()

        display = _make_display()
        display._first_draw = False
        display._needs_redraw = False

        stdscr = MagicMock()
        stdscr.getmaxyx.return_value = (30, 80)
        display._stdscr = stdscr
        display._last_terminal_size = (30, 80)  # Same size, no resize

        mock_win = MagicMock()
        mock_win.getmaxyx.return_value = (20, 40)
        mock_win.getbegyx.return_value = (0, 0)
        display._header_win = mock_win
        display._info_win = mock_win
        display._sat_win = mock_win
        display._msg_win = mock_win

        display._state = MagicMock()
        display._state.get_state_snapshot.return_value = _make_state_dict()

        display.update_display(stdscr)

        # doupdate should be called at the end
        mock_curses.doupdate.assert_called_once()
        # State snapshot should have been fetched
        display._state.get_state_snapshot.assert_called_once()


# ===========================================================================
# TestShowHelpOverlay
# ===========================================================================

class TestShowHelpOverlay:
    """Tests for show_help_overlay modal behavior."""

    @patch("status_display.curses")
    def test_creates_overlay_and_waits_for_key(self, mock_curses):
        mock_curses.error = curses.error

        display = _make_display()
        stdscr = MagicMock()
        stdscr.getmaxyx.return_value = (30, 80)

        overlay_win = MagicMock()
        mock_curses.newwin.return_value = overlay_win

        display.show_help_overlay(stdscr)

        # Should create a new window for the overlay
        mock_curses.newwin.assert_called_once()
        overlay_win.box.assert_called_once()
        overlay_win.refresh.assert_called_once()

        # Should set nodelay(False) to block, then back to True
        stdscr.nodelay.assert_any_call(False)
        stdscr.nodelay.assert_any_call(True)
        stdscr.getch.assert_called_once()

    @patch("status_display.curses")
    def test_triggers_redraw_after_help(self, mock_curses):
        mock_curses.error = curses.error

        display = _make_display()
        display._needs_redraw = False

        stdscr = MagicMock()
        stdscr.getmaxyx.return_value = (30, 80)
        mock_curses.newwin.return_value = MagicMock()

        display.show_help_overlay(stdscr)

        assert display._needs_redraw is True


# ===========================================================================
# TestTriggerRedraw
# ===========================================================================

class TestTriggerRedraw:
    """Tests for trigger_redraw flag management."""

    def test_sets_needs_redraw_flag(self):
        display = _make_display()
        display._needs_redraw = False
        display.trigger_redraw()
        assert display._needs_redraw is True


# ===========================================================================
# TestClose
# ===========================================================================

class TestClose:
    """Tests for close cleanup logic."""

    def test_clears_window_handles_and_stdscr(self):
        display = _make_display()
        display._stdscr = MagicMock()
        display._header_win = MagicMock()
        display._info_win = MagicMock()
        display._sat_win = MagicMock()
        display._msg_win = MagicMock()

        display.close()

        assert display._stdscr is None
        assert display._header_win is None
        assert display._info_win is None
        assert display._sat_win is None
        assert display._msg_win is None

    def test_no_error_when_stdscr_is_none(self):
        display = _make_display()
        display._stdscr = None
        # Should not raise any exception
        display.close()

    def test_does_not_call_endwin(self):
        """close() must NOT call endwin — curses.wrapper handles that."""
        display = _make_display()
        display._stdscr = MagicMock()

        with patch("status_display.curses") as mock_curses:
            display.close()
            mock_curses.endwin.assert_not_called()


# ===========================================================================
# TestDrawInfoPanel
# ===========================================================================

class TestDrawInfoPanel:
    """Tests for _draw_info_panel GNSS/NTRIP info rendering."""

    def test_returns_early_when_win_is_none(self):
        display = _make_display()
        display._info_win = None
        state = _make_state_dict()
        # Should not raise
        display._draw_info_panel(state)

    @patch("status_display.curses")
    def test_rtk_fixed_uses_green_color(self, mock_curses):
        mock_curses.has_colors.return_value = True
        mock_curses.A_BOLD = curses.A_BOLD
        mock_curses.A_NORMAL = curses.A_NORMAL
        mock_curses.A_DIM = curses.A_DIM
        mock_curses.color_pair.side_effect = lambda n: n * 256
        mock_curses.error = curses.error

        display = _make_display()
        win = _make_mock_win(max_y=30, max_x=60)
        display._info_win = win

        state = _make_state_dict(rtk_status="RTK Fixed")
        display._draw_info_panel(state)

        # The RTK status value should be written with green color (pair(1)|BOLD)
        green_attr = (1 * 256) | curses.A_BOLD
        rtk_calls = [c for c in win.addstr.call_args_list
                     if len(c[0]) >= 4 and "RTK Fixed" in str(c[0][2])]
        assert len(rtk_calls) > 0, "RTK Fixed text should be written"
        assert rtk_calls[0][0][3] == green_attr, "RTK Fixed should use green|bold"

    @patch("status_display.curses")
    def test_rtk_float_uses_yellow_color(self, mock_curses):
        mock_curses.has_colors.return_value = True
        mock_curses.A_BOLD = curses.A_BOLD
        mock_curses.A_NORMAL = curses.A_NORMAL
        mock_curses.A_DIM = curses.A_DIM
        mock_curses.color_pair.side_effect = lambda n: n * 256
        mock_curses.error = curses.error

        display = _make_display()
        win = _make_mock_win(max_y=30, max_x=60)
        display._info_win = win

        state = _make_state_dict(rtk_status="RTK Float")
        display._draw_info_panel(state)

        yellow_attr = (2 * 256) | curses.A_BOLD
        rtk_calls = [c for c in win.addstr.call_args_list
                     if len(c[0]) >= 4 and "RTK Float" in str(c[0][2])]
        assert len(rtk_calls) > 0, "RTK Float text should be written"
        assert rtk_calls[0][0][3] == yellow_attr, "RTK Float should use yellow|bold"
