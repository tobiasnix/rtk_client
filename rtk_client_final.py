# rtk_client_final.py - Main entry point for the RTK Client application

import argparse
import curses
import logging
import sys
import time
import signal # Import signal handling
import traceback # Import traceback for detailed error logging

# Import necessary components from other modules
from rtk_constants import DEFAULT_LOG_FILENAME, LOG_FORMAT
from rtk_config import Config, parse_arguments
from rtk_controller import RtkController
# --- Import StatusDisplay explicitly to check its attributes if needed ---
import status_display # Import the module
from status_display import StatusDisplay # Import the class

# Logger for the main application part
logger = logging.getLogger("main")

# Global flag to signal shutdown
shutdown_requested = False

def signal_handler(sig, frame):
    """Handle termination signals like SIGINT (Ctrl+C) and SIGTERM."""
    global shutdown_requested
    if not shutdown_requested:
        try:
            sig_name = signal.Signals(sig).name
        except ValueError:
            sig_name = f"Signal {sig}"
        log_message = f"{sig_name} received. Initiating shutdown..."
        logger.warning(log_message)
        # Try logging to UI if possible, but don't rely on it during shutdown
        # if controller and controller.state: controller.state.add_ui_log_message(log_message) # Avoid accessing controller globally
        shutdown_requested = True
    else:
        logger.warning("Multiple shutdown signals received. Forcing exit might be required.")
        # Consider a more forceful exit if needed after a delay
        # sys.exit(1)


