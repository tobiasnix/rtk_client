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

file_logger = logging.getLogger(__name__)

class StatusDisplay:
    """Handles the curses-based status display with improved error handling and modularity."""
    
    # Panel layout constants
    HEADER_HEIGHT = 3
    MESSAGE_PANEL_HEIGHT = 5
    MIN_TERMINAL_HEIGHT = 20
    MIN_TERMINAL_WIDTH = 80
    
    # Color definitions and thresholds
    SNR_THRESHOLD_GOOD = 35
    SNR_THRESHOLD_BAD = 20
    
    def __init__(self, state: GnssState, config: Config):
        self._state = state
        self._config = config
        self._logger = logging.getLogger(self.__class__.__name__)
        self._stdscr = None
        self._panels: Dict[str, curses.window] = {}
        self._needs_redraw = True
        self._first_draw = True

        # Layout definition
        self._layout = {
            "header": {"y": 0, "x": 0, "h": self.HEADER_HEIGHT, "w": 0},
            "info":   {"y": self.HEADER_HEIGHT, "x": 0, "h": 0, "w": 0},
            "sat":    {"y": self.HEADER_HEIGHT, "x": 0, "h": 0, "w": 0},
            "msg":    {"y": 0, "x": 0, "h": self.MESSAGE_PANEL_HEIGHT, "w": 0}
        }

        # Initialize color attributes (will be properly set in _setup_curses)
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
        """Sets up curses environment with proper error handling."""
        self._stdscr = stdscr
        try:
            # Basic curses setup
            curses.curs_set(0)  # Hide cursor
            stdscr.nodelay(True)  # Non-blocking input
            stdscr.timeout(1000)  # 1 second timeout
            
            # Initialize colors if terminal supports them
            if curses.has_colors():
                curses.start_color()
                if curses.can_change_color():
                    curses.use_default_colors()
                    
                # Define color pairs
                self._init_color_pairs()
            else:
                self._logger.warning("Terminal does not support colors.")
                self._assign_fallback_colors()
                
            # Set common attributes
            self.ATTR_BOLD = curses.A_BOLD
            self.COLOR_NORMAL = curses.A_NORMAL
            
            self._logger.debug("Curses setup complete.")
        except curses.error as e:
            self._logger.error(f"Curses setup failed: {e}.")
            self._assign_fallback_colors()
        except Exception as e:
            self._logger.error(f"Unexpected error during curses setup: {e}", exc_info=True)
            self._assign_fallback_colors()

    def _init_color_pairs(self):
        """Initialize color pairs for different display elements."""
        curses.init_pair(1, curses.COLOR_GREEN, -1)
        curses.init_pair(2, curses.COLOR_YELLOW, -1)
        curses.init_pair(3, curses.COLOR_RED, -1)
        curses.init_pair(4, curses.COLOR_CYAN, -1)
        curses.init_pair(5, curses.COLOR_WHITE, -1)
        curses.init_pair(6, curses.COLOR_BLUE, -1)
        
        # Set color attributes
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
        self.COLOR_SAT_OTH = curses.A_DIM

    def _assign_fallback_colors(self):
        """Assign fallback colors when color support is not available."""
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

    def _create_windows(self):
        """Creates the display windows based on terminal size with improved error handling."""
        if not self._stdscr:
            self._logger.error("Cannot create windows: stdscr not available.")
            return
            
        try:
            self._stdscr.clear()
            max_y, max_x = self._stdscr.getmaxyx()
            self._panels = {}
            
            # Check for minimum terminal size
            if max_y < self.MIN_TERMINAL_HEIGHT or max_x < self.MIN_TERMINAL_WIDTH:
                self._logger.warning(
                    f"Terminal potentially too small ({max_y}x{max_x}). "
                    f"Min {self.MIN_TERMINAL_HEIGHT}x{self.MIN_TERMINAL_WIDTH} recommended."
                )
                
            # Calculate panel dimensions
            header_h = self._layout["header"]["h"]
            msg_h = self._layout["msg"]["h"]
            main_h = max(1, max_y - header_h - msg_h)
            info_w = max(1, max_x // 2)
            sat_w = max(1, max_x - info_w)
            msg_y = max(0, max_y - msg_h)
            
            # Create the windows
            if header_h > 0 and max_x > 0:
                self._panels["header"] = self._stdscr.derwin(header_h, max_x, 0, 0)
                
            if main_h > 0 and info_w > 0:
                self._panels["info"] = self._stdscr.derwin(main_h, info_w, header_h, 0)
                
            if main_h > 0 and sat_w > 0:
                self._panels["sat"] = self._stdscr.derwin(main_h, sat_w, header_h, info_w)
                
            if msg_h > 0 and max_x > 0:
                self._panels["msg"] = self._stdscr.derwin(msg_h, max_x, msg_y, 0)
                
            self._logger.debug(
                f"Windows created/resized: M({msg_h}x{max_x} @{msg_y}) "
                f"Info({main_h}x{info_w}) Sat({main_h}x{sat_w})"
            )
            
            # Mark all panels for full redraw
            for panel in self._panels.values():
                panel.clearok(True)
                
        except curses.error as e:
            self._logger.error(f"Error creating curses windows: {e}.")
            self._panels = {}
        except Exception as e:
            self._logger.error(f"Unexpected error creating curses windows: {e}", exc_info=True)
            self._panels = {}

    def _draw_borders(self):
        """Draws borders around panels with improved error handling."""
        if not self._panels:
            return
            
        # Draw panel borders
        for name, panel in self._panels.items():
            if name == "header":
                continue
            self._safe_call(panel.border)
                
        # Draw separator between info and sat panels
        if "info" in self._panels and "sat" in self._panels:
            try:
                info_h, info_w = self._panels["info"].getmaxyx()
                sat_h, _ = self._panels["sat"].getmaxyx()
                sep_x = info_w
                start_y = self._layout["header"]["h"]
                sep_h = min(info_h, sat_h)
                end_y = start_y + sep_h - 1
                msg_panel_start_y = self._stdscr.getmaxyx()[0] - self._layout["msg"]["h"]
                
                for y in range(start_y, end_y):
                    if 0 <= y < self._stdscr.getmaxyx()[0] and 0 <= sep_x < self._stdscr.getmaxyx()[1]:
                        char = curses.ACS_VLINE
                        
                        if y == start_y:
                            char = curses.ACS_TTEE
                            
                        if y == msg_panel_start_y - 1:
                            char = curses.ACS_BTEE
                            
                        self._stdscr.insch(y, sep_x, char)
            except:
                # Ignore errors for border drawing
                pass

    def _safe_call(self, func: Callable, *args, **kwargs):
        """Safely calls a curses function, catching any errors."""
        try:
            return func(*args, **kwargs)
        except curses.error:
            pass
        except Exception as e:
            self._logger.error(f"Error in safe call to {func.__name__}: {e}", exc_info=True)
        return None

    def _safe_addstr(self, win, y: int, x: int, text: str, attr=curses.A_NORMAL):
        """Safely adds a string to a window, handling potential errors."""
        if not win:
            return False
            
        try:
            win.addstr(y, x, text, attr)
            return True
        except curses.error:
            # This is usually caused by writing at the bottom-right corner
            # which is generally harmless in our application
            return False
        except Exception as e:
            self._logger.error(f"Error adding string at ({y},{x}): {e}", exc_info=True)
            return False

    def _draw_header(self, win, state):
        """Draws the header panel with improved error handling."""
        if not win:
            return
            
        win.erase()
        max_y, max_x = win.getmaxyx()
        
        if max_y < 3 or max_x < 10:
            return
            
        # Draw horizontal lines
        self._safe_call(win.hline, 0, 0, curses.ACS_HLINE, max_x)
        self._safe_call(win.hline, max_y - 1, 0, curses.ACS_HLINE, max_x)
        
        # Draw title
        title = f" LC29HDA RTK Status - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} "
        title_x = max(0, (max_x - len(title)) // 2)
        self._safe_addstr(win, 1, title_x, title, self.ATTR_BOLD)

    def _draw_info_panel(self, win, state):
        """Draws GNSS and NTRIP info panel with improved structure."""
        if not win:
            return
            
        win.erase()
        self._safe_call(win.border)
        
        max_y, max_x = win.getmaxyx()
        y, x = 1, 2
        label_width = 22
        
        # Helper function for drawing a labeled value with proper attributes
        def draw_line(label, value, attr=self.COLOR_VALUE):
            nonlocal y
            if y >= max_y - 1:
                return y
                
            label_str = f"{label:<{label_width}}:"
            self._safe_addstr(win, y, x, label_str, self.COLOR_LABEL)
            
            value_str = str(value)
            available_width = max_x - x - len(label_str) - 2
            truncated_value = value_str[:max(0, available_width)]
            self._safe_addstr(win, y, x + len(label_str) + 1, truncated_value, attr)
            
            y += 1
            return y
            
        # Helper function for drawing section titles
        def draw_section_title(title):
            nonlocal y
            if y >= max_y - 1:
                return y
                
            self._safe_addstr(win, y, x, title, self.ATTR_BOLD)
            y += 1
            return y
            
        # Draw GNSS Info section
        y = draw_section_title("[GNSS Info]")
        
        # Calculate runtime
        runtime = datetime.now(timezone.utc) - state.get('start_time', datetime.now(timezone.utc))
        y = draw_line("Runtime", str(runtime).split('.')[0])
        
        # Firmware version
        y = draw_line("Firmware", state.get('firmware_version', 'N/A'))
        
        # Position data
        pos = state.get('position', {})
        y = draw_line("Latitude", f"{pos.get('lat', 0.0):.8f}\N{DEGREE SIGN}")
        y = draw_line("Longitude", f"{pos.get('lon', 0.0):.8f}\N{DEGREE SIGN}")
        y = draw_line("Altitude", f"{pos.get('alt', 0.0):.3f} m")
        
        # Fix age calculation
        last_fix_time = state.get('last_fix_time')
        if last_fix_time:
            fix_age = (datetime.now(timezone.utc) - last_fix_time).total_seconds()
            age_attr = self.COLOR_YELLOW if fix_age > 10 else self.COLOR_VALUE
            y = draw_line("Fix Age", f"{fix_age:.1f} sec", attr=age_attr)
        else:
            y = draw_line("Fix Age", "N/A")
            
        # Time To First Fix
        ttff = state.get('first_fix_time_sec')
        y = draw_line("TTFF", f"{ttff:.1f} sec" if ttff is not None else "Pending...")
        
        # RTK status with appropriate coloring
        rtk_status = state.get('rtk_status', "Unknown")
        rtk_attr = self.ATTR_BOLD
        
        if rtk_status == "RTK Fixed":
            rtk_attr |= self.COLOR_GREEN
        elif rtk_status == "RTK Float":
            rtk_attr |= self.COLOR_YELLOW
        elif rtk_status in ["No Fix / Invalid", "Unknown"]:
            rtk_attr |= self.COLOR_RED
        else:
            rtk_attr |= self.COLOR_VALUE
            
        y = draw_line("RTK Status", rtk_status, attr=rtk_attr)
        
        # Other GNSS data
        y = draw_line("Fix Type Code", state.get('fix_type', 0))
        y = draw_line("Sats Used / View", f"{state.get('num_satellites_used', 0)} / {state.get('num_satellites_in_view', 0)}")
        y = draw_line("HDOP", f"{state.get('hdop', DEFAULT_HDOP):.2f}")
        
        # Satellite systems in view
        systems = state.get('satellite_systems', Counter())
        systems_str = ", ".join(f"{sys}:{c}" for sys, c in sorted(systems.items())) if systems else "N/A"
        y = draw_line("Systems View", systems_str)
        
        # NTRIP Info section
        y += 1
        if y < max_y - 1:
            y = draw_section_title("[NTRIP Info]")
        
        if y < max_y - 1:
            ntrip_host = f"{getattr(self._config, 'ntrip_server', 'N/A')}:{getattr(self._config, 'ntrip_port', 'N/A')}"
            y = draw_line("Server", ntrip_host)
            
        if y < max_y - 1:
            y = draw_line("Mountpoint", getattr(self._config, 'ntrip_mountpoint', 'N/A'))
            
        # NTRIP status with coloring
        if y < max_y - 1:
            ntrip_conn = state.get('ntrip_connected', False)
            ntrip_msg = state.get('ntrip_status_message', 'Unknown')
            gave_up = state.get('ntrip_connection_gave_up', False)
            reconnect_attempts = state.get('ntrip_reconnect_attempts', 0)
            
            # Default color for disconnected state
            ntrip_attr = self.COLOR_RED
            
            if gave_up:
                display_status = "Gave Up"
                display_msg = f"{display_status} - {ntrip_msg}"
                ntrip_attr = self.COLOR_RED | self.ATTR_BOLD
            elif ntrip_conn:
                display_status = 'Connected'
                display_msg = f"{display_status} - {ntrip_msg}"
                ntrip_attr = self.COLOR_GREEN
            else:
                display_status = 'Disconnected'
                
                # Check if status message includes retry information
                if "Retry" in ntrip_msg:
                    # Create a visual countdown display
                    try:
                        # Extract retry info
                        parts = ntrip_msg.split()
                        retry_count = parts[1]  # Format: "Retry X/Y in Z.Zs"
                        seconds_left = float(parts[3][:-1])  # Remove 's' at the end
                        
                        # Create a progress bar for the countdown
                        max_width = 10  # Width of the progress bar
                        bar_width = min(max_width, int(max_width * seconds_left / NTRIP_INITIAL_RECONNECT_TIMEOUT))
                        progress_bar = "[" + "#" * (max_width - bar_width) + "-" * bar_width + "]"
                        
                        display_msg = f"{display_status} - {retry_count} {progress_bar} {seconds_left:.1f}s"
                    except (IndexError, ValueError):
                        # Fallback if parsing fails
                        display_msg = f"{display_status} - {ntrip_msg}"
                        
                    ntrip_attr = self.COLOR_YELLOW  # Use yellow for active retry
                else:
                    display_msg = f"{display_status} - {ntrip_msg}"
                    # ntrip_attr remains COLOR_RED
                
            y = draw_line("Status", display_msg, attr=ntrip_attr)
            
            # Show additional reconnection information
            if not ntrip_conn and reconnect_attempts > 0 and y < max_y - 1 and "Retry" not in ntrip_msg:
                retry_attr = self.COLOR_YELLOW if reconnect_attempts < MAX_NTRIP_RETRIES else self.COLOR_RED
                y = draw_line("Reconnect Status", f"Attempt {reconnect_attempts}/{MAX_NTRIP_RETRIES}", attr=retry_attr)
            
        # RTCM age with coloring
        if y < max_y - 1:
            last_data_time = state.get('ntrip_last_data_time')
            if not last_data_time:
                y = draw_line("RTCM Age", "N/A")
            else:
                age_seconds = (datetime.now(timezone.utc) - last_data_time).total_seconds()
                attr = self.COLOR_RED if age_seconds > NTRIP_DATA_TIMEOUT else self.COLOR_VALUE
                y = draw_line("RTCM Age", f"{age_seconds:.1f} sec", attr=attr)
                
        # RTCM data rate
        if y < max_y - 1:
            rates_deque = state.get('ntrip_data_rates', deque())
            avg_rate_bps = sum(rates_deque) / len(rates_deque) if rates_deque else 0
            y = draw_line("RTCM Rate (avg)", f"{avg_rate_bps:.1f} B/s")
            
        # Other NTRIP stats
        if y < max_y - 1:
            y = draw_line("Total RTCM Bytes", f"{state.get('ntrip_total_bytes', 0):,}")
            
        if y < max_y - 1:
            y = draw_line("Reconnects", state.get('ntrip_reconnect_attempts', 0))
            
        # RTCM message types
        if y < max_y - 1:
            rtcm_types_list = list(state.get('last_rtcm_message_types', deque()))
            if rtcm_types_list:
                types_str = '[' + ', '.join(map(str, rtcm_types_list[-5:])) + ']'
                if len(rtcm_types_list) > 5:
                    types_str += '...'
            else:
                types_str = 'None'
                
            y = draw_line("Last RTCM Types", types_str)
            
        win.noutrefresh()

    def _draw_sat_panel(self, win, state):
        """Draws satellite information panel with improved structure."""
        if not win:
            return
            
        win.erase()
        self._safe_call(win.border)
        
        max_y, max_x = win.getmaxyx()
        y, x = 1, 2
        
        # Draw section title
        if y < max_y - 1:
            self._safe_addstr(win, y, x, "[Satellites in View]", self.ATTR_BOLD)
            y += 1
            
        # Define column configuration
        header = f"{'PRN':>3} {'Sys':<5} {'SNR':>3} {'El':>3} {'Az':>3} {'Use':<3}"
        col_widths = [3, 5, 3, 3, 3, 3]
        col_spacing = 1
        total_width = sum(col_widths) + col_spacing * (len(col_widths) - 1)
        
        header_drawn = False
        
        # Draw header if there's enough space
        if y < max_y - 1 and max_x > x + total_width:
            if self._safe_addstr(win, y, x, header, self.ATTR_BOLD):
                y += 1
                if y < max_y - 1:
                    self._safe_addstr(win, y, x, "-" * total_width)
                    y += 1
                header_drawn = True
        elif max_x <= x + total_width and y < max_y - 1:
            self._safe_addstr(win, y, x, "Too narrow", self.COLOR_YELLOW)
            y += 1
            
        # Get satellite information
        satellites_info = state.get('satellites_info', {})
        
        # Sort satellites by system then PRN
        def sort_key(item):
            _, sat_data = item
            prn_int = 999
            try:
                prn_int = int(sat_data.get('prn', 999))
            except:
                pass
            return (sat_data.get('system', 'zzz'), prn_int)
            
        sorted_sats = sorted(satellites_info.items(), key=sort_key)
        
        # Draw each satellite
        for _, sat_info in sorted_sats:
            if y >= max_y - 1:
                break
                
            # Extract satellite data
            prn = sat_info.get('prn', '??')
            system = sat_info.get('system', 'UNK')
            snr = sat_info.get('snr', 0)
            elev = sat_info.get('elevation')
            azim = sat_info.get('azimuth')
            active = sat_info.get('active', False)
            
            # Determine system attributes and short name
            sys_attr = self.COLOR_NORMAL
            sys_short = system[:3].upper()
            
            if system == "GPS":
                sys_attr = self.COLOR_SAT_GPS
                sys_short = "GPS"
            elif system == "GLONASS":
                sys_attr = self.COLOR_SAT_GLO
                sys_short = "GLO"
            elif system == "Galileo":
                sys_attr = self.COLOR_SAT_GAL
                sys_short = "GAL"
            elif system == "BeiDou":
                sys_attr = self.COLOR_SAT_BDS
                sys_short = "BDS"
            elif system == "QZSS":
                sys_attr = self.COLOR_SAT_QZS
                sys_short = "QZS"
            elif system == "NavIC":
                sys_attr = self.COLOR_SAT_OTH
                sys_short = "NAV"
                
            # Determine SNR color
            snr_attr = self.COLOR_NORMAL | curses.A_DIM
            if snr >= self.SNR_THRESHOLD_GOOD:
                snr_attr = self.COLOR_GREEN
            elif snr >= self.SNR_THRESHOLD_BAD:
                snr_attr = self.COLOR_YELLOW
            elif snr > 0:
                snr_attr = self.COLOR_RED
                
            # Format all fields
            prn_str = f"{prn:>{col_widths[0]}}"
            sys_str = f"{sys_short:<{col_widths[1]}}"
            snr_str = f"{snr:>{col_widths[2]}}" if snr else f"{'-':>{col_widths[2]}}"
            el_str = f"{elev:>{col_widths[3]}}" if elev is not None else f"{'-':>{col_widths[3]}}"
            az_str = f"{azim:>{col_widths[4]}}" if azim is not None else f"{'-':>{col_widths[4]}}"
            use_str = f"{'[*]':<{col_widths[5]}}" if active else f"{'[ ]':<{col_widths[5]}}"
            
            # Draw satellite line
            try:
                if header_drawn:
                    current_x = x
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
                    use_attr = self.ATTR_BOLD if active else self.COLOR_NORMAL
                    self._safe_addstr(win, y, current_x, use_str, use_attr)
                elif max_x > x + col_widths[0] + col_spacing + col_widths[2]:
                    # Simplified display when space is limited
                    self._safe_addstr(win, y, x, f"{prn_str} {snr_str}")
                    
                y += 1
            except:
                break
                
        win.noutrefresh()

    def _draw_msg_panel(self, win, state):
        """Draws message log panel with improved truncation for long messages."""
        if not win:
            return

        win.erase()
        self._safe_call(win.border)

        max_y, max_x = win.getmaxyx()
        y, x = 1, 2
        title = "[Messages]"

        # Get messages and calculate how many to display
        messages = state.get('ui_log_messages', deque())
        total_messages = len(messages)
        
        # Maximum number of lines we can display in the panel
        # Reserve space for top border/title and bottom border
        num_msg_lines = max(0, max_y - 3)

        # Calculate if we need "more" indicator for older messages
        start_index = max(0, total_messages - num_msg_lines)
        num_hidden_lines = start_index
        more_indicator = f"[+{num_hidden_lines}]" if num_hidden_lines > 0 else ""

        # Draw title and indicator
        try:
            self._safe_addstr(win, 0, x, title, self.ATTR_BOLD)

            if more_indicator:
                # Position indicator near the right edge but leave space for border
                indicator_x = max(x + len(title) + 1, max_x - len(more_indicator) - 2)
                if indicator_x + len(more_indicator) < max_x - 1:
                    self._safe_addstr(win, 0, indicator_x, more_indicator, self.ATTR_BOLD)
        except:
            pass

        # Draw messages
        line_num = 0
        for i in range(start_index, total_messages):
            # Exit if we've filled all available lines
            if line_num >= num_msg_lines:
                break
                
            # Get the message and calculate display position
            msg = messages[i]
            display_line = y + line_num
            
            # Ensure we never write to or below the bottom border
            if display_line >= max_y - 1:
                break

            # Calculate maximum safe width for messages (account for borders)
            available_width = max_x - x - 3  # Extra margin to stay away from border
            
            # Skip if we don't have enough width to display anything meaningful
            if available_width <= 5:
                break
                
            # Truncate message to fit within panel width
            if len(msg) > available_width:
                truncated_msg = msg[:available_width-3] + "..."
            else:
                truncated_msg = msg

            # Determine message color based on content
            msg_attr = self.COLOR_NORMAL
            lmsg = msg.lower()

            if any(err in lmsg for err in ["error", "failed", "fatal", "critical"]):
                msg_attr = self.COLOR_RED
            elif any(wrn in lmsg for wrn in ["warning", "reconnecting", "timeout"]):
                msg_attr = self.COLOR_YELLOW
            elif any(ok in lmsg for ok in ["connected", "success", "fixed", "sent", "start", "run", "ack"]):
                msg_attr = self.COLOR_GREEN

            # Only increment line counter if we successfully add the message
            if self._safe_addstr(win, display_line, x, truncated_msg, msg_attr):
                line_num += 1
            else:
                # If we failed to add this message, stop trying to add more
                break

        win.noutrefresh()
    def update_display(self, stdscr):
        """Main display update method with improved error handling."""
        # Handle first-time setup
        if self._first_draw:
            self._setup_curses(stdscr)
            self._stdscr.clear()
            self._create_windows()
            self._draw_borders()
            self._stdscr.refresh()
            self._first_draw = False
            self._needs_redraw = False
            self._logger.debug("Initial draw complete.")
            return
            
        # Handle redraw requests (e.g. after resize)
        if self._needs_redraw:
            self._logger.debug("Handling redraw request (e.g., resize).")
            self._create_windows()
            self._draw_borders()
            self._needs_redraw = False
            
            # Mark all panels for full redraw
            for panel in self._panels.values():
                if panel:
                    panel.clearok(True)
                    
        # Get current state snapshot
        state = self._state.get_state_snapshot()
        
        try:
            # Draw each panel
            if "header" in self._panels:
                self._draw_header(self._panels["header"], state)
                
            if "info" in self._panels:
                self._draw_info_panel(self._panels["info"], state)
                
            if "sat" in self._panels:
                self._draw_sat_panel(self._panels["sat"], state)
                
            if "msg" in self._panels:
                self._draw_msg_panel(self._panels["msg"], state)
                
            # Update the physical screen at once
            curses.doupdate()
            
        except curses.error as e:
            self._logger.error(f"Curses error during display update: {e}. Triggering redraw.")
            self.trigger_redraw()
        except Exception as e:
            self._logger.error(f"Unexpected error during display update: {e}", exc_info=True)
            self.trigger_redraw()

    def trigger_redraw(self):
        """Flags that a full redraw is needed (e.g., after resize)."""
        if not self._needs_redraw:
            self._logger.info("Redraw triggered (e.g., resize).")
            self._needs_redraw = True
