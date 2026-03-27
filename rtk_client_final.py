# rtk_client_final.py

import argparse
import curses
import logging
import logging.handlers
import os
import signal
import sys
from typing import Optional

from rtk_config import Config, parse_arguments

# Import necessary components from other modules
from rtk_constants import DEFAULT_LOG_FILENAME, LOG_FORMAT
from rtk_controller import RtkController

# Import the class directly
from status_display import StatusDisplay

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
        log_message = f"{sig_name} received. Initiating graceful shutdown..."
        logger.warning(log_message)
        # Log to UI state if possible (might not be initialized yet)
        # if 'controller' in globals() and controller and controller.state:
        #    controller.state.add_ui_log_message(f"Shutdown signal ({sig_name}) received.")
        shutdown_requested = True
    else:
        logger.warning("Multiple shutdown signals received. Forcing exit might occur.")

# --- Main Curses Function ---
def main_curses(stdscr, args: argparse.Namespace):
    """Main function wrapped by curses."""
    controller: Optional[RtkController] = None
    status_display_obj: Optional[StatusDisplay] = None
    global shutdown_requested

    try:
        logger.info("curses.wrapper entered. Initializing components...")

        # --- Initialize Core Components ---
        try:
             config = Config(args)
             # Pass config to controller
             controller = RtkController(config)
             if not controller.state: # Check if state object exists
                  logger.critical("Controller state object not initialized!")
                  raise RuntimeError("Controller state failed initialization.") # Raise to be caught below

             # Create StatusDisplay instance, passing state and config
             status_display_obj = StatusDisplay(controller.state, config)

             logger.info("Core components initialized.")
        except Exception as init_err:
             logger.critical(f"Failed to initialize core components: {init_err}", exc_info=True)
             # Can't use curses yet, error will be logged and potentially printed post-wrapper
             raise # Propagate error to curses.wrapper handler

        # --- Setup Curses UI ---
        try:
             # Pass stdscr to StatusDisplay for setup
             status_display_obj._setup_curses(stdscr)
             logger.info("Curses setup complete via StatusDisplay.")
        except Exception as curses_setup_err:
             logger.critical(f"Curses setup failed within StatusDisplay: {curses_setup_err}", exc_info=True)
             # Curses setup failed, wrapper will likely handle cleanup
             raise # Propagate error

        # --- Start RTK Controller ---
        try:
            if not controller.start():
                logger.error("RtkController failed to start. Check previous logs.")
                # Attempt to show error in curses before exiting if possible
                if status_display_obj and stdscr and not stdscr.isendwin():
                    err_msg = "Error: RTK Controller failed to start. Check logs. Press key."
                    color = getattr(status_display_obj, 'COLOR_RED', curses.A_NORMAL)
                    attr = getattr(status_display_obj, 'ATTR_BOLD', curses.A_NORMAL)
                    max_y, max_x = stdscr.getmaxyx()
                    stdscr.clear()
                    stdscr.addstr(0, 0, err_msg[:max_x-1], color | attr)
                    stdscr.refresh()
                    stdscr.nodelay(False) # Wait for key press
                    stdscr.getch()
                return # Exit main_curses function
            logger.info("RtkController started successfully.")
        except Exception as start_err:
            logger.critical(f"Unexpected exception during RtkController start: {start_err}", exc_info=True)
            if status_display_obj and stdscr and not stdscr.isendwin():
                 # Attempt to show fatal error
                 err_msg = f"FATAL: Controller start failed: {start_err}. Check logs. Press key."
                 color = getattr(status_display_obj, 'COLOR_RED', curses.A_NORMAL)
                 attr = getattr(status_display_obj, 'ATTR_BOLD', curses.A_NORMAL)
                 max_y, max_x = stdscr.getmaxyx()
                 stdscr.clear()
                 stdscr.addstr(0, 0, err_msg[:max_x-1], color | attr)
                 stdscr.refresh()
                 stdscr.nodelay(False)
                 stdscr.getch()
            return # Exit main_curses function

        # --- Main Application Loop ---
        logger.info("Entering main application loop...")
        while controller.is_running and not shutdown_requested:
            try:
                # --- Input Handling ---
                key = stdscr.getch() # Blocks for timeout defined in setup

                if key == curses.KEY_RESIZE:
                    # Let StatusDisplay handle resize internally
                    if status_display_obj:
                         status_display_obj.trigger_redraw()
                         logger.info("Resize event detected, redraw triggered.")
                    else:
                         logger.warning("Resize event detected but StatusDisplay object is missing.")

                elif key != -1: # Process actual key presses (-1 means timeout)
                     if key == ord('q') or key == ord('Q'):
                          logger.info("Quit key 'q' pressed. Initiating shutdown...")
                          if controller and controller.state: controller.state.add_ui_log_message("Shutdown requested by user (q).")
                          shutdown_requested = True
                          break # Exit loop immediately
                     elif key == ord('r') or key == ord('R'):
                          logger.warning("NTRIP reset key 'r' pressed.")
                          if controller:
                               if controller.reset_ntrip_connection():
                                    logger.info("NTRIP connection reset successful.")
                               else:
                                    logger.warning("NTRIP connection reset failed (client not running?).")
                          else:
                               logger.warning("Cannot reset NTRIP: controller not available.")
                     elif key == ord('?'):
                          if status_display_obj:
                               status_display_obj.show_help_overlay(stdscr)
                     else:
                          # Log other key presses if needed for debugging
                          # logger.debug(f"Key pressed: {key}")
                          pass

                # --- Display Update ---
                if status_display_obj:
                    # Display update handles its own exceptions
                    status_display_obj.update_display(stdscr)
                else:
                    # This should ideally not happen if initialization was successful
                    logger.critical("StatusDisplay object missing in main loop!")
                    shutdown_requested = True # Trigger shutdown if UI is lost
                    break

            # --- Exception Handling for the Loop Iteration ---
            except KeyboardInterrupt:
                 # Should be caught by signal handler, but handle defensively
                 if not shutdown_requested:
                      logger.warning("KeyboardInterrupt caught inside main loop. Initiating shutdown...")
                      if controller and controller.state: controller.state.add_ui_log_message("Shutdown (Ctrl+C in loop).")
                      shutdown_requested = True
                 break
            except curses.error as loop_curses_err:
                 # Handle curses errors during getch() or update_display()
                 logger.error(f"Curses error in main loop iteration: {loop_curses_err}", exc_info=False) # Keep log concise
                 if status_display_obj: status_display_obj.trigger_redraw() # Try to recover display
                 # Avoid immediate shutdown unless error is severe (e.g., -1 return from getch?)
                 # time.sleep(0.1) # Small pause before next iteration
            except Exception as loop_err:
                 # Catch any other unexpected error during the loop
                 logger.critical(f"Unhandled exception in main_curses loop: {loop_err}", exc_info=True)
                 if controller and controller.state: controller.state.add_ui_log_message(f"FATAL LOOP ERROR: {loop_err}")
                 shutdown_requested = True # Trigger shutdown on critical errors
                 # Attempt final error display after loop breaks (in outer finally)
                 break

        logger.info("Exited main application loop.")

    # --- General Exception Handling for main_curses ---
    except KeyboardInterrupt:
        # Catch Ctrl+C during setup/initialization phase
        if not shutdown_requested:
             logger.warning("KeyboardInterrupt caught outside main loop (during setup?). Initiating shutdown...")
             shutdown_requested = True
    except Exception as e:
        # Catch errors during initialization or critical failures
        logger.critical(f"Unhandled exception during main_curses execution: {e}", exc_info=True)
        shutdown_requested = True # Ensure shutdown sequence runs
        # Try a final message attempt *if* curses was somewhat initialized
        try:
            if stdscr and not stdscr.isendwin():
                 color = curses.color_pair(3) | curses.A_BOLD if curses.has_colors() else curses.A_BOLD
                 err_msg = f"FATAL ERROR: {e}. Check log. Press key."
                 max_y, max_x = stdscr.getmaxyx()
                 stdscr.clear()
                 stdscr.addstr(0, 0, err_msg[:max_x-1], color)
                 stdscr.refresh()
                 stdscr.nodelay(False)
                 stdscr.getch()
        except Exception as display_err:
            logger.error(f"Failed to display final error message in curses: {display_err}")
            # Fallback to printing to stderr if curses display fails
            print(f"\nFATAL ERROR (curses unavailable): {e}. Check log.", file=sys.stderr)

    # --- Guaranteed Cleanup ---
    finally:
        logger.info("Entering main_curses finally block for cleanup...")
        # Stop the controller first
        if controller:
            logger.info("Stopping RtkController...")
            try:
                controller.stop()
                logger.info("RtkController stop sequence complete.")
            except Exception as stop_err:
                 logger.error(f"Exception during RtkController stop: {stop_err}", exc_info=True)
        else:
             logger.warning("Controller object was not available for stopping.")

        if status_display_obj:
            status_display_obj.close()

        # Curses cleanup is handled by the curses.wrapper
        logger.info("Exiting main_curses function.")