def main_curses(stdscr, args: argparse.Namespace):
    """Main function wrapped by curses."""
    controller = None
    # Initialize status_display to None
    status_display_obj = None # Use a different name to avoid confusion with module
    global shutdown_requested # Access the global flag

    try:
        # Initialize components using parsed arguments
        config = Config(args)
        controller = RtkController(config)
        # Ensure controller.state exists before passing it
        if not hasattr(controller, 'state') or controller.state is None:
             # This should not happen based on RtkController.__init__
             logger.critical("Controller state object not initialized!")
             # Display error directly using stdscr as status_display isn't created yet
             stdscr.clear()
             stdscr.addstr(0, 0, "FATAL: Controller state missing. Check logs. Press key.", curses.A_BOLD | curses.color_pair(3)) # Assume color pair 3 is RED
             stdscr.refresh()
             stdscr.nodelay(False)
             stdscr.getch()
             return

        # --- Removed module check logging ---
        # logger.info(f"Imported status_display module: {status_display}")
        # logger.info(f"Attributes of status_display module: {dir(status_display)}")
        # --- End module check ---

        # Create the StatusDisplay object
        status_display_obj = StatusDisplay(controller.state, config) # Access state via property

        # Check if status_display object was created successfully
        if not isinstance(status_display_obj, StatusDisplay):
             logger.critical("StatusDisplay object failed to initialize or is wrong type!")
             logger.critical(f"Object type is: {type(status_display_obj)}")
             # Display error directly using stdscr
             stdscr.clear()
             stdscr.addstr(0, 0, "FATAL: StatusDisplay failed. Check logs. Press key.", curses.A_BOLD | curses.color_pair(3))
             stdscr.refresh()
             stdscr.nodelay(False)
             stdscr.getch()
             return

        status_display_obj._setup_curses(stdscr) # Setup curses in the display object

        if not controller.start():
            # Use curses to display error before exiting wrapper
            try:
                stdscr.clear()
                # Use color attributes from status_display if available, else fallback
                color_red = getattr(status_display_obj, 'COLOR_RED', curses.A_NORMAL)
                attr_bold = getattr(status_display_obj, 'ATTR_BOLD', curses.A_NORMAL)
                stdscr.addstr(0, 0, "Error: Failed to start RTK Controller. Check logs. Press any key.", color_red | attr_bold)
                stdscr.refresh()
                stdscr.nodelay(False) # Make getch blocking
                stdscr.getch()
            except Exception as e:
                logger.error(f"Error displaying startup failure message: {e}")
            return # Exit main_curses

        # Main application loop - check controller and shutdown flag
        while controller.is_running and not shutdown_requested:
            key = stdscr.getch() # Check for input (non-blocking due to timeout set in setup_curses)

            if key == curses.KEY_RESIZE:
                 # Handle terminal resize
                 try:
                      # It's generally recommended to recreate windows on resize
                      curses.update_lines_cols() # Update curses' internal size variables
                      if isinstance(status_display_obj, StatusDisplay):
                           status_display_obj.trigger_redraw() # Signal display to recreate windows
                      logger.info("Terminal resized. Redrawing UI.")
                      if controller and controller.state: controller.state.add_ui_log_message("Terminal resized.")
                      # Optional: redraw immediately? Or let next loop handle it?
                      # stdscr.clear() # Force clear before redraw?
                      # status_display_obj.update_display(stdscr) # Redraw now
                 except curses.error as e:
                      logger.error(f"Error handling resize: {e}")
                      # May need more robust handling if resize causes issues
            elif key == ord('q') or key == ord('Q'):
                # Handle quit command
                logger.info("Quit key 'q' pressed. Initiating shutdown...")
                if controller and controller.state: controller.state.add_ui_log_message("Shutdown initiated by user (q).")
                shutdown_requested = True # Signal shutdown
                break # Exit the main loop immediately now shutdown_requested is True
            # Add other key handlers here if needed (e.g., pause, toggle debug)
            # elif key == ord('p'): logger.info("Pause key pressed (not implemented).")

            # --- Main Display Update ---
            # Check if status_display object is still valid before calling method
            if isinstance(status_display_obj, StatusDisplay):
                try:
                    # --- Removed dir() logging ---
                    # logger.debug(f"Attempting to call update_display. Object type: {type(status_display_obj)}")
                    # logger.critical(f"Attributes of status_display_obj: {dir(status_display_obj)}")
                    # --- End dir() logging ---

                    # *** THIS IS THE LINE THAT WAS PREVIOUSLY FAILING ***
                    status_display_obj.update_display(stdscr) # Update display using display object method

                except AttributeError as ae:
                     # Log the specific AttributeError in detail (Should not happen now)
                     logger.critical(f"AttributeError calling update_display: {ae}", exc_info=True)
                     logger.critical(f"Object type IS: {type(status_display_obj)}")
                     shutdown_requested = True # Trigger shutdown on this critical error
                     # Attempt to show minimal error in curses
                     try:
                         if not stdscr.isendwin():
                              stdscr.clear()
                              stdscr.addstr(0, 0, f"FATAL Display Error: {ae}. Check log.", curses.A_BOLD | curses.color_pair(3))
                              stdscr.refresh()
                              time.sleep(3) # Pause to show error
                     except: pass # Ignore errors during emergency display
                except curses.error as e:
                    # Handle potential curses error during display update (e.g., after resize)
                    logger.error(f"Curses error during display update: {e}. Triggering redraw.")
                    if isinstance(status_display_obj, StatusDisplay): status_display_obj.trigger_redraw() # Signal full redraw needed
                except Exception as e: # Catch any other unexpected error during display update
                    logger.error(f"Unexpected error during display update: {e}", exc_info=True)
                    if isinstance(status_display_obj, StatusDisplay): status_display_obj.trigger_redraw() # Trigger redraw as state might be inconsistent
            else:
                # This case should not be reached if initialization checks pass
                logger.critical("Error: status_display_obj is not a StatusDisplay instance in main loop!")
                shutdown_requested = True # Trigger shutdown

            # Optional small sleep if getch timeout is very short or not used
            # time.sleep(0.05) # e.g., 50ms sleep

    # Handle expected shutdown conditions outside the main loop
    except KeyboardInterrupt:
        # This might still happen if signal handler doesn't catch it fast enough
        if not shutdown_requested:
             logger.warning("KeyboardInterrupt (Ctrl+C) caught directly. Initiating shutdown...")
             if controller and controller.state: controller.state.add_ui_log_message("Shutdown initiated by user (Ctrl+C).")
             shutdown_requested = True
    # Handle specific curses errors that might terminate the loop
    except curses.error as e:
        # Handle curses errors, e.g., terminal too small during resize or init
        logger.critical(f"Critical Curses error in main loop: {e}", exc_info=True)
        # curses already ended by wrapper, print to stderr is the best option
        print(f"\nFATAL CURSES ERROR: {e}. Check log '{args.log_file}'.", file=sys.stderr)
        print("Ensure terminal is large enough (min 80x20 recommended).", file=sys.stderr)
    # Handle any other unexpected exceptions
    except Exception as e:
        logger.critical(f"Unhandled exception in main_curses loop: {e}", exc_info=True)
        # Log the full traceback
        # logger.critical(traceback.format_exc()) # Redundant if exc_info=True used
        shutdown_requested = True # Ensure shutdown sequence runs

        # --- Final Error Display Attempt (Corrected isendwin check) ---
        try:
            # Check if curses is still active using the main stdscr object
            if not stdscr.isendwin():
                 color_red = curses.color_pair(3) | curses.A_BOLD # Assume pair 3=RED
                 # Check if status_display_obj and its attributes exist before using them
                 if isinstance(status_display_obj, StatusDisplay):
                      color_red = getattr(status_display_obj, 'COLOR_RED', curses.A_NORMAL) | getattr(status_display_obj, 'ATTR_BOLD', curses.A_NORMAL)

                 stdscr.clear()
                 err_msg = f"FATAL ERROR: {e}. Check log. Press key."
                 # Truncate error message if too long for the first line
                 max_x = stdscr.getmaxyx()[1]
                 stdscr.addstr(0, 0, err_msg[:max_x-1], color_red)
                 stdscr.refresh()
                 stdscr.nodelay(False) # Blocking getch
                 stdscr.getch() # Wait for user
            else:
                 # Curses already ended, print to stderr
                 print(f"\nFATAL ERROR (curses inactive): {e}. Check log '{args.log_file}'.", file=sys.stderr)
        except AttributeError as ae:
             # Catch the specific error from the log: 'isendwin' on the wrong object (should be fixed now)
             # or other potential AttributeErrors during this final display attempt.
             logger.error(f"AttributeError during final error display: {ae}")
             # Log type of stdscr for debugging
             try: logger.error(f"Object causing error: {type(stdscr)}")
             except: pass
             print(f"\nFATAL ERROR: {e}. Check log '{args.log_file}'. (Display failed: {ae})", file=sys.stderr)
        except Exception as display_err:
            logger.error(f"Failed to display final error in curses: {display_err}")
            print(f"\nFATAL ERROR: {e}. Check log '{args.log_file}'. (Display failed)", file=sys.stderr)
        # --- End Final Error Display ---

    finally:
        logger.info("Exiting main_curses function, stopping controller...")
        if controller:
             # Check if stop method exists before calling
             if hasattr(controller, 'stop') and callable(controller.stop):
                  controller.stop() # Ensure controller cleanup happens
             else:
                  logger.error("Controller object missing 'stop' method!")
        logger.info("Controller stop sequence initiated.")


