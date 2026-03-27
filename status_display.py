# status_display.py - Redesigned curses-based status display

import curses
import logging
from collections import Counter, deque
from datetime import datetime, timezone

from rtk_config import Config
from rtk_constants import *
from rtk_state import GnssState

# Use a logger specific to this module
display_logger = logging.getLogger(__name__)

class StatusDisplay:
    """Handles the curses-based status display with a simpler and more robust approach."""

    def __init__(self, state: GnssState, config: Config):
        self._state = state
        self._config = config
        self._logger = display_logger
        self._stdscr = None
        self._needs_redraw = True
        self._first_draw = True
        self._last_terminal_size = None

        # Window handles
        self._header_win = None
        self._info_win = None
        self._sat_win = None
        self._msg_win = None

        # Simple layout constants
        self.HEADER_HEIGHT = 3
        self.MSG_HEIGHT = 7
        self.MIN_WIDTH = 50
        self.MIN_HEIGHT = 15

    def _setup_curses(self, stdscr):
        """Set up basic curses environment."""
        self._stdscr = stdscr
        try:
            # Basic curses setup
            curses.curs_set(0)  # Hide cursor
            stdscr.nodelay(True)  # Non-blocking input
            stdscr.timeout(1000)  # 1 second timeout for getch()

            # Set up colors
            if curses.has_colors():
                curses.start_color()
                if curses.can_change_color():
                    curses.use_default_colors()
                    bg = -1  # Use terminal's default
                else:
                    bg = curses.COLOR_BLACK

                # Define color pairs
                curses.init_pair(1, curses.COLOR_GREEN, bg)
                curses.init_pair(2, curses.COLOR_YELLOW, bg)
                curses.init_pair(3, curses.COLOR_RED, bg)
                curses.init_pair(4, curses.COLOR_CYAN, bg)
                curses.init_pair(5, curses.COLOR_WHITE, bg)
                curses.init_pair(6, curses.COLOR_BLUE, bg)
                curses.init_pair(7, curses.COLOR_MAGENTA, bg)

            self._logger.debug("Basic curses setup complete.")
            return True
        except Exception as e:
            self._logger.critical(f"Failed to setup curses: {e}", exc_info=True)
            raise

    def _create_windows(self):
        """Create all display windows with direct positioning."""
        if not self._stdscr:
            return False

        try:
            # Clear screen and get dimensions
            self._stdscr.clear()
            max_y, max_x = self._stdscr.getmaxyx()
            self._last_terminal_size = (max_y, max_x)

            # Check minimum size
            if max_y < self.MIN_HEIGHT or max_x < self.MIN_WIDTH:
                warning = f"Terminal too small: {max_y}x{max_x}. Need {self.MIN_HEIGHT}x{self.MIN_WIDTH}"
                try:
                    self._stdscr.addstr(0, 0, warning[:max_x-1], curses.A_BOLD)
                    self._stdscr.refresh()
                except curses.error:
                    pass
                self._logger.warning(warning)
                # Continue anyway, but display might be compromised

            # Calculate layout
            header_h = self.HEADER_HEIGHT

            # Make message panel slightly smaller to prevent overflow
            msg_h = min(self.MSG_HEIGHT - 1, max(3, max_y // 6))  # Reduced height

            main_h = max_y - header_h - msg_h

            # Calculate horizontal split for info and sat panels
            info_w = max_x // 2
            sat_w = max_x - info_w - 1  # Leave 1 column for separator

            # Message panel starts at the bottom
            msg_y = max_y - msg_h

            # Create windows directly with absolute positions
            # Header window
            self._header_win = curses.newwin(header_h, max_x, 0, 0)

            # Info panel (left side)
            self._info_win = curses.newwin(main_h, info_w, header_h, 0)

            # Satellite panel (right side)
            self._sat_win = curses.newwin(main_h, sat_w, header_h, info_w + 1)

            # Message panel (bottom)
            self._msg_win = curses.newwin(msg_h, max_x, msg_y, 0)

            # Set all windows to clear on next update
            for win in [self._header_win, self._info_win, self._sat_win, self._msg_win]:
                if win:
                    win.clearok(True)

            self._logger.info(f"Created windows: {max_y}x{max_x}, main_h={main_h}, info_w={info_w}, sat_w={sat_w}")
            return True

        except Exception as e:
            self._logger.error(f"Failed to create windows: {e}", exc_info=True)
            # Clear any partially created windows
            self._header_win = None
            self._info_win = None
            self._sat_win = None
            self._msg_win = None
            return False
    def _draw_separator(self):
        """Draw vertical separator between info and sat panels."""
        if not self._stdscr or not self._info_win or not self._sat_win:
            return

        try:
            # Get dimensions
            max_y, max_x = self._stdscr.getmaxyx()
            _, info_w = self._info_win.getmaxyx()
            info_y, info_x = self._info_win.getbegyx()

            # Calculate separator position
            sep_x = info_x + info_w

            # Draw vertical line from header bottom to message panel top
            header_h = self.HEADER_HEIGHT
            msg_y = max_y - self.MSG_HEIGHT if self._msg_win else max_y

            # Draw the separator line
            for y in range(header_h, msg_y):
                try:
                    self._stdscr.addch(y, sep_x, curses.ACS_VLINE)
                except curses.error:
                    pass  # Ignore errors at screen edges

            # Add connecting characters at top and bottom
            try:
                self._stdscr.addch(header_h, sep_x, curses.ACS_TTEE)  # Top T
                if self._msg_win:
                    self._stdscr.addch(msg_y, sep_x, curses.ACS_BTEE)  # Bottom T
            except curses.error:
                pass  # Ignore edge errors

        except Exception as e:
            self._logger.debug(f"Error drawing separator: {e}")

    def _addstr_safe(self, win, y, x, text, attr=curses.A_NORMAL):
        """Safely add a string to a window, handling boundaries and errors."""
        if not win:
            return False

        try:
            max_y, max_x = win.getmaxyx()

            # Check if starting position is valid
            if y < 0 or y >= max_y or x < 0 or x >= max_x:
                return False

            # Calculate available width and truncate if needed
            available = max_x - x - 1  # Leave room for border/padding
            if available <= 0:
                return False

            # Truncate text to fit
            display_text = text[:available]

            # Add the string
            win.addstr(y, x, display_text, attr)
            return True

        except curses.error:
            # Silently handle curses errors (usually boundary issues)
            return False
        except Exception as e:
            self._logger.error(f"Error in addstr_safe: {e}")
            return False

    def _get_color(self, name):
        """Get color attribute by name with fallbacks if colors not available."""
        if not curses.has_colors():
            return curses.A_NORMAL

        # Color definitions
        colors = {
            "green": curses.color_pair(1) | curses.A_BOLD,
            "yellow": curses.color_pair(2) | curses.A_BOLD,
            "red": curses.color_pair(3) | curses.A_BOLD,
            "cyan": curses.color_pair(4),
            "white": curses.color_pair(5),
            "blue": curses.color_pair(6),
            "magenta": curses.color_pair(7),
            "bold": curses.A_BOLD,
            "normal": curses.A_NORMAL,
            "dim": curses.A_DIM
        }

        return colors.get(name.lower(), curses.A_NORMAL)

    def _draw_header(self, state):
        """Draw the header panel with title and status."""
        win = self._header_win
        if not win:
            return

        win.erase()
        win.box()

        max_y, max_x = win.getmaxyx()
        if max_y < 3 or max_x < 20:
            return  # Too small to draw anything meaningful

        # Draw title centered
        module = state.get('module_name', 'RTK GNSS')
        title = f" {module} RTK Status - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} "
        title_x = max(1, (max_x - len(title)) // 2)
        self._addstr_safe(win, 1, title_x, title, self._get_color("bold"))

        win.noutrefresh()

    def _draw_info_panel(self, state):
        """Draw the GNSS and NTRIP info panel (left side)."""
        win = self._info_win
        if not win:
            return

        win.erase()
        win.box()

        # Get dimensions
        max_y, max_x = win.getmaxyx()
        if max_y < 5 or max_x < 20:
            return  # Too small to draw content

        # Starting position
        y, x = 1, 2
        label_width = 15

        # Helper to draw a line
        def draw_line(label, value, value_color="white"):
            nonlocal y
            if y >= max_y - 1:
                return y  # No more space

            # Draw label
            label_str = f"{label:<{label_width}}:"
            self._addstr_safe(win, y, x, label_str, self._get_color("cyan"))

            # Draw value with appropriate color
            value_x = x + label_width + 1
            self._addstr_safe(win, y, value_x, str(value), self._get_color(value_color))

            y += 1
            return y

        # Draw section header
        def draw_section(title):
            nonlocal y
            if y >= max_y - 1:
                return y

            self._addstr_safe(win, y, x, title, self._get_color("yellow"))
            y += 1
            return y

        # GNSS Info Section
        y = draw_section("[GNSS Info]")

        # Runtime
        runtime = datetime.now(timezone.utc) - state.get('start_time', datetime.now(timezone.utc))
        y = draw_line("Runtime", str(runtime).split('.')[0])

        # Firmware
        y = draw_line("Firmware", state.get('firmware_version', 'N/A')[:20])

        # Position
        pos = state.get('position', {})
        y = draw_line("Latitude", f"{pos.get('lat', 0.0):.8f}°")
        y = draw_line("Longitude", f"{pos.get('lon', 0.0):.8f}°")
        y = draw_line("Altitude", f"{pos.get('alt', 0.0):.3f} m")

        # Fix information
        last_fix_time = state.get('last_fix_time')
        if last_fix_time:
            fix_age = (datetime.now(timezone.utc) - last_fix_time).total_seconds()
            age_color = "red" if fix_age > 30 else ("yellow" if fix_age > 10 else "white")
            y = draw_line("Fix Age", f"{fix_age:.1f} sec", age_color)
        else:
            y = draw_line("Fix Age", "N/A")

        # TTFF
        ttff = state.get('first_fix_time_sec')
        y = draw_line("TTFF", f"{ttff:.1f} sec" if ttff is not None else "Pending...")

        # RTK Status
        rtk_status = state.get('rtk_status', "Unknown")
        rtk_color = "green" if rtk_status == "RTK Fixed" else \
                    "yellow" if rtk_status == "RTK Float" else \
                    "white" if "GPS" in rtk_status or "DGPS" in rtk_status else "red"
        y = draw_line("RTK Status", rtk_status, rtk_color)

        # Fix quality and satellites
        y = draw_line("Fix Quality", state.get('fix_type', 0))
        y = draw_line("Sats Used/View", f"{state.get('num_satellites_used', 0)} / {state.get('num_satellites_in_view', 0)}")
        y = draw_line("HDOP", f"{state.get('hdop', DEFAULT_HDOP):.2f}")

        # Systems in view
        systems = state.get('satellite_systems', Counter())
        systems_str = ", ".join(f"{sys}:{c}" for sys, c in sorted(systems.items())) if systems else "N/A"
        y = draw_line("Systems View", systems_str)

        # NTRIP Section (add a blank line if space allows)
        if y < max_y - 5:
            y += 1
            y = draw_section("[NTRIP Info]")

            # Server info
            ntrip_host = f"{getattr(self._config, 'ntrip_server', 'N/A')}:{getattr(self._config, 'ntrip_port', 'N/A')}"
            y = draw_line("Server", ntrip_host)
            y = draw_line("Mountpoint", getattr(self._config, 'ntrip_mountpoint', 'N/A'))

            # Connection status
            ntrip_conn = state.get('ntrip_connected', False)
            ntrip_msg = state.get('ntrip_status_message', 'Unknown')
            gave_up = state.get('ntrip_connection_gave_up', False)

            if gave_up:
                status_str = f"Gave Up - {ntrip_msg}"
                status_color = "red"
            elif ntrip_conn:
                status_str = f"Connected - {ntrip_msg}"
                status_color = "green"
            elif "Retry" in ntrip_msg:
                status_str = f"Retrying - {ntrip_msg}"
                status_color = "yellow"
            elif "Connecting" in ntrip_msg:
                status_str = ntrip_msg
                status_color = "yellow"
            else:
                status_str = f"Disconnected - {ntrip_msg}"
                status_color = "red"

            y = draw_line("Status", status_str, status_color)

            # Reconnect attempts if relevant
            if not ntrip_conn and not gave_up and "Retry" not in ntrip_msg:
                reconnect_attempts = state.get('ntrip_reconnect_attempts', 0)
                if reconnect_attempts > 0:
                    retry_color = "yellow" if reconnect_attempts < MAX_NTRIP_RETRIES else "red"
                    y = draw_line("Reconnect", f"Attempt {reconnect_attempts}/{MAX_NTRIP_RETRIES}", retry_color)

            # RTCM data info
            if not ntrip_conn:
                y = draw_line("RTCM Age", "N/A")
            else:
                last_data_time = state.get('ntrip_last_data_time')
                if last_data_time:
                    age_seconds = (datetime.now(timezone.utc) - last_data_time).total_seconds()
                    age_color = "red" if age_seconds > NTRIP_DATA_TIMEOUT else \
                               "yellow" if age_seconds > 10 else "white"
                    y = draw_line("RTCM Age", f"{age_seconds:.1f} sec", age_color)
                else:
                    y = draw_line("RTCM Age", "N/A")

            # RTCM data rates and totals
            rates_deque = state.get('ntrip_data_rates', deque())
            avg_rate = sum(rates_deque) / len(rates_deque) if rates_deque else 0.0
            y = draw_line("RTCM Rate", f"{avg_rate:.1f} B/s")
            y = draw_line("Total RTCM", f"{state.get('ntrip_total_bytes', 0):,} B")

            # RTCM message types
            rtcm_types = list(state.get('last_rtcm_message_types', deque()))
            if rtcm_types:
                unique_types = []
                for t in reversed(rtcm_types):
                    if t not in unique_types:
                        unique_types.append(t)
                    if len(unique_types) >= 5:
                        break
                types_str = '[' + ', '.join(map(str, reversed(unique_types))) + ']'
                if len(rtcm_types) > len(unique_types):
                    types_str += '...'
            else:
                types_str = 'None Received'

            y = draw_line("RTCM Types", types_str)

        win.noutrefresh()

    def _draw_sat_panel(self, state):
        """Draw the satellite information panel (right side)."""
        win = self._sat_win
        if not win:
            return

        win.erase()
        win.box()

        max_y, max_x = win.getmaxyx()
        if max_y < 5 or max_x < 20:
            return  # Too small to draw content

        # Start position
        y, x = 1, 2

        # Draw title
        title = "[Satellites in View]"
        self._addstr_safe(win, y, x, title, self._get_color("yellow"))
        y += 1

        # Define column format and widths
        col_fmt = "{:>3} {:<3} {:>4} {:>3} {:>3} {:<3}"
        header = col_fmt.format("PRN", "Sys", "SNR", "El", "Az", "Use")
        separator = "-" * (len(header) if len(header) < max_x - 4 else max_x - 4)

        # Check if we have space for the table
        if max_x < 20 or y >= max_y - 3:
            self._addstr_safe(win, y, x, "Panel too narrow", self._get_color("yellow"))
            win.noutrefresh()
            return

        # Draw header and separator
        self._addstr_safe(win, y, x, header, self._get_color("bold"))
        y += 1
        self._addstr_safe(win, y, x, separator)
        y += 1

        # Get satellite data
        satellites = state.get('satellites_info', {})

        # Debug: If no satellites, show message
        if not satellites:
            self._addstr_safe(win, y, x, "No satellite data available", self._get_color("yellow"))
            win.noutrefresh()
            return

        # Sort satellites by system then PRN
        def sort_key(item):
            _, sat_data = item
            try:
                prn = int(sat_data.get('prn', '999'))
            except ValueError:
                prn = 999
            return (sat_data.get('system', 'zzz'), prn)

        sorted_sats = sorted(satellites.items(), key=sort_key)

        # Display satellites
        for sat_key, sat in sorted_sats:
            if y >= max_y - 1:
                # No more room, show truncation indicator
                self._addstr_safe(win, max_y - 2, x, "...more satellites...", self._get_color("yellow"))
                break

            # Extract satellite data
            prn = sat.get('prn', '??')
            system = sat.get('system', 'UNK')
            snr = sat.get('snr', 0)
            elev = sat.get('elevation')
            azim = sat.get('azimuth')
            active = sat.get('active', False)

            # Format system abbreviation
            sys_abbr = {
                "GPS": "GPS",
                "GLONASS": "GLO",
                "Galileo": "GAL",
                "BeiDou": "BDS",
                "QZSS": "QZS",
                "NavIC": "NAV"
            }.get(system, system[:3].upper())

            # Choose system color
            sys_color = {
                "GPS": "green",
                "GLONASS": "yellow",
                "Galileo": "blue",
                "BeiDou": "red",
                "QZSS": "magenta"
            }.get(system, "normal")

            # Choose SNR color
            if snr is not None and snr > 0:
                snr_color = "green" if snr >= SNR_THRESHOLD_GOOD else \
                           "yellow" if snr >= SNR_THRESHOLD_BAD else "red"
            else:
                snr_color = "dim"

            # Format fields
            prn_str = f"{prn:>3}"
            sys_str = f"{sys_abbr:<3}"
            snr_str = f"{snr if snr is not None else '-':>4}"
            elev_str = f"{elev if elev is not None else '-':>3}"
            azim_str = f"{azim if azim is not None else '-':>3}"
            use_str = f"{'[*]':<3}" if active else f"{'[ ]':<3}"

            # Draw each field with appropriate color
            col_x = x
            self._addstr_safe(win, y, col_x, prn_str)
            col_x += 4

            self._addstr_safe(win, y, col_x, sys_str, self._get_color(sys_color))
            col_x += 4

            self._addstr_safe(win, y, col_x, snr_str, self._get_color(snr_color))
            col_x += 5

            self._addstr_safe(win, y, col_x, elev_str)
            col_x += 4

            self._addstr_safe(win, y, col_x, azim_str)
            col_x += 4

            use_attr = self._get_color("bold") if active else self._get_color("normal")
            self._addstr_safe(win, y, col_x, use_str, use_attr)

            y += 1

        win.noutrefresh()

    def _draw_msg_panel(self, state):
        """Draw the message log panel at the bottom."""
        win = self._msg_win
        if not win:
            return

        win.erase()
        win.box()

        max_y, max_x = win.getmaxyx()
        if max_y < 3 or max_x < 20:
            return  # Too small

        # Draw title
        title = "[Messages]"
        self._addstr_safe(win, 0, 2, title, self._get_color("yellow"))

        # Get messages
        messages = state.get('ui_log_messages', deque())
        total_msgs = len(messages)

        # Calculate how many messages we can show
        # Use max_y - 2 to ensure we stay inside borders
        available_lines = max_y - 2  # Subtract borders

        # Adjust available_lines to ensure no overflow
        available_lines = max(1, available_lines - 1)  # Reserve one more line as safety margin

        start_idx = max(0, total_msgs - available_lines)

        # Show indicator for hidden messages
        if start_idx > 0:
            indicator = f"[+{start_idx}]"
            indicator_x = max_x - len(indicator) - 2
            self._addstr_safe(win, 0, indicator_x, indicator, self._get_color("bold"))

        # Display messages
        line = 1  # Start after top border
        for i in range(start_idx, total_msgs):
            if line >= max_y - 1:
                break  # No more room

            msg = messages[i]

            # Determine color based on content
            msg_color = "normal"
            msg_lower = msg.lower()

            if any(err in msg_lower for err in ["error", "failed", "fatal", "critical", "timeout", "gave up"]):
                msg_color = "red"
            elif any(warn in msg_lower for warn in ["warn", "reconnecting", "retry"]):
                msg_color = "yellow"
            elif any(ok in msg_lower for ok in ["connect", "success", "fixed", "config"]):
                msg_color = "green"

            # Display message with safe truncation
            self._addstr_safe(win, line, 2, msg, self._get_color(msg_color))
            line += 1

        win.noutrefresh()

    def update_display(self, stdscr):
        """Main display update method."""
        try:
            # First-time setup
            if self._first_draw:
                self._setup_curses(stdscr)
                if not self._create_windows():
                    self._logger.error("Failed to create windows on first draw")
                    return
                self._first_draw = False
                self._needs_redraw = False
                self._stdscr.refresh()
                return  # Wait for next update to draw content

            # Check for resize
            if self._stdscr:
                cur_y, cur_x = self._stdscr.getmaxyx()
                last_size = self._last_terminal_size or (0, 0)
                if (cur_y, cur_x) != last_size:
                    self._logger.info(f"Terminal resized: {last_size[0]}x{last_size[1]} -> {cur_y}x{cur_x}")
                    curses.resizeterm(cur_y, cur_x)
                    self._needs_redraw = True
                    self._state.add_ui_log_message("Terminal resized")

            # Handle redraw if needed
            if self._needs_redraw:
                if not self._create_windows():
                    self._logger.error("Failed to recreate windows after resize")
                    return
                self._needs_redraw = False

            # Get current state
            state = self._state.get_state_snapshot()

            # Draw each panel
            self._draw_header(state)
            self._draw_info_panel(state)
            self._draw_sat_panel(state)
            self._draw_msg_panel(state)

            # Draw separator between panels
            self._draw_separator()

            # Update the screen
            curses.doupdate()

        except curses.error as e:
            self._logger.error(f"Curses error in update_display: {e}")
            self.trigger_redraw()
        except Exception as e:
            self._logger.error(f"Unexpected error in update_display: {e}", exc_info=True)
            self.trigger_redraw()

    def show_help_overlay(self, stdscr) -> None:
        """Shows a help overlay with keyboard shortcuts."""
        max_y, max_x = stdscr.getmaxyx()
        # Calculate overlay dimensions
        help_lines = [
            "Keyboard Shortcuts",
            "",
            "  q  -  Quit application",
            "  r  -  Reset NTRIP connection",
            "  ?  -  Show this help",
            "",
            "Press any key to close",
        ]
        h = len(help_lines) + 4
        w = max(len(line) for line in help_lines) + 6
        y = max(0, (max_y - h) // 2)
        x = max(0, (max_x - w) // 2)

        try:
            win = curses.newwin(h, w, y, x)
            win.box()
            for i, line in enumerate(help_lines):
                self._addstr_safe(win, i + 2, 3, line, curses.A_NORMAL)
            win.refresh()
            stdscr.nodelay(False)
            stdscr.getch()  # Wait for any key
            stdscr.nodelay(True)
            self.trigger_redraw()
        except curses.error:
            pass

    def trigger_redraw(self):
        """Request a full redraw on next update."""
        self._needs_redraw = True
        self._logger.info("Full redraw triggered")

    def close(self):
        """Clean up curses environment if needed."""
        if self._stdscr and not self._stdscr.isendwin():
            try:
                curses.nocbreak()
                self._stdscr.keypad(False)
                curses.echo()
                curses.endwin()
                self._logger.info("Curses environment closed")
            except Exception as e:
                self._logger.error(f"Error closing curses: {e}")