# --- Script Entry Point ---
if __name__ == "__main__":
    args = parse_arguments()

    # --- Setup Logging ---
    log_level = logging.DEBUG if args.debug else logging.INFO
    log_formatter = logging.Formatter(LOG_FORMAT)
    log_filename = args.log_file or DEFAULT_LOG_FILENAME

    try:
        # Ensure log directory exists if specified in path
        log_dir = os.path.dirname(log_filename)
        if log_dir and not os.path.exists(log_dir):
            os.makedirs(log_dir)

        root_logger = logging.getLogger() # Get root logger
        # Remove existing handlers if any (useful for repeated runs in some environments)
        for handler in root_logger.handlers[:]:
            handler.close()
            root_logger.removeHandler(handler)

        root_logger.setLevel(logging.DEBUG) # Capture all levels at root

        # File Handler with rotation (always logs DEBUG level)
        # maxBytes=5MB, keep 3 backup files
        file_handler = logging.handlers.RotatingFileHandler(
            log_filename, maxBytes=5*1024*1024, backupCount=3, encoding='utf-8'
        )
        file_handler.setFormatter(log_formatter)
        file_handler.setLevel(logging.DEBUG)
        root_logger.addHandler(file_handler)

        # Console handler for pre/post curses messages
        # Logs INFO or DEBUG based on args.debug
        console_handler = logging.StreamHandler(sys.stderr)
        console_handler.setFormatter(log_formatter)
        console_handler.setLevel(log_level)
        root_logger.addHandler(console_handler)

        # Initial log messages
        logger.info(f"File logging setup ({log_filename}) at level DEBUG")
        logger.info(f"Console logging setup at level {logging.getLevelName(log_level)}")
        if args.debug: logger.debug("Debug logging is ON.")

    except Exception as e:
        print(f"FATAL: Error setting up logging to '{log_filename}': {e}", file=sys.stderr)
        sys.exit(1)

    # --- Setup Signal Handlers ---
    try:
        signal.signal(signal.SIGINT, signal_handler)  # Ctrl+C
        signal.signal(signal.SIGTERM, signal_handler) # kill/system shutdown
        logger.debug("Signal handlers registered (SIGINT, SIGTERM).")
    except Exception as e:
        logger.error(f"Failed to register signal handlers: {e}", exc_info=True)
        # Continue, but shutdown might not be graceful

    # --- Run the Application ---
    exit_code = 0
    try:
        logger.info("Attempting to start curses.wrapper...")
        # curses.wrapper handles initscr, noecho, cbreak, keypad, and endwin automatically
        curses.wrapper(main_curses, args)
        logger.info("curses.wrapper finished.")

        # Check shutdown reason after wrapper finishes
        if shutdown_requested:
             print(f"\nApplication shut down. Log file: {log_filename}")
             logger.warning("Application shut down due to signal, error, or user request.")
        else:
             print(f"\nApplication finished normally. Log file: {log_filename}")
             logger.info("Application finished normally.")

    except curses.error as e:
        # Handle errors during curses *initialization* by the wrapper itself
        print(f"\nFATAL: Curses initialization failed: {e}", file=sys.stderr)
        print("Ensure terminal supports curses (e.g., UTF-8 locale) and is large enough.", file=sys.stderr)
        logger.critical(f"curses.wrapper failed to initialize: {e}", exc_info=True)
        exit_code = 1
    except Exception as e:
        # Catch unexpected errors occurring outside main_curses (e.g., during wrapper setup/teardown)
        print(f"\nFATAL: An unexpected error occurred: {e}", file=sys.stderr)
        logger.critical(f"Unhandled exception OUTSIDE main_curses loop: {e}", exc_info=True)
        print(f"Check log file '{log_filename}' for details.", file=sys.stderr)
        exit_code = 1
    finally:
        logger.info("Application final exit sequence.")
        logging.shutdown() # Ensure all logs are flushed and handlers closed
        print(f"Exiting with code {exit_code}.")
        sys.exit(exit_code)
