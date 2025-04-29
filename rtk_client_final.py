# rtk_client_final.py

import argparse
import curses
import logging
import sys
import time
import signal
import traceback

# Import necessary components from other modules
from rtk_constants import DEFAULT_LOG_FILENAME, LOG_FORMAT
from rtk_config import Config, parse_arguments
from rtk_controller import RtkController
import status_display # Import the module
from status_display import StatusDisplay # Import the class

# Logger for the main application part
logger = logging.getLogger("main") # Use 'main' logger defined in logging setup

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
        # Use the main logger here
        logger.warning(log_message)
        shutdown_requested = True
    else:
        logger.warning("Multiple shutdown signals received. Forcing exit might be required.")

# *** MODIFICATION START: Enhanced main_curses ***
def main_curses(stdscr, args: argparse.Namespace):
    """Main function wrapped by curses."""
    controller = None
    status_display_obj = None
    global shutdown_requested

    try:
        # --- Curses Initialization Logging ---
        logger.info("curses.wrapper entered. Initializing components...")

        # Initialize components
        config = Config(args)
        controller = RtkController(config)
        if not hasattr(controller, 'state') or controller.state is None:
             logger.critical("Controller state object not initialized!")
             # Cannot easily display curses error here as setup might fail
             return # Exit early

        status_display_obj = StatusDisplay(controller.state, config)
        if not isinstance(status_display_obj, StatusDisplay):
             logger.critical("StatusDisplay object failed to initialize or is wrong type!")
             logger.critical(f"Object type is: {type(status_display_obj)}")
             return # Exit early

        # Setup curses within the display object
        status_display_obj._setup_curses(stdscr)
        logger.info("Curses setup complete within StatusDisplay.")

        # --- Controller Start with Error Handling ---
        try:
            if not controller.start():
                logger.error("RtkController failed to start. See previous logs.")
                # Attempt to show error in curses before exiting
                try:
                    stdscr.clear()
                    color_red = getattr(status_display_obj, 'COLOR_RED', curses.A_NORMAL)
                    attr_bold = getattr(status_display_obj, 'ATTR_BOLD', curses.A_NORMAL)
                    stdscr.addstr(0, 0, "Error: Failed to start RTK Controller. Check logs. Press key.", color_red | attr_bold)
                    stdscr.refresh()
                    stdscr.nodelay(False)
                    stdscr.getch()
                except Exception as display_err:
                    logger.error(f"Error displaying controller startup failure message: {display_err}")
                return # Exit main_curses
            logger.info("RtkController started successfully.")
        except Exception as start_err:
            logger.critical(f"Unexpected exception during RtkController start: {start_err}", exc_info=True)
            # Attempt to show error in curses
            try:
                stdscr.clear()
                color_red = getattr(status_display_obj, 'COLOR_RED', curses.A_NORMAL)
                attr_bold = getattr(status_display_obj, 'ATTR_BOLD', curses.A_NORMAL)
                stdscr.addstr(0, 0, f"FATAL: Controller start failed: {start_err}. Check logs. Press key.", color_red | attr_bold)
                stdscr.refresh()
                stdscr.nodelay(False)
                stdscr.getch()
            except Exception as display_err:
                logger.error(f"Error displaying controller start exception message: {display_err}")
            return # Exit main_curses

        # --- Main Application Loop ---
        logger.info("Entering main application loop...")
        while controller.is_running and not shutdown_requested:
            try:
                key = stdscr.getch()

                if key == curses.KEY_RESIZE:
                    try:
                        curses.update_lines_cols()
                        if isinstance(status_display_obj, StatusDisplay):
                            status_display_obj.trigger_redraw()
                        logger.info("Terminal resized. Redrawing UI.")
                        if controller and controller.state: controller.state.add_ui_log_message("Terminal resized.")
                    except curses.error as resize_err:
                        logger.error(f"Error handling resize: {resize_err}")
                    except Exception as resize_err_gen:
                        logger.error(f"Unexpected error during resize handling: {resize_err_gen}", exc_info=True)

                elif key == ord('q') or key == ord('Q'):
                    logger.info("Quit key 'q' pressed. Initiating shutdown...")
                    if controller and controller.state: controller.state.add_ui_log_message("Shutdown initiated by user (q).")
                    shutdown_requested = True
                    break # Exit loop

                # --- Display Update with Error Handling ---
                if isinstance(status_display_obj, StatusDisplay):
                    try:
                        status_display_obj.update_display(stdscr)
                    except AttributeError as ae:
                         logger.critical(f"AttributeError calling update_display: {ae}", exc_info=True)
                         logger.critical(f"Object type IS: {type(status_display_obj)}")
                         shutdown_requested = True # Trigger shutdown
                         # Error display attempt moved to outer except block for loop errors
                         raise # Re-raise to be caught by the loop's general handler
                    except curses.error as disp_curses_err:
                        logger.error(f"Curses error during display update: {disp_curses_err}. Triggering redraw.")
                        if isinstance(status_display_obj, StatusDisplay): status_display_obj.trigger_redraw()
                    except Exception as disp_err:
                         logger.error(f"Unexpected error during display update: {disp_err}", exc_info=True)
                         if isinstance(status_display_obj, StatusDisplay): status_display_obj.trigger_redraw()
                else:
                    logger.critical("Error: status_display_obj is not a StatusDisplay instance in main loop!")
                    shutdown_requested = True # Trigger shutdown
                    break # Exit loop

            # --- Exception Handling for the Main Loop Iteration ---
            except KeyboardInterrupt:
                 # Should be caught by signal handler, but handle defensively
                 if not shutdown_requested:
                      logger.warning("KeyboardInterrupt caught inside main loop. Initiating shutdown...")
                      if controller and controller.state: controller.state.add_ui_log_message("Shutdown initiated by user (Ctrl+C).")
                      shutdown_requested = True
                 break # Exit loop
            except curses.error as loop_curses_err:
                 # Handle curses errors that might break the loop (less likely now with specific handling)
                 logger.critical(f"Critical Curses error in main loop iteration: {loop_curses_err}", exc_info=True)
                 shutdown_requested = True
                 # Attempt final error display after loop breaks
                 break
            except Exception as loop_err:
                 # Catch any other unexpected error during the loop iteration
                 logger.critical(f"Unhandled exception in main_curses loop iteration: {loop_err}", exc_info=True)
                 shutdown_requested = True
                 # Attempt final error display after loop breaks
                 break # Exit loop

        logger.info("Exited main application loop.")

    # --- General Exception Handling for main_curses ---
    except KeyboardInterrupt:
        if not shutdown_requested:
             logger.warning("KeyboardInterrupt (Ctrl+C) caught outside main loop. Initiating shutdown...")
             if controller and controller.state: controller.state.add_ui_log_message("Shutdown initiated by user (Ctrl+C).")
             shutdown_requested = True
    except curses.error as e:
        logger.critical(f"Critical Curses error (likely during setup/initial draw): {e}", exc_info=True)
        # Curses already ended by wrapper or never started properly
        print(f"\nFATAL CURSES ERROR: {e}. Check log '{args.log_file}'.", file=sys.stderr)
        print("Ensure terminal is large enough (min 80x20 recommended).", file=sys.stderr)
    except Exception as e:
        logger.critical(f"Unhandled exception during main_curses execution: {e}", exc_info=True)
        shutdown_requested = True # Ensure shutdown sequence runs
        # --- Final Error Display Attempt (if curses was running) ---
        try:
            if stdscr and not stdscr.isendwin():
                 color_red = curses.color_pair(3) | curses.A_BOLD
                 if isinstance(status_display_obj, StatusDisplay):
                      color_red = getattr(status_display_obj, 'COLOR_RED', curses.A_NORMAL) | getattr(status_display_obj, 'ATTR_BOLD', curses.A_NORMAL)

                 stdscr.clear()
                 err_msg = f"FATAL ERROR: {e}. Check log. Press key."
                 max_x = stdscr.getmaxyx()[1]
                 stdscr.addstr(0, 0, err_msg[:max_x-1], color_red)
                 stdscr.refresh()
                 stdscr.nodelay(False)
                 stdscr.getch()
            else:
                 print(f"\nFATAL ERROR (curses inactive): {e}. Check log '{args.log_file}'.", file=sys.stderr)
        except Exception as display_err:
            logger.error(f"Failed to display final error in curses: {display_err}")
            print(f"\nFATAL ERROR: {e}. Check log '{args.log_file}'. (Display failed)", file=sys.stderr)
        # --- End Final Error Display ---

    # --- Finally block for guaranteed cleanup ---
    finally:
        logger.info("Entering main_curses finally block for cleanup...")
        if controller:
            logger.info("Stopping RtkController...")
            try:
                controller.stop() # Ensure controller cleanup happens
                logger.info("RtkController stop sequence initiated.")
            except Exception as stop_err:
                 logger.error(f"Exception during RtkController stop: {stop_err}", exc_info=True)
        else:
             logger.warning("Controller object was not created or available for stopping.")
        # Curses cleanup is handled by the wrapper
        logger.info("Exiting main_curses function.")
