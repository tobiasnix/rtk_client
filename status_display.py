# status_display.py - Handles the curses-based status display

import curses
import time
import logging
from datetime import datetime, timezone
from collections import deque, Counter
from typing import Optional, Dict, Any, Tuple, List, Callable
from rtk_state import GnssState
from rtk_config import Config
from rtk_constants import *

# Use a logger specific to this module
display_logger = logging.getLogger(__name__) # Renamed from file_logger

class StatusDisplay:
    """Handles the curses-based status display with improved error handling and modularity."""

    # Panel layout constants
    HEADER_HEIGHT = 3
    MESSAGE_PANEL_HEIGHT = 7 # Default, can be adjusted by terminal size
    MIN_TERMINAL_HEIGHT = 15 # Reduced slightly
    MIN_TERMINAL_WIDTH = 60  # Reduced slightly

    # Color definitions and thresholds
    SNR_THRESHOLD_GOOD = 35
    SNR_THRESHOLD_BAD = 20

    def __init__(self, state: GnssState, config: Config):
        self._state = state
        self._config = config
        # Use the module-specific logger
        self._logger = display_logger # Changed from getLogger(self.__class__.__name__)
        self._stdscr: Optional[curses.window] = None
        self._panels: Dict[str, Optional[curses.window]] = {} # Allow None for panels
        self._needs_redraw = True
        self._first_draw = True
        self._last_terminal_size: Optional[Tuple[int, int]] = None

        # Layout definition (remains the same)
        self._layout = {
            "header": {"y": 0, "x": 0, "h": self.HEADER_HEIGHT, "w": 0},
            "info":   {"y": self.HEADER_HEIGHT, "x": 0, "h": 0, "w": 0},
            "sat":    {"y": self.HEADER_HEIGHT, "x": 0, "h": 0, "w": 0},
            "msg":    {"y": 0, "x": 0, "h": self.MESSAGE_PANEL_HEIGHT, "w": 0}
        }

        # Initialize color attributes (will be properly set in _setup_curses)
        self._initialize_color_attributes()

    def _initialize_color_attributes(self, use_colors=True):
        """Initializes color attributes, either with color pairs or fallbacks."""
        if use_colors and curses.has_colors():
            try:
                curses.start_color()
                if curses.can_change_color():
                    # Use terminal's default background if possible
                    curses.use_default_colors()
                    bg_color = -1
                else:
                    # Fallback for terminals that can't change default colors
                    bg_color = curses.COLOR_BLACK

                # Define color pairs (using -1 for default background)
                curses.init_pair(1, curses.COLOR_GREEN, bg_color)
                curses.init_pair(2, curses.COLOR_YELLOW, bg_color)
                curses.init_pair(3, curses.COLOR_RED, bg_color)
                curses.init_pair(4, curses.COLOR_CYAN, bg_color)
                curses.init_pair(5, curses.COLOR_WHITE, bg_color)
                curses.init_pair(6, curses.COLOR_BLUE, bg_color)
                curses.init_pair(7, curses.COLOR_MAGENTA, bg_color) # Added Magenta for QZSS

                # Set color attributes
                self.COLOR_GREEN = curses.color_pair(1) | curses.A_BOLD
                self.COLOR_YELLOW = curses.color_pair(2) | curses.A_BOLD
                self.COLOR_RED = curses.color_pair(3) | curses.A_BOLD
                self.COLOR_LABEL = curses.color_pair(4) # Cyan Label
                self.COLOR_VALUE = curses.color_pair(5) # White Value
                self.COLOR_NORMAL = curses.A_NORMAL # Use default terminal color/attrs
                self.ATTR_BOLD = curses.A_BOLD

                # Satellite Colors
                self.COLOR_SAT_GPS = curses.color_pair(1) # Green
                self.COLOR_SAT_GLO = curses.color_pair(2) # Yellow
                self.COLOR_SAT_GAL = curses.color_pair(6) | curses.A_BOLD # Bold Blue
                self.COLOR_SAT_BDS = curses.color_pair(3) # Red
                self.COLOR_SAT_QZS = curses.color_pair(7) # Magenta
                self.COLOR_SAT_OTH = curses.A_DIM # Dim for others

                self._logger.debug("Color pairs initialized.")
                return True

            except curses.error as e:
                self._logger.warning(f"Curses color setup failed: {e}. Using fallback attributes.")
                # Fall through to fallback if initialization fails
            except Exception as e:
                 self._logger.error(f"Unexpected error during color setup: {e}", exc_info=True)
                 # Fall through to fallback

        # Fallback (no colors or error during setup)
        self._logger.debug("Using fallback (monochrome) attributes.")
        self.COLOR_GREEN = curses.A_BOLD
        self.COLOR_YELLOW = curses.A_BOLD
        self.COLOR_RED = curses.A_BOLD
        self.COLOR_LABEL = curses.A_NORMAL
        self.COLOR_VALUE = curses.A_BOLD
        self.COLOR_NORMAL = curses.A_NORMAL
        self.ATTR_BOLD = curses.A_BOLD
        self.COLOR_SAT_GPS = curses.A_NORMAL
        self.COLOR_SAT_GLO = curses.A_NORMAL
        self.COLOR_SAT_GAL = curses.A_BOLD
        self.COLOR_SAT_BDS = curses.A_NORMAL
        self.COLOR_SAT_QZS = curses.A_BOLD
        self.COLOR_SAT_OTH = curses.A_DIM
        return False

    def _setup_curses(self, stdscr):
        """Sets up curses environment."""
        self._stdscr = stdscr
        try:
            curses.curs_set(0) # Hide cursor
            stdscr.nodelay(True) # Non-blocking input
            stdscr.timeout(1000) # Timeout for getch() in ms

            # Initialize colors (or fallbacks if not supported)
            self._initialize_color_attributes()

            self._logger.debug("Curses basic setup complete.")
        except curses.error as e:
            # Log critical error if basic setup fails
            self._logger.critical(f"Basic curses setup failed: {e}.", exc_info=True)
            # Re-raise? Wrapper might handle it, but good to know.
            raise
        except Exception as e:
            self._logger.critical(f"Unexpected error during basic curses setup: {e}", exc_info=True)
            raise

    def _create_windows(self):
        """Creates or recreates the display windows based on terminal size."""
        if not self._stdscr:
            self._logger.error("Cannot create windows: stdscr not available.")
            return False

        try:
            # Clear screen and get dimensions
            self._stdscr.clear()
            max_y, max_x = self._stdscr.getmaxyx()
            self._last_terminal_size = (max_y, max_x) # Store current size

            # Reset panel dictionary
            self._panels = {"header": None, "info": None, "sat": None, "msg": None}

            # Check minimum size requirement
            if max_y < self.MIN_TERMINAL_HEIGHT or max_x < self.MIN_TERMINAL_WIDTH:
                # Display warning directly on screen if possible
                warning_msg = f"Terminal too small ({max_y}x{max_x}). Min {self.MIN_TERMINAL_HEIGHT}x{self.MIN_TERMINAL_WIDTH} recommended."
                self._logger.warning(warning_msg)
                try:
                    self._stdscr.addstr(0, 0, warning_msg[:max_x-1], self.COLOR_YELLOW)
                    self._stdscr.refresh()
                except curses.error: pass # Ignore if we can't even write the warning
                # Don't create panels if too small? Or let them be created and potentially fail drawing?
                # Let's try creating them, drawing functions should handle small sizes.
                # return False # Optionally stop here

            # Calculate panel dimensions dynamically
            header_h = self._layout["header"]["h"]
            # Ensure message panel height is reasonable, not too large or small
            msg_h = max(3, min(self._layout["msg"]["h"], max_y // 3))
            # Main area height takes the rest
            main_h = max(5, max_y - header_h - msg_h) # Ensure main area has some height
            # Adjust msg_h if total exceeds max_y
            if header_h + main_h + msg_h > max_y:
                msg_h = max(3, max_y - header_h - main_h)

            # Calculate widths (ensure positive)
            info_w = max(1, max_x // 2)
            sat_w = max(1, max_x - info_w)

            # Calculate message panel starting position (ensure non-negative)
            msg_y = max(0, max_y - msg_h)

            # Create the windows, checking dimensions before creation
            create_panel = lambda h, w, y, x: self._stdscr.derwin(h, w, y, x) if h > 0 and w > 0 else None

            self._panels["header"] = create_panel(header_h, max_x, 0, 0)
            self._panels["info"] = create_panel(main_h, info_w, header_h, 0)
            self._panels["sat"] = create_panel(main_h, sat_w, header_h, info_w)
            self._panels["msg"] = create_panel(msg_h, max_x, msg_y, 0)

            self._logger.debug(f"Windows created/resized: H({header_h}x{max_x}) M({msg_h}x{max_x} @{msg_y}) I({main_h}x{info_w}) S({main_h}x{sat_w})")

            # Mark all *successfully created* panels for full redraw
            for panel in self._panels.values():
                if panel:
                    panel.clearok(True)
            return True

        except curses.error as e:
            self._logger.error(f"Error creating curses windows: {e}.")
            self._panels = {} # Ensure panels is empty on error
            return False
        except Exception as e:
            self._logger.error(f"Unexpected error creating curses windows: {e}", exc_info=True)
            self._panels = {}
            return False

    def _draw_borders(self):
        """Draws borders around panels."""
        if not self._stdscr: return

        # Draw borders for main panels if they exist
        for name in ["info", "sat", "msg"]:
             panel = self._panels.get(name)
             if panel:
                 self._safe_call(panel.border)

        # Draw separator between info and sat panels more robustly
        info_panel = self._panels.get("info")
        sat_panel = self._panels.get("sat")
        header_panel = self._panels.get("header")
        msg_panel = self._panels.get("msg")

        if info_panel and sat_panel:
            try:
                info_h, info_w = info_panel.getmaxyx()
                sat_h, _ = sat_panel.getmaxyx()
                header_h = header_panel.getmaxyx()[0] if header_panel else 0
                msg_y, _ = msg_panel.getbegyx() if msg_panel else (self._stdscr.getmaxyx()[0], 0)

                sep_x = info_panel.getbegyx()[1] + info_w -1 # X coordinate of the separator line

                # Calculate start and end Y, respecting panel boundaries
                start_y = info_panel.getbegyx()[0] # Should be header_h
                sep_h = min(info_h, sat_h) # Height of the drawable area
                end_y = start_y + sep_h -1 # Last Y coordinate for the line

                # Ensure separator doesn't overwrite message panel top border
                if msg_panel and end_y >= msg_y:
                    end_y = msg_y - 1

                # Ensure separator doesn't overwrite header panel bottom border (if any)
                # Header border is drawn by stdscr, not the panel itself usually.
                # But ensure start_y is correct.
                if start_y < header_h: start_y = header_h

                # Draw the vertical line
                for y in range(start_y, end_y + 1):
                    # Determine the character for junctions
                    char = curses.ACS_VLINE
                    if y == start_y and header_panel: # Top junction
                        char = curses.ACS_TTEE
                    elif y == end_y and y == msg_y -1 and msg_panel: # Bottom junction with msg panel
                        char = curses.ACS_BTEE

                    # Safely draw the character on stdscr
                    self._safe_addch(self._stdscr, y, sep_x, char)

            except curses.error as e:
                 self._logger.debug(f"Minor error drawing separator: {e}")
            except Exception as e:
                 self._logger.warning(f"Unexpected error drawing separator: {e}", exc_info=True)


    def _safe_call(self, func: Callable, *args, **kwargs):
        """Safely calls a curses function, catching known errors."""
        try:
            return func(*args, **kwargs)
        except curses.error as e:
            # Log curses errors, but allow execution to continue where possible
            self._logger.debug(f"Curses error in safe call to {func.__name__}: {e}")
        except Exception as e:
            # Log unexpected errors more seriously
            self._logger.error(f"Unexpected error in safe call to {func.__name__}: {e}", exc_info=True)
        return None

    def _safe_addch(self, win: Optional[curses.window], y: int, x: int, char: Any, attr=curses.A_NORMAL):
        """Safely adds a single character to a window."""
        if not win: return False
        try:
            max_y, max_x = win.getmaxyx()
            # Check boundaries EXCLUDING the bottom-right corner
            if 0 <= y < max_y and 0 <= x < max_x:
                # Useinsch for potential border drawing on stdscr, addch otherwise
                if win == self._stdscr:
                     win.insch(y, x, char, attr)
                else:
                     win.addch(y, x, char, attr)
                return True
            else:
                # self._logger.debug(f"Skipped addch at invalid coord ({y},{x}) in window {win.getbegyx()}")
                return False
        except curses.error as e:
            self._logger.debug(f"Curses error adding char at ({y},{x}): {e}")
            return False
        except Exception as e:
            self._logger.error(f"Unexpected error adding char at ({y},{x}): {e}", exc_info=True)
            return False


    def _safe_addstr(self, win: Optional[curses.window], y: int, x: int, text: str, attr=curses.A_NORMAL):
        """Safely adds a string, handling boundaries and errors."""
        if not win: return False
        try:
            max_y, max_x = win.getmaxyx()
            # Check if starting position is valid
            if 0 <= y < max_y and 0 <= x < max_x:
                # Truncate string to fit window width from starting position
                available_width = max_x - x
                truncated_text = text[:max(0, available_width)]
                # Add the potentially truncated string
                win.addstr(y, x, truncated_text, attr)
                return True
            else:
                # self._logger.debug(f"Skipped addstr at invalid start coord ({y},{x})")
                return False
        except curses.error as e:
            # This error often occurs when trying to write exactly at max_y-1, max_x-1
            # It's often harmless visually but indicates boundary issues.
            self._logger.debug(f"Curses error adding string at ({y},{x}): '{text[:20]}...' - {e}")
            return False
        except Exception as e:
            self._logger.error(f"Unexpected error adding string at ({y},{x}): {e}", exc_info=True)
            return False

    def _draw_header(self, win: Optional[curses.window], state):
        """Draws the header panel."""
        if not win: return
        win.erase()
        max_y, max_x = win.getmaxyx()
        if max_y < 3 or max_x < 10: return # Not enough space

        # Draw horizontal lines (optional, border might be sufficient)
        # self._safe_call(win.hline, 0, 0, curses.ACS_HLINE, max_x)
        # self._safe_call(win.hline, max_y - 1, 0, curses.ACS_HLINE, max_x)
        self._safe_call(win.border) # Use border instead of hlines

        # Draw title centered
        title = f" LC29HDA RTK Status - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} "
        title_x = max(1, (max_x - len(title)) // 2) # Ensure at least x=1
        self._safe_addstr(win, 1, title_x, title, self.ATTR_BOLD | self.COLOR_VALUE)

    def _draw_info_panel(self, win: Optional[curses.window], state):
        """Draws GNSS and NTRIP info panel."""
        if not win: return
        win.erase()
        self._safe_call(win.border) # Draw border first

        max_y, max_x = win.getmaxyx()
        y, x = 1, 2 # Start position inside border
        label_width = 20 # Adjusted width for labels
        value_x = x + label_width + 1 # Start position for values

        # Helper to draw a line, returns next Y
        def draw_line(label, value, value_attr=self.COLOR_VALUE):
            nonlocal y
            # Check if panel is high enough for this line
            if y >= max_y - 1: return y # Stop if no more space

            label_str = f"{label:<{label_width}}:"
            self._safe_addstr(win, y, x, label_str, self.COLOR_LABEL)

            # Calculate available width for value and truncate
            available_width = max_x - value_x - 1 # -1 for right border padding
            value_str = str(value)
            truncated_value = value_str[:max(0, available_width)]
            self._safe_addstr(win, y, value_x, truncated_value, value_attr)

            y += 1
            return y

        # Helper for section titles
        def draw_section_title(title):
            nonlocal y
            if y >= max_y - 1: return y
            self._safe_addstr(win, y, x, title, self.ATTR_BOLD | self.COLOR_YELLOW)
            y += 1
            return y

        # --- GNSS Info Section ---
        y = draw_section_title("[GNSS Info]")
        runtime = datetime.now(timezone.utc) - state.get('start_time', datetime.now(timezone.utc))
        y = draw_line("Runtime", str(runtime).split('.')[0])
        y = draw_line("Firmware", state.get('firmware_version', 'N/A')[:25]) # Limit FW length display
        pos = state.get('position', {})
        y = draw_line("Latitude", f"{pos.get('lat', 0.0):.8f}\N{DEGREE SIGN}")
        y = draw_line("Longitude", f"{pos.get('lon', 0.0):.8f}\N{DEGREE SIGN}")
        y = draw_line("Altitude", f"{pos.get('alt', 0.0):.3f} m")

        last_fix_time = state.get('last_fix_time')
        if last_fix_time:
            fix_age = (datetime.now(timezone.utc) - last_fix_time).total_seconds()
            age_attr = self.COLOR_RED if fix_age > 30 else (self.COLOR_YELLOW if fix_age > 10 else self.COLOR_VALUE)
            y = draw_line("Fix Age", f"{fix_age:.1f} sec", value_attr=age_attr)
        else:
            y = draw_line("Fix Age", "N/A")

        ttff = state.get('first_fix_time_sec')
        y = draw_line("TTFF", f"{ttff:.1f} sec" if ttff is not None else "Pending...")

        rtk_status = state.get('rtk_status', "Unknown")
        rtk_attr = self.ATTR_BOLD
        if rtk_status == "RTK Fixed": rtk_attr |= self.COLOR_GREEN
        elif rtk_status == "RTK Float": rtk_attr |= self.COLOR_YELLOW
        elif "GPS" in rtk_status or "DGPS" in rtk_status: rtk_attr |= self.COLOR_VALUE
        else: rtk_attr |= self.COLOR_RED # No Fix, Unknown, Estimated
        y = draw_line("RTK Status", rtk_status, value_attr=rtk_attr)

        y = draw_line("Fix Quality Code", state.get('fix_type', 0))
        y = draw_line("Sats Used / View", f"{state.get('num_satellites_used', 0)} / {state.get('num_satellites_in_view', 0)}")
        y = draw_line("HDOP", f"{state.get('hdop', DEFAULT_HDOP):.2f}")
        systems = state.get('satellite_systems', Counter())
        systems_str = ", ".join(f"{sys}:{c}" for sys, c in sorted(systems.items())) if systems else "N/A"
        y = draw_line("Systems View", systems_str)

        # --- NTRIP Info Section ---
        y += 1 # Add a blank line separator if space allows
        if y < max_y - 1 : y = draw_section_title("[NTRIP Info]")

        ntrip_host = f"{getattr(self._config, 'ntrip_server', 'N/A')}:{getattr(self._config, 'ntrip_port', 'N/A')}"
        y = draw_line("Server", ntrip_host)
        y = draw_line("Mountpoint", getattr(self._config, 'ntrip_mountpoint', 'N/A'))

        ntrip_conn = state.get('ntrip_connected', False)
        ntrip_msg = state.get('ntrip_status_message', 'Unknown')
        gave_up = state.get('ntrip_connection_gave_up', False)
        ntrip_attr = self.COLOR_VALUE # Default attribute

        display_status = ntrip_msg # Start with the raw status message
        if gave_up:
            display_status = f"Gave Up - {ntrip_msg}"
            ntrip_attr = self.COLOR_RED | self.ATTR_BOLD
        elif ntrip_conn:
            display_status = f"Connected - {ntrip_msg}"
            ntrip_attr = self.COLOR_GREEN
        elif "Retry" in ntrip_msg:
             # Message already formatted with countdown in ntrip_client
             display_status = f"Retrying - {ntrip_msg}"
             ntrip_attr = self.COLOR_YELLOW
        elif "Connecting" in ntrip_msg:
             display_status = ntrip_msg
             ntrip_attr = self.COLOR_YELLOW
        else: # Disconnected, Failed, Error, etc.
            display_status = f"Disconnected - {ntrip_msg}"
            ntrip_attr = self.COLOR_RED

        y = draw_line("Status", display_status, value_attr=ntrip_attr)

        # Show reconnect attempts only if relevant (not connected, not gave up)
        if not ntrip_conn and not gave_up and "Retry" not in ntrip_msg:
             reconnect_attempts = state.get('ntrip_reconnect_attempts', 0)
             if reconnect_attempts > 0:
                 retry_attr = self.COLOR_YELLOW if reconnect_attempts < MAX_NTRIP_RETRIES else self.COLOR_RED
                 y = draw_line("Reconnect Status", f"Attempt {reconnect_attempts}/{MAX_NTRIP_RETRIES}", value_attr=retry_attr)


        last_data_time = state.get('ntrip_last_data_time')
        if not last_data_time or not ntrip_conn: # Show N/A if not connected
            y = draw_line("RTCM Age", "N/A")
        else:
            age_seconds = (datetime.now(timezone.utc) - last_data_time).total_seconds()
            attr = self.COLOR_RED if age_seconds > NTRIP_DATA_TIMEOUT else (self.COLOR_YELLOW if age_seconds > 10 else self.COLOR_VALUE)
            y = draw_line("RTCM Age", f"{age_seconds:.1f} sec", value_attr=attr)

        rates_deque = state.get('ntrip_data_rates', deque())
        avg_rate_bps = sum(rates_deque) / len(rates_deque) if rates_deque else 0.0
        y = draw_line("RTCM Rate (avg)", f"{avg_rate_bps:.1f} B/s")
        y = draw_line("Total RTCM Bytes", f"{state.get('ntrip_total_bytes', 0):,}")
        # Don't display reconnect attempts here again, shown with status

        rtcm_types_list = list(state.get('last_rtcm_message_types', deque()))
        if rtcm_types_list:
             # Show only last few unique types
             unique_types = []
             for rtcm_type in reversed(rtcm_types_list):
                 if rtcm_type not in unique_types:
                     unique_types.append(rtcm_type)
                 if len(unique_types) >= 5: break # Limit display
             types_str = '[' + ', '.join(map(str, reversed(unique_types))) + ']'
             if len(rtcm_types_list) > len(unique_types): types_str += '...'
        else:
             types_str = 'None Received'
        y = draw_line("Last RTCM Types", types_str)

        # Ensure panel doesn't draw over its own border
        win.noutrefresh()


    def _draw_sat_panel(self, win: Optional[curses.window], state):
        """Draws satellite information panel."""
        if not win: return
        win.erase()
        self._safe_call(win.border)

        max_y, max_x = win.getmaxyx()
        y, x = 1, 2
        title = "[Satellites in View]"
        if y < max_y -1: y = self._safe_addstr(win, y, x, title, self.ATTR_BOLD | self.COLOR_YELLOW) + 1

        # Define columns and calculate total width
        # Adjusted widths slightly
        header_fmt = "{:>3} {:<3} {:>4} {:>3} {:>3} {:<3}"
        header = header_fmt.format("PRN", "Sys", "SNR", "El", "Az", "Use")
        col_widths = [3, 3, 4, 3, 3, 3]
        col_spacing = 1
        total_width = sum(col_widths) + col_spacing * (len(col_widths) - 1)
        separator = "-" * total_width

        header_drawn = False
        if y < max_y - 2 and max_x > x + total_width:
             self._safe_addstr(win, y, x, header, self.ATTR_BOLD)
             y += 1
             self._safe_addstr(win, y, x, separator)
             y += 1
             header_drawn = True
        elif max_x <= x + total_width and y < max_y - 1:
             self._safe_addstr(win, y, x, "Panel too narrow", self.COLOR_YELLOW)
             y += 1

        satellites_info = state.get('satellites_info', {})

        # Sort satellites by system then PRN (integer conversion for PRN)
        def sort_key(item):
            _, sat_data = item
            prn_int = 999
            try: prn_int = int(sat_data.get('prn', '999'))
            except ValueError: pass
            return (sat_data.get('system', 'zzz'), prn_int)

        sorted_sats = sorted(satellites_info.items(), key=sort_key)

        # Draw each satellite if space allows
        for _, sat_info in sorted_sats:
            if y >= max_y - 1: # Check before drawing
                 # Optionally indicate truncation
                 if x + 15 < max_x: self._safe_addstr(win, y, x, "...truncated...", self.COLOR_YELLOW)
                 break

            # Extract data safely
            prn = sat_info.get('prn', '??')
            system = sat_info.get('system', 'UNK')
            snr = sat_info.get('snr') # Can be None or 0
            elev = sat_info.get('elevation') # Can be None
            azim = sat_info.get('azimuth')   # Can be None
            active = sat_info.get('active', False)

            # System attribute and short name
            sys_attr, sys_short = self.COLOR_SAT_OTH, system[:3].upper()
            if system == "GPS": sys_attr, sys_short = self.COLOR_SAT_GPS, "GPS"
            elif system == "GLONASS": sys_attr, sys_short = self.COLOR_SAT_GLO, "GLO"
            elif system == "Galileo": sys_attr, sys_short = self.COLOR_SAT_GAL, "GAL"
            elif system == "BeiDou": sys_attr, sys_short = self.COLOR_SAT_BDS, "BDS"
            elif system == "QZSS": sys_attr, sys_short = self.COLOR_SAT_QZS, "QZS"
            elif system == "NavIC": sys_attr, sys_short = self.COLOR_SAT_OTH, "NAV"

            # SNR color
            snr_attr = self.COLOR_NORMAL | curses.A_DIM
            if snr is not None and snr > 0: # Check snr is not None before comparing
                 if snr >= self.SNR_THRESHOLD_GOOD: snr_attr = self.COLOR_GREEN
                 elif snr >= self.SNR_THRESHOLD_BAD: snr_attr = self.COLOR_YELLOW
                 else: snr_attr = self.COLOR_RED
            # Format SNR value (handle None)
            snr_str = f"{snr if snr is not None else '-':>{col_widths[2]}}"

            # Format other fields (handle None)
            prn_str = f"{prn:>{col_widths[0]}}"
            sys_str = f"{sys_short:<{col_widths[1]}}"
            el_str = f"{elev if elev is not None else '-':>{col_widths[3]}}"
            az_str = f"{azim if azim is not None else '-':>{col_widths[4]}}"
            use_str = f"{'[*]':<{col_widths[5]}}" if active else f"{'[ ]':<{col_widths[5]}}"
            use_attr = self.ATTR_BOLD if active else self.COLOR_NORMAL

            # Draw line elements carefully
            if header_drawn:
                line_content = header_fmt.format(prn_str, sys_str, snr_str, el_str, az_str, use_str)
                current_x = x
                # Draw segment by segment with attributes
                self._safe_addstr(win, y, current_x, prn_str)
                current_x += col_widths[0] + col_spacing
                self._safe_addstr(win, y, current_x, sys_str, sys_attr)
                current_x += col_widths[1] + col_spacing
                self._safe_addstr(win, y, current_x, snr_str, snr_attr)
                current_x += col_widths[2] + col_spacing
                self._safe_addstr(win, y, current_x, el_str)
                current_x += col_widths[3] + col_spacing
                self._safe_addstr(win, y, current_x, az_str)
                current_x += col_widths[4] + col_spacing
                self._safe_addstr(win, y, current_x, use_str, use_attr)

            elif max_x > x + col_widths[0] + col_widths[2] + 2: # Simplified display if narrow
                self._safe_addstr(win, y, x, f"{prn_str}", self.COLOR_LABEL)
                self._safe_addstr(win, y, x + col_widths[0]+1, snr_str, snr_attr)

            y += 1 # Move to next line

        win.noutrefresh()

    def _draw_msg_panel(self, win: Optional[curses.window], state):
        """Draws message log panel."""
        if not win: return
        win.erase()
        self._safe_call(win.border)

        max_y, max_x = win.getmaxyx()
        y, x = 1, 2
        title = "[Messages]"

        # Draw title safely
        self._safe_addstr(win, 0, x, title, self.ATTR_BOLD | self.COLOR_YELLOW)

        messages = state.get('ui_log_messages', deque())
        total_messages = len(messages)

        # Calculate available lines for messages (inside borders)
        num_msg_lines = max(0, max_y - 2) # Space for top/bottom border

        # Determine which messages to show (newest at the bottom)
        start_index = max(0, total_messages - num_msg_lines)
        num_hidden_lines = start_index

        # Indicator for older messages
        if num_hidden_lines > 0:
             indicator = f"[+{num_hidden_lines}]"
             indicator_x = max(x + len(title) + 1, max_x - len(indicator) - 2)
             self._safe_addstr(win, 0, indicator_x, indicator, self.ATTR_BOLD)

        # Available width for each message line content
        available_width = max(1, max_x - x - 1) # Leave 1 char padding on right

        # Draw messages from determined start index
        line_num = 0
        for i in range(start_index, total_messages):
            if line_num >= num_msg_lines: break # Should not happen with calculation, but safety check

            msg_line = messages[i]
            display_y = y + line_num # Y position for this message

            # Truncate the entire message line if needed
            truncated_msg = msg_line[:available_width]

            # Determine color based on content (simple check)
            msg_attr = self.COLOR_NORMAL
            lmsg = msg_line.lower()
            if any(err in lmsg for err in ["error", "failed", "fatal", "critical", "timeout", "gave up", "issue"]):
                msg_attr = self.COLOR_RED
            elif any(wrn in lmsg for wrn in ["warn", "reconnecting", "retry", "timeout"]):
                msg_attr = self.COLOR_YELLOW
            elif any(ok in lmsg for ok in ["connect", "success", "fixed", "sent", "start", "run", "ack", "config", "serial"]):
                msg_attr = self.COLOR_GREEN

            self._safe_addstr(win, display_y, x, truncated_msg, msg_attr)
            line_num += 1

        win.noutrefresh()


    def update_display(self, stdscr: curses.window):
        """Main display update method called in the loop."""
        try:
            # --- Initialization / First Draw ---
            if self._first_draw:
                self._logger.debug("Performing first draw setup...")
                self._setup_curses(stdscr) # Basic curses setup
                if not self._create_windows(): # Create panels
                    self._logger.error("Window creation failed during first draw.")
                    # Attempt to show error message? main_curses might handle exit
                    return # Avoid further drawing attempts if panels failed
                self._draw_borders()
                self._stdscr.refresh() # Initial screen refresh
                self._first_draw = False
                self._needs_redraw = False # Reset redraw flag
                self._logger.debug("Initial draw complete.")
                return # Don't draw panels yet, wait for next update

            # --- Check for Resize ---
            if self._stdscr: # Ensure stdscr is valid
                 current_y, current_x = self._stdscr.getmaxyx()
                 last_size = self._last_terminal_size or (0, 0)
                 if (current_y, current_x) != last_size:
                      self._logger.info(f"Terminal resized: {last_size[0]}x{last_size[1]} -> {current_y}x{current_x}")
                      # curses.update_lines_cols() # May not be needed/available
                      curses.resizeterm(current_y, current_x) # Resize internal structures
                      self._needs_redraw = True
                      self._last_terminal_size = (current_y, current_x)
                      self._state.add_ui_log_message("Terminal resized.") # Log resize to UI

            # --- Handle Redraw Request (e.g., after resize) ---
            if self._needs_redraw:
                self._logger.debug("Handling redraw request...")
                if not self._create_windows(): # Recreate windows
                     self._logger.error("Window creation failed during redraw.")
                     # Maybe attempt to display error message?
                     return # Stop update if panels failed
                self._draw_borders()
                self._needs_redraw = False
                # Panels were marked clearok in _create_windows

            # --- Draw Panels ---
            state = self._state.get_state_snapshot() # Get latest state

            # Draw each panel safely
            self._draw_header(self._panels.get("header"), state)
            self._draw_info_panel(self._panels.get("info"), state)
            self._draw_sat_panel(self._panels.get("sat"), state)
            self._draw_msg_panel(self._panels.get("msg"), state)

            # --- Refresh Screen ---
            # Use doupdate() for potentially smoother updates if panels use noutrefresh()
            curses.doupdate()
            # Or use stdscr.refresh() if panels use .refresh() directly (less optimal)
            # self._stdscr.refresh()

        except curses.error as e:
            # Handle common curses errors during update
            self._logger.error(f"Curses error during display update: {e}. Triggering redraw.")
            # Trigger redraw to try and recover the display
            self.trigger_redraw()
        except Exception as e:
            # Catch any unexpected errors during the update process
            self._logger.error(f"Unexpected error during display update: {e}", exc_info=True)
            # Trigger redraw as a potential recovery mechanism
            self.trigger_redraw()

    def trigger_redraw(self):
        """Flags that a full redraw is needed."""
        if not self._needs_redraw:
            self._logger.info("Redraw triggered.")
            self._needs_redraw = True
        # Force clearing of panels on next redraw
        for panel in self._panels.values():
             if panel: panel.clearok(True)

    def close(self):
        """Cleanup curses (usually handled by wrapper)."""
        # This might not be strictly necessary if curses.wrapper is used,
        # but provides an explicit cleanup point if needed.
        if self._stdscr and not self._stdscr.isendwin():
             try:
                  curses.nocbreak()
                  self._stdscr.keypad(False)
                  curses.echo()
                  curses.endwin()
                  self._logger.info("Curses environment closed by StatusDisplay.")
             except Exception as e:
                  self._logger.error(f"Error during StatusDisplay curses cleanup: {e}")