if __name__ == "__main__":
    # Parse arguments first
    args = parse_arguments()

    # --- Setup File Logging ---
    log_level = logging.DEBUG if args.debug else logging.INFO
    log_formatter = logging.Formatter(LOG_FORMAT)
    log_filename = args.log_file or DEFAULT_LOG_FILENAME # Use default if not provided
    try:
        # Configure root logger
        root_logger = logging.getLogger()
        # Remove potential default handlers if basicConfig was called elsewhere
        for handler in root_logger.handlers[:]:
            root_logger.removeHandler(handler)
        root_logger.setLevel(logging.DEBUG) # Set root logger to lowest level

        # File Handler (logs DEBUG and above)
        file_handler = logging.FileHandler(log_filename, mode='w') # Overwrite log each run
        file_handler.setFormatter(log_formatter)
        file_handler.setLevel(logging.DEBUG) # Log everything to file
        root_logger.addHandler(file_handler)

        # Console handler for messages *before* curses starts or *after* it ends
        # Only logs INFO and above unless debug is set
        console_handler = logging.StreamHandler(sys.stderr) # Log to stderr
        console_handler.setFormatter(log_formatter)
        console_handler.setLevel(log_level) # Set level based on args.debug
        root_logger.addHandler(console_handler)

        logger.info(f"File logging setup ({log_filename}) at level DEBUG")
        logger.info(f"Console logging setup at level {logging.getLevelName(log_level)}")
        if args.debug: logger.debug("Debug logging is ON.")

    except Exception as e:
        print(f"Error setting up logging ({log_filename}): {e}", file=sys.stderr)
        sys.exit(1)
    # --- End Logging Setup ---

    # --- Setup Signal Handlers ---
    signal.signal(signal.SIGINT, signal_handler) # Handle Ctrl+C
    signal.signal(signal.SIGTERM, signal_handler) # Handle termination signal
    logger.debug("Signal handlers registered.")
    # --- End Signal Handlers ---


    # Run the main application within the curses wrapper
    exit_code = 0
    try:
        # Pass parsed args to the main function run by the wrapper
        curses.wrapper(main_curses, args)
        # Check shutdown_requested flag to determine if exit was normal or forced
        if shutdown_requested:
             print(f"\nApplication shut down due to signal or error. Log file: {log_filename}")
        else:
             print(f"\nApplication finished normally. Log file: {log_filename}")
    except curses.error as e:
        # Handle errors during curses initialization (e.g., unsupported terminal)
        print(f"\nCurses initialization failed: {e}", file=sys.stderr)
        print("Ensure your terminal supports curses (e.g., not basic Windows cmd, use WSL, Linux terminal, macOS terminal) and is large enough (min 80x20 recommended).", file=sys.stderr)
        exit_code = 1
    except Exception as e:
        # Catch any other unexpected errors during setup or wrapper execution
        print(f"\nAn unexpected error occurred before or during curses wrapper execution: {e}", file=sys.stderr)
        logger.critical(f"Unhandled exception preventing curses wrapper: {e}", exc_info=True)
        print(f"Check log file '{log_filename}' for details.", file=sys.stderr)
        exit_code = 1
    finally:
        logger.info("Application exiting.")
        logging.shutdown() # Ensure logs are flushed before exiting
        sys.exit(exit_code) # Exit with appropriate code