# *** MODIFICATION END: Enhanced main_curses ***

if __name__ == "__main__":
    # Parse arguments first
    args = parse_arguments()

    # --- Setup File Logging ---
    log_level = logging.DEBUG if args.debug else logging.INFO
    log_formatter = logging.Formatter(LOG_FORMAT)
    log_filename = args.log_file or DEFAULT_LOG_FILENAME
    try:
        root_logger = logging.getLogger()
        for handler in root_logger.handlers[:]:
            root_logger.removeHandler(handler)
        root_logger.setLevel(logging.DEBUG) # Log everything to root

        # File Handler
        file_handler = logging.FileHandler(log_filename, mode='w')
        file_handler.setFormatter(log_formatter)
        file_handler.setLevel(logging.DEBUG) # Log all levels to file
        root_logger.addHandler(file_handler)

        # Console handler (for pre/post curses)
        console_handler = logging.StreamHandler(sys.stderr)
        console_handler.setFormatter(log_formatter)
        console_handler.setLevel(log_level) # Controlled by --debug
        root_logger.addHandler(console_handler)

        # Initial log messages *before* curses starts
        logger.info(f"File logging setup ({log_filename}) at level DEBUG")
        logger.info(f"Console logging setup at level {logging.getLevelName(log_level)}")
        if args.debug: logger.debug("Debug logging is ON.")

    except Exception as e:
        print(f"Error setting up logging ({log_filename}): {e}", file=sys.stderr)
        sys.exit(1)

    # --- Setup Signal Handlers ---
    try:
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)
        logger.debug("Signal handlers registered.")
    except Exception as e:
        logger.error(f"Failed to register signal handlers: {e}", exc_info=True)
        # Continue execution, but Ctrl+C might not work gracefully

    # --- Run the main application within the curses wrapper ---
    exit_code = 0
    try:
        # *** MODIFICATION START: Logging around wrapper ***
        logger.info("Attempting to start curses.wrapper...")
        curses.wrapper(main_curses, args)
        logger.info("curses.wrapper finished.")
        # *** MODIFICATION END ***

        # Check shutdown reason
        if shutdown_requested:
             print(f"\nApplication shut down due to signal or error. Log file: {log_filename}")
             logger.warning("Application shut down due to signal or error.")
        else:
             print(f"\nApplication finished normally. Log file: {log_filename}")
             logger.info("Application finished normally.")
    except curses.error as e:
        # Handle errors during curses *initialization* by the wrapper itself
        print(f"\nCurses initialization failed: {e}", file=sys.stderr)
        print("Ensure terminal supports curses and is large enough.", file=sys.stderr)
        # Log the error AFTER wrapper fails
        logger.critical(f"curses.wrapper failed to initialize: {e}", exc_info=True)
        exit_code = 1
    except Exception as e:
        # Catch unexpected errors during wrapper setup/teardown
        print(f"\nAn unexpected error occurred outside main_curses: {e}", file=sys.stderr)
        logger.critical(f"Unhandled exception OUTSIDE main_curses: {e}", exc_info=True)
        print(f"Check log file '{log_filename}' for details.", file=sys.stderr)
        exit_code = 1
    finally:
        logger.info("Application final exit sequence.")
        logging.shutdown() # Ensure logs are flushed
        sys.exit(exit_code)
