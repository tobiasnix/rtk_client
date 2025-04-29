# status_display.py - Handles the curses-based status display

import curses
import time
import logging
from datetime import datetime, timezone
# --- Import Counter and deque ---
from collections import deque, Counter
from typing import Optional, Dict, Any
from rtk_state import GnssState
from rtk_config import Config # Only needed for display config? Maybe pass directly
from rtk_constants import * # Import constants

# --- Removed extra file loading log ---
file_logger = logging.getLogger(__name__)
# file_logger.critical(f"--- Loading status_display.py ---") # Removed

class StatusDisplay:
    """Formats and prints the system status using curses panels."""
    def __init__(self, state: GnssState, config: Config):
        # --- Removed extra __init__ log ---
        init_logger = logging.getLogger(self.__class__.__name__)
        # init_logger.critical(f"--- StatusDisplay __init__ executing from THIS file ---") # Removed
        # --- End __init__ logging ---
        self._state = state
        self._config = config
        self._logger = init_logger # Use the same logger
        self._stdscr = None
        self._panels: Dict[str, curses.window] = {}
        self._needs_redraw = True

        # Layout definition (consider making more dynamic or configurable)
        self._layout = {
            "header": {"y": 0, "x": 0, "h": 3, "w": 0}, # w=0 means use max_x
            "info":   {"y": 3, "x": 0, "h": 0, "w": 0}, # h=0, w=0 means calculate dynamically
            "sat":    {"y": 3, "x": 0, "h": 0, "w": 0},
            "msg":    {"y": 0, "x": 0, "h": 5, "w": 0}  # h=5 lines for messages
        }

        # Curses attributes (initialized properly in _setup_curses)
        self.COLOR_GREEN = curses.A_NORMAL
        self.COLOR_YELLOW = curses.A_NORMAL
        self.COLOR_RED = curses.A_NORMAL
        self.COLOR_LABEL = curses.A_NORMAL
        self.COLOR_VALUE = curses.A_NORMAL
        self.COLOR_NORMAL = curses.A_NORMAL
        self.ATTR_BOLD = curses.A_BOLD
        self.COLOR_SAT_GPS = curses.A_NORMAL
        self.COLOR_SAT_GLO = curses.A_NORMAL
        self.COLOR_SAT_GAL = curses.A_NORMAL
        self.COLOR_SAT_BDS = curses.A_NORMAL
        self.COLOR_SAT_QZS = curses.A_NORMAL
        self.COLOR_SAT_OTH = curses.A_NORMAL

    def _setup_curses(self, stdscr):
        """Initial curses setup. Called once by update_display."""
        self._stdscr = stdscr
        curses.curs_set(0) # Hide cursor
        stdscr.nodelay(True) # Make getch non-blocking
        # Set a timeout for getch() - controls loop responsiveness
        # Reduced timeout for better responsiveness if needed, e.g. 100ms
        stdscr.timeout(int(STATUS_UPDATE_INTERVAL * 100)) # e.g., 100ms timeout

        # Initialize colors if available
        if curses.has_colors():
            try:
                curses.start_color()
                # Use default background (-1)
                curses.use_default_colors()
                # Define color pairs (foreground, background)
                curses.init_pair(1, curses.COLOR_GREEN, -1)
                curses.init_pair(2, curses.COLOR_YELLOW, -1)
                curses.init_pair(3, curses.COLOR_RED, -1)
                curses.init_pair(4, curses.COLOR_CYAN, -1)
                curses.init_pair(5, curses.COLOR_WHITE, -1)
                curses.init_pair(6, curses.COLOR_BLUE, -1)

                # Assign color pairs and attributes
                self.COLOR_GREEN = curses.color_pair(1) | curses.A_BOLD
                self.COLOR_YELLOW = curses.color_pair(2) | curses.A_BOLD
                self.COLOR_RED = curses.color_pair(3) | curses.A_BOLD
                self.COLOR_LABEL = curses.color_pair(4)
                self.COLOR_VALUE = curses.color_pair(5)
                self.COLOR_SAT_GPS = curses.color_pair(1)
                self.COLOR_SAT_GLO = curses.color_pair(2)
                self.COLOR_SAT_GAL = curses.color_pair(6) | curses.A_BOLD
                self.COLOR_SAT_BDS = curses.color_pair(3)
                self.COLOR_SAT_QZS = curses.color_pair(4)
                self.COLOR_SAT_OTH = curses.A_DIM # Dim for other/unknown systems
            except curses.error as e:
                 self._logger.error(f"Curses color setup failed: {e}. Continuing without colors.")
                 curses.has_colors = lambda: False # Force fallback logic
                 # Fallback assignments (repeated for clarity)
                 self.COLOR_GREEN = curses.A_BOLD
                 self.COLOR_YELLOW = curses.A_BOLD
                 self.COLOR_RED = curses.A_BOLD
                 self.COLOR_LABEL = curses.A_NORMAL
                 self.COLOR_VALUE = curses.A_BOLD
                 self.COLOR_SAT_GPS = curses.A_NORMAL
                 self.COLOR_SAT_GLO = curses.A_NORMAL
                 self.COLOR_SAT_GAL = curses.A_NORMAL
                 self.COLOR_SAT_BDS = curses.A_NORMAL
                 self.COLOR_SAT_QZS = curses.A_NORMAL
                 self.COLOR_SAT_OTH = curses.A_DIM

        else:
            # Fallback for terminals without color
            self._logger.warning("Terminal does not support colors.")
            self.COLOR_GREEN = curses.A_BOLD
            self.COLOR_YELLOW = curses.A_BOLD
            self.COLOR_RED = curses.A_BOLD
            self.COLOR_LABEL = curses.A_NORMAL
            self.COLOR_VALUE = curses.A_BOLD # Use bold for values if no color
            # No specific satellite colors without curses colors
            self.COLOR_SAT_GPS = curses.A_NORMAL
            self.COLOR_SAT_GLO = curses.A_NORMAL
            self.COLOR_SAT_GAL = curses.A_NORMAL
            self.COLOR_SAT_BDS = curses.A_NORMAL
            self.COLOR_SAT_QZS = curses.A_NORMAL
            self.COLOR_SAT_OTH = curses.A_DIM

        self.ATTR_BOLD = curses.A_BOLD
        self.COLOR_NORMAL = curses.A_NORMAL
        self._logger.debug("Curses setup complete.")

    def _create_windows(self):
        """Create curses windows based on layout and current terminal size."""
        max_y, max_x = self._stdscr.getmaxyx()
        self._panels = {} # Clear existing panels

        # Terminal size check
        min_h, min_w = 20, 80 # Minimum recommended size
        if max_y < min_h or max_x < min_w:
             # Log error but try to continue - drawing might fail partially
             self._logger.error(f"Terminal too small ({max_y}x{max_x})! Minimum {min_h}x{min_w} recommended.")
             # Optional: Raise an exception here if minimum size is critical
             # raise curses.error(f"Terminal too small! Minimum {min_h}x{min_w} required.")

        # Calculate panel dimensions dynamically
        header_height = self._layout["header"]["h"]
        msg_panel_height = self._layout["msg"]["h"]
        main_panel_height = max(1, max_y - header_height - msg_panel_height) # Ensure at least 1 row

        # Split main area width (e.g., 50/50)
        info_panel_width = max(1, max_x // 2)
        sat_panel_width = max(1, max_x - info_panel_width)

        # Define windows using derwin (relative to stdscr)
        # Format: nlines, ncols, begin_y, begin_x
        try:
            self._panels["header"] = self._stdscr.derwin(header_height, max_x, 0, 0)
            self._panels["info"] = self._stdscr.derwin(main_panel_height, info_panel_width, header_height, 0)
            self._panels["sat"] = self._stdscr.derwin(main_panel_height, sat_panel_width, header_height, info_panel_width)
            # Place message panel at the bottom
            msg_y = max(0, max_y - msg_panel_height)
            self._panels["msg"] = self._stdscr.derwin(msg_panel_height, max_x, msg_y, 0)
            self._logger.debug("Curses windows created/resized.")
        except curses.error as e:
             # This can happen if dimensions are invalid (e.g., zero or negative)
             self._logger.error(f"Error creating curses windows: {e}. Check layout and terminal size.")
             # Clear panels to prevent drawing errors later
             self._panels = {}
        except Exception as e:
             self._logger.error(f"Unexpected error creating curses windows: {e}", exc_info=True)
             self._panels = {}

        self._needs_redraw = True # Flag that content needs drawing

    def _draw_borders(self):
        """Draw borders and separators for the panels."""
        # Draw borders for main panels
        for name in ["info", "sat", "msg"]:
            if name in self._panels:
                 try:
                      self._panels[name].border()
                 except curses.error as e:
                      # Ignore errors if panel is too small for border
                      self._logger.debug(f"Could not draw border for {name}: {e}")
                 except Exception as e:
                      self._logger.error(f"Error drawing border for {name}: {e}", exc_info=True)


        # Draw vertical separator between info and sat panels
        if "info" in self._panels and "sat" in self._panels:
            max_y, max_x = self._stdscr.getmaxyx() # Use stdscr size
            info_h, info_w = self._panels["info"].getmaxyx() # Get actual info panel width
            sep_x = info_w # Separator is at the right edge of info panel
            start_y = self._layout["header"]["h"] # Start below header
            end_y = max(0, max_y - self._layout["msg"]["h"]) # End above message panel border

            for y in range(start_y, end_y):
                 # Check boundaries carefully
                 if 0 <= y < max_y and 0 <= sep_x < max_x:
                    try:
                        # Choose appropriate character for T-junctions
                        if y == start_y:
                            char = curses.ACS_TTEE # Top junction
                        else:
                            char = curses.ACS_VLINE # Vertical line

                        # Use insch to avoid moving cursor
                        self._stdscr.insch(y, sep_x, char)

                    except curses.error as e:
                        # Ignore errors drawing separator, esp. at edges
                         self._logger.debug(f"Error drawing separator at ({y},{sep_x}): {e}")
                    except Exception as e:
                         self._logger.error(f"Error drawing separator: {e}", exc_info=True)

            # Draw bottom T junction for separator explicitly if space allows
            bottom_t_y = end_y -1 # The line *inside* the msg panel's top border
            if start_y <= bottom_t_y < max_y and 0 <= sep_x < max_x:
                  try:
                       self._stdscr.insch(bottom_t_y, sep_x, curses.ACS_BTEE)
                  except curses.error as e:
                       self._logger.debug(f"Error drawing bottom T separator at ({bottom_t_y},{sep_x}): {e}")
                  except Exception as e:
                       self._logger.error(f"Error drawing bottom T separator: {e}", exc_info=True)

    def _draw_header(self, win, state):
        """Draw the header panel content."""
        if not win: return
        win.erase() # Clear previous content
        max_y, max_x = win.getmaxyx() # Get window dimensions

        # Check if window is large enough before drawing
        if max_y < 3 or max_x < 10:
            self._logger.debug("Header window too small to draw.")
            return

        try:
            # Draw top/bottom lines
            win.hline(0, 0, curses.ACS_HLINE, max_x)
            win.hline(max_y - 1, 0, curses.ACS_HLINE, max_x) # Use hline for bottom too

            # Construct title and center it
            title = f" LC29HDA RTK Status - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} "
            title_x = max(0, (max_x - len(title)) // 2)
            win.addstr(1, title_x, title, self.ATTR_BOLD)

        except curses.error as e:
            # Catch errors drawing to window edges
            self._logger.debug(f"Curses error drawing header: {e}")
        except Exception as e:
             self._logger.error(f"Error drawing header: {e}", exc_info=True)

        win.noutrefresh() # Mark changes for doupdate()


    def _draw_info_panel(self, win, state):
        """Draw GNSS and NTRIP info panel content."""
        if not win: return
        win.erase()
        # Draw border manually within try-except if .border() causes issues
        try:
            win.border()
        except curses.error as e:
            self._logger.debug(f"Could not draw info panel border: {e}")
        except Exception as e:
            self._logger.error(f"Error drawing info panel border: {e}", exc_info=True)


        max_y, max_x = win.getmaxyx()
        y = 1  # Start drawing below border
        x = 2  # Indent content from border
        label_width = 22 # Width for labels like "Runtime"

        # Helper function to draw a labeled line safely
        def draw_line(label, value, attr=self.COLOR_VALUE):
            nonlocal y
            # Check if there's space to draw this line
            if y >= max_y - 1: # Stop if we reach the bottom border
                return y # Return current y without incrementing

            label_str = f"{label:<{label_width}}:"
            try:
                # Draw label
                win.addstr(y, x, label_str, self.COLOR_LABEL)

                # Prepare and truncate value string
                value_str = str(value)
                available_width = max_x - x - len(label_str) - 1 -1 # space, border
                if available_width < 1: # Not enough space for value
                     truncated_value = ""
                else:
                     truncated_value = value_str[:available_width]

                # Draw value
                win.addstr(y, x + len(label_str) + 1, truncated_value, attr)

            except curses.error as e:
                # Ignore errors if drawing goes out of bounds (should be prevented by checks)
                self._logger.debug(f"Curses error drawing line '{label}': {e}")
            except Exception as e:
                self._logger.error(f"Error drawing line '{label}': {e}", exc_info=True)

            y += 1 # Move to next line
            return y

        # --- Section Titles ---
        def draw_section_title(title):
            nonlocal y
            if y >= max_y - 1: return y
            try:
                 win.addstr(y, x, title, self.ATTR_BOLD)
            except curses.error as e: self._logger.debug(f"Curses error drawing title '{title}': {e}")
            except Exception as e: self._logger.error(f"Error drawing title '{title}': {e}", exc_info=True)
            y += 1
            return y

        # --- GNSS Info ---
        y = draw_section_title("[GNSS Info]")
        runtime = datetime.now(timezone.utc) - state.get('start_time', datetime.now(timezone.utc))
        y = draw_line("Runtime", str(runtime).split('.')[0]) # Display HH:MM:SS
        y = draw_line("Firmware", state.get('firmware_version', 'N/A'))
        # Safely access position data
        pos = state.get('position', {})
        y = draw_line("Latitude", f"{pos.get('lat', 0.0):.8f}\N{DEGREE SIGN}")
        y = draw_line("Longitude", f"{pos.get('lon', 0.0):.8f}\N{DEGREE SIGN}")
        y = draw_line("Altitude", f"{pos.get('alt', 0.0):.3f} m")

        # Fix Age
        last_fix_time = state.get('last_fix_time')
        if last_fix_time:
            fix_age = (datetime.now(timezone.utc) - last_fix_time).total_seconds()
            age_attr = self.COLOR_YELLOW if fix_age > 10 else self.COLOR_VALUE
            y = draw_line("Fix Age", f"{fix_age:.1f} sec", attr=age_attr)
        else:
            y = draw_line("Fix Age", "N/A")

        # TTFF
        ttff = state.get('first_fix_time_sec')
        y = draw_line("TTFF", f"{ttff:.1f} sec" if ttff is not None else "Pending...")

        # RTK Status (colored)
        rtk_status = state.get('rtk_status', "Unknown")
        rtk_attr = self.ATTR_BOLD # Always bold
        if rtk_status == "RTK Fixed": rtk_attr |= self.COLOR_GREEN
        elif rtk_status == "RTK Float": rtk_attr |= self.COLOR_YELLOW
        elif rtk_status in ["No Fix / Invalid", "Unknown"]: rtk_attr |= self.COLOR_RED
        else: rtk_attr |= self.COLOR_VALUE # Other statuses like GPS, DGPS
        y = draw_line("RTK Status", rtk_status, attr=rtk_attr)
        y = draw_line("Fix Type Code", state.get('fix_type', 0)) # Raw fix type number
        y = draw_line("Sats Used / View", f"{state.get('num_satellites_used', 0)} / {state.get('num_satellites_in_view', 0)}")
        y = draw_line("HDOP", f"{state.get('hdop', DEFAULT_HDOP):.2f}")

        # --- Satellite Systems in View ---
        # *** This is the line causing the NameError ***
        # Use Counter (imported from collections) as the default factory
        systems = state.get('satellite_systems', Counter()) # Fixed: Use imported Counter
        # --- End Fix ---
        systems_str = ", ".join(f"{sys}:{c}" for sys, c in sorted(systems.items())) if systems else "N/A"
        y = draw_line("Systems View", systems_str)

        # --- NTRIP Info ---
        y += 1 # Add a blank line
        y = draw_section_title("[NTRIP Info]")
        # Display config values safely
        ntrip_host = f"{getattr(self._config, 'ntrip_server', 'N/A')}:{getattr(self._config, 'ntrip_port', 'N/A')}"
        y = draw_line("Server", ntrip_host)
        y = draw_line("Mountpoint", getattr(self._config, 'ntrip_mountpoint', 'N/A'))

        # Connection Status (colored)
        ntrip_conn = state.get('ntrip_connected', False)
        ntrip_msg = state.get('ntrip_status_message', 'Unknown')
        ntrip_conn_status = 'Connected' if ntrip_conn else 'Disconnected'
        ntrip_attr = self.COLOR_GREEN if ntrip_conn else self.COLOR_RED
        y = draw_line("Status", f"{ntrip_conn_status} - {ntrip_msg}", attr=ntrip_attr)

        # RTCM Age
        last_data_time = state.get('ntrip_last_data_time')
        if last_data_time:
            rtcm_age = (datetime.now(timezone.utc) - last_data_time).total_seconds()
            rtcm_age_attr = self.COLOR_RED if rtcm_age > NTRIP_DATA_TIMEOUT else self.COLOR_VALUE
            y = draw_line("RTCM Age", f"{rtcm_age:.1f} sec", attr=rtcm_age_attr)
        else:
            y = draw_line("RTCM Age", "N/A")

        # RTCM Rate (Average over deque)
        rates_deque = state.get('ntrip_data_rates', deque())
        avg_rate_bps = sum(rates_deque) / len(rates_deque) if rates_deque else 0
        y = draw_line("RTCM Rate (avg)", f"{avg_rate_bps:.1f} B/s") # Indicate it's an average
        y = draw_line("Total RTCM Bytes", f"{state.get('ntrip_total_bytes', 0):,}") # Formatted with commas
        y = draw_line("Reconnects", state.get('ntrip_reconnect_attempts', 0))

        # Last RTCM Types Received
        rtcm_types_list = list(state.get('last_rtcm_message_types', deque()))
        # Show last 5 types nicely formatted
        types_str = ('[' + ', '.join(map(str, rtcm_types_list[-5:])) + ']' +
                    ('...' if len(rtcm_types_list)>5 else '')) if rtcm_types_list else 'None'
        y = draw_line("Last RTCM Types", types_str)

        win.noutrefresh() # Mark changes for doupdate()

    def _draw_sat_panel(self, win, state):
        """Draw satellite details panel content."""
        if not win: return
        win.erase()
        try:
            win.border()
        except curses.error as e: self._logger.debug(f"Could not draw sat panel border: {e}")
        except Exception as e: self._logger.error(f"Error drawing sat panel border: {e}", exc_info=True)

        max_y, max_x = win.getmaxyx()
        y = 1; x = 2

        # Draw Title
        if y < max_y -1:
            try: win.addstr(y, x, "[Satellites in View]", self.ATTR_BOLD); y += 1
            except curses.error as e: self._logger.debug(f"Curses error drawing sat title: {e}")
            except Exception as e: self._logger.error(f"Error drawing sat title: {e}", exc_info=True)


        # Define table header and column widths
        header = f"{'PRN':>3} {'Sys':<5} {'SNR':>3} {'El':>3} {'Az':>3} {'Use':<3}"
        col_widths = [3, 5, 3, 3, 3, 3]
        col_spacing = 1
        total_width = sum(col_widths) + col_spacing * (len(col_widths) - 1)

        # Draw header and separator if space allows
        if y < max_y - 1 and max_x > x + total_width:
            try:
                 win.addstr(y, x, header, self.ATTR_BOLD); y += 1
                 if y < max_y - 1: # Check space for separator line
                      win.addstr(y, x, "-" * total_width); y += 1
            except curses.error as e: self._logger.debug(f"Curses error drawing sat header: {e}")
            except Exception as e: self._logger.error(f"Error drawing sat header: {e}", exc_info=True)

        elif max_x <= x + total_width and y < max_y -1 : # Not enough width
             try: win.addstr(y, x, "Too narrow for Sat Table", self.COLOR_YELLOW); y+=1
             except curses.error: pass # Ignore if drawing error message fails
             except Exception as e: self._logger.error(f"Error drawing narrow msg: {e}", exc_info=True)


        # Get satellite data, sort by system then PRN
        satellites_info = state.get('satellites_info', {})
        # Ensure PRN is treated as integer for sorting
        def sort_key(item):
            key, sat_data = item
            try:
                 prn_int = int(sat_data.get('prn', 999))
            except (ValueError, TypeError):
                 prn_int = 999 # Place unparseable PRNs last
            return (sat_data.get('system', 'zzz'), prn_int)

        sorted_sats = sorted(satellites_info.items(), key=sort_key)

        # Draw satellite rows
        for _, sat_info in sorted_sats:
            if y >= max_y - 1: break # Stop if panel is full

            # Extract data safely
            prn = sat_info.get('prn', '??')
            system = sat_info.get('system', 'UNK')
            snr = sat_info.get('snr', 0)
            elev = sat_info.get('elevation', None)
            azim = sat_info.get('azimuth', None)
            active = sat_info.get('active', False)

            # Determine color/attribute based on system and SNR
            sys_attr = self.COLOR_NORMAL
            sys_short = system[:3].upper() # Abbreviate system name
            if system == "GPS": sys_attr = self.COLOR_SAT_GPS; sys_short="GPS"
            elif system == "GLONASS": sys_attr = self.COLOR_SAT_GLO; sys_short="GLO"
            elif system == "Galileo": sys_attr = self.COLOR_SAT_GAL; sys_short="GAL"
            elif system == "BeiDou": sys_attr = self.COLOR_SAT_BDS; sys_short="BDS"
            elif system == "QZSS": sys_attr = self.COLOR_SAT_QZS; sys_short="QZS"
            elif system == "NavIC": sys_attr = self.COLOR_SAT_OTH; sys_short="NAV"
            # Add more system mappings if needed

            snr_attr = self.COLOR_NORMAL | curses.A_DIM # Default dim for SNR 0
            # Use constants for thresholds if defined
            snr_good = getattr(self, 'SNR_THRESHOLD_GOOD', 35)
            snr_bad = getattr(self, 'SNR_THRESHOLD_BAD', 20)
            if snr >= snr_good: snr_attr = self.COLOR_GREEN
            elif snr >= snr_bad: snr_attr = self.COLOR_YELLOW
            elif snr > 0: snr_attr = self.COLOR_RED # Low SNR in red

            # Format strings for each column
            prn_str = f"{prn:>{col_widths[0]}}"
            sys_str = f"{sys_short:<{col_widths[1]}}"
            snr_str = f"{snr:>{col_widths[2]}}" if snr else f"{'-':>{col_widths[2]}}"
            el_str = f"{elev:>{col_widths[3]}}" if elev is not None else f"{'-':>{col_widths[3]}}"
            az_str = f"{azim:>{col_widths[4]}}" if azim is not None else f"{'-':>{col_widths[4]}}"
            use_str = f"{'[*]':<{col_widths[5]}}" if active else f"{'[ ]':<{col_widths[5]}}"

            # Draw the row column by column
            current_x = x
            try:
                # Check width before attempting to draw full line
                if max_x > x + total_width:
                     win.addstr(y, current_x, prn_str)
                     current_x += col_widths[0] + col_spacing
                     win.addstr(y, current_x, sys_str, sys_attr)
                     current_x += col_widths[1] + col_spacing
                     win.addstr(y, current_x, snr_str, snr_attr)
                     current_x += col_widths[2] + col_spacing
                     win.addstr(y, current_x, el_str)
                     current_x += col_widths[3] + col_spacing
                     win.addstr(y, current_x, az_str)
                     current_x += col_widths[4] + col_spacing
                     # Make 'Use' bold if active
                     use_attr = self.ATTR_BOLD if active else self.COLOR_NORMAL
                     win.addstr(y, current_x, use_str, use_attr)
                else:
                    # If too narrow, maybe just print PRN and SNR?
                    # Or skip drawing the row if header wasn't drawn.
                    if max_x > x + col_widths[0] + col_spacing + col_widths[2]:
                        win.addstr(y, x, f"{prn_str} {snr_str}")
                    # else: skip drawing row completely if too narrow

                y += 1 # Move to next line only if drawing was attempted

            except curses.error as e:
                self._logger.debug(f"Curses error drawing sat row {prn}: {e}")
                break # Stop drawing rows if an error occurs
            except Exception as e:
                self._logger.error(f"Error drawing sat row {prn}: {e}", exc_info=True)
                break


        win.noutrefresh() # Mark changes for doupdate()

    def _draw_msg_panel(self, win, state):
        """Draw the message log panel content."""
        if not win: return
        win.erase()
        try:
            win.border()
        except curses.error as e: self._logger.debug(f"Could not draw msg panel border: {e}")
        except Exception as e: self._logger.error(f"Error drawing msg panel border: {e}", exc_info=True)

        max_y, max_x = win.getmaxyx()
        y = 1; x = 2 # Start position inside border

        # Draw Title on top border line (optional)
        try:
            win.addstr(0, x, "[Messages]", self.ATTR_BOLD)
        except curses.error: pass # Ignore if title doesn't fit
        except Exception as e: self._logger.error(f"Error drawing msg title: {e}", exc_info=True)


        # Get messages from state (it's a deque)
        messages = state.get('ui_log_messages', deque())
        num_msg_lines = max(0, max_y - 2) # Available lines for messages

        # Calculate start index to show only the last `num_msg_lines` messages
        start_index = max(0, len(messages) - num_msg_lines)
        line_num = 0 # Relative line number within the panel

        # Draw messages from calculated start index
        for i in range(start_index, len(messages)):
            msg = messages[i]
            display_line = y + line_num # Absolute y coordinate in the window

            if display_line >= max_y - 1: break # Stop if panel is full

            # Truncate message to fit width
            truncated_msg = msg[:max_x - x - 1] # Leave space for border

            # Determine message color based on content (simple keyword check)
            msg_attr = self.COLOR_NORMAL
            lmsg = msg.lower() # Case-insensitive check
            # Prioritize error > warning > success
            if "error" in lmsg or "failed" in lmsg or "fatal" in lmsg or "critical" in lmsg:
                 msg_attr = self.COLOR_RED
            elif "warning" in lmsg or "reconnecting" in lmsg or "timeout" in lmsg:
                 msg_attr = self.COLOR_YELLOW
            elif "connected" in lmsg or "success" in lmsg or "fixed" in lmsg or "sent" in lmsg or "starting" in lmsg or "running" in lmsg:
                 msg_attr = self.COLOR_GREEN

            # Draw the message line
            try:
                win.addstr(display_line, x, truncated_msg, msg_attr)
            except curses.error as e:
                self._logger.debug(f"Curses error drawing message: {e}")
                break # Stop drawing messages if error occurs
            except Exception as e:
                self._logger.error(f"Error drawing message: {e}", exc_info=True)
                break

            line_num += 1 # Move to next line in the panel

        win.noutrefresh() # Mark changes for doupdate()

    # --- Removed logging marker for method definition ---
    # file_logger.critical(f"--- Defining update_display method in StatusDisplay ---")
    # --- End method definition logging ---

    def update_display(self, stdscr):
        """Main display update called periodically."""
        # Setup curses on first call
        if self._stdscr is None:
             self._setup_curses(stdscr)

        # Get latest state snapshot
        state = self._state.get_state_snapshot()

        try:
            # Check if redraw is needed (e.g., after resize or init)
            if self._needs_redraw:
                 self._stdscr.clear() # Clear physical screen immediately
                 # Recreate windows based on potentially new size
                 self._create_windows()
                 # Draw static borders after creating windows
                 self._draw_borders()
                 # Reset redraw flag AFTER drawing static elements
                 self._needs_redraw = False
                 # Refresh immediately to show new layout/borders
                 self._stdscr.refresh()


            # Draw dynamic panel content to window buffers (noutrefresh used inside)
            # Check if panels exist before drawing (in case _create_windows failed)
            if "header" in self._panels: self._draw_header(self._panels["header"], state)
            if "info" in self._panels: self._draw_info_panel(self._panels["info"], state)
            if "sat" in self._panels: self._draw_sat_panel(self._panels["sat"], state)
            if "msg" in self._panels: self._draw_msg_panel(self._panels["msg"], state)

            # Refresh the physical screen once with all buffered changes
            curses.doupdate()

        except curses.error as e:
            # Handle errors during the update process
            self._logger.error(f"Curses error during display update: {e}. Terminal might be too small or closed.")
            # Trigger a full redraw on next cycle to try and recover
            self.trigger_redraw()
        except Exception as e:
             self._logger.error(f"Unexpected error during display update: {e}", exc_info=True)
             self.trigger_redraw() # Trigger redraw on unexpected errors too


    def trigger_redraw(self):
        """Flags that a full redraw is needed (e.g., after resize)."""
        self._logger.debug("Redraw triggered.")
        self._needs_redraw = True
