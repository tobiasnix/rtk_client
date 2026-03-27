# nmea_parser.py - Parses NMEA sentences and updates state

import logging
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Dict

import pynmea2

from rtk_constants import *  # Import constants
from rtk_state import GnssState

logger = logging.getLogger(__name__)

class NmeaParser:
    """Parses NMEA sentences and updates the shared state."""
    def __init__(self, state: GnssState):
        self._state = state
        # Temporary storage for GSV sequence building
        self._current_gsv_sequence_sats = {}
        self._current_gsv_systems = Counter()

    def _get_fix_status_string(self, fix_type: int) -> str:
        """Maps fix type integer to a status string."""
        status_map = {
            FIX_QUALITY_RTK_FIXED: "RTK Fixed",
            FIX_QUALITY_RTK_FLOAT: "RTK Float",
            FIX_QUALITY_DGPS: "DGPS",
            FIX_QUALITY_GPS: "GPS (SPS)",
            FIX_QUALITY_ESTIMATED: "Estimated (DR)",
            FIX_QUALITY_INVALID: "No Fix / Invalid"
            # Add PPS if needed: FIX_QUALITY_PPS: "PPS"
        }
        return status_map.get(fix_type, f"Unknown ({fix_type})")

    def _parse_gga(self, msg: pynmea2.types.talker.GGA) -> None:
        """Parses GGA message content."""
        current_state = self._state.get_state_snapshot()
        old_fix_type = current_state['fix_type']
        try:
            # Ensure gps_qual is treated as an integer, default to invalid if empty/missing
            new_fix_type = int(msg.gps_qual) if msg.gps_qual else FIX_QUALITY_INVALID
        except (ValueError, TypeError):
             logger.warning(f"Could not parse gps_qual '{msg.gps_qual}' to int in GGA. Setting to Invalid.")
             new_fix_type = FIX_QUALITY_INVALID

        now = datetime.now(timezone.utc)
        updates: Dict[str, Any] = {'fix_type': new_fix_type}
        has_valid_coords = False

        # Check for valid latitude and longitude AND if the fix is considered valid
        if msg.latitude is not None and msg.longitude is not None and new_fix_type > FIX_QUALITY_INVALID:
            # Use get() with default to handle potential missing keys if state is somehow inconsistent
            current_pos = current_state.get('position', {})
            current_alt = current_pos.get('alt', self._state.default_alt) # Use state's default if not set
            try:
                 # Ensure altitude is float, fallback to current/default if empty/missing
                 alt_val = float(msg.altitude) if msg.altitude else current_alt
            except (ValueError, TypeError):
                 logger.warning(f"Could not parse altitude '{msg.altitude}' to float in GGA. Using previous/default.")
                 alt_val = current_alt

            updates['position'] = {
                "lat": msg.latitude,
                "lon": msg.longitude,
                "alt": alt_val
            }
            updates['have_position_lock'] = True
            updates['last_fix_time'] = now
            has_valid_coords = True
        else:
            # If coords are invalid or fix is invalid, mark no lock
            updates['have_position_lock'] = False
            # Optionally: Clear position in state? Or leave last known? Current logic leaves last known.
            # updates['position'] = {"lat": 0.0, "lon": 0.0, "alt": 0.0} # Example of clearing

        try:
            # Ensure num_sats is integer, default to 0 if empty/missing
            updates['num_satellites_used'] = int(msg.num_sats) if msg.num_sats else 0
        except (ValueError, TypeError):
             logger.warning(f"Could not parse num_sats '{msg.num_sats}' to int in GGA. Setting to 0.")
             updates['num_satellites_used'] = 0

        try:
            # Ensure hdop is float, fallback to default if empty/missing
            updates['hdop'] = float(msg.horizontal_dil) if msg.horizontal_dil else DEFAULT_HDOP
        except (ValueError, TypeError):
            logger.warning(f"Could not parse hdop '{msg.horizontal_dil}' to float in GGA. Using default.")
            updates['hdop'] = DEFAULT_HDOP

        # Record Time To First Fix (TTFF)
        if not current_state.get('first_fix_time_sec') and has_valid_coords:
            updates['first_fix_time_sec'] = (now - current_state['start_time']).total_seconds()

        # Handle RTK status string and change logging
        new_rtk_status = self._get_fix_status_string(new_fix_type)
        updates['rtk_status'] = new_rtk_status
        old_rtk_status = current_state['rtk_status']

        if new_rtk_status != old_rtk_status:
            self._state.add_ui_log_message(f"Fix status: {new_rtk_status}")
            logger.info(f"Fix type changed from {old_fix_type} ({old_rtk_status}) to {new_fix_type} ({new_rtk_status})")

        # Track last RTK Fixed time and epochs since
        if new_rtk_status == "RTK Fixed":
            updates['last_rtk_fix_time'] = now
            updates['epochs_since_fix'] = 0 # Reset counter on getting fix
        elif current_state.get('last_rtk_fix_time'):
            # Increment only if we previously had an RTK fix
             updates['epochs_since_fix'] = current_state.get('epochs_since_fix', 0) + 1

        # Update fix type counter (thread-safe via state method if needed, here direct lock assumed)
        with self._state._lock:
            self._state.fix_type_counter[new_rtk_status] += 1

        self._state.update(**updates)

    def _parse_gsv(self, msg: pynmea2.types.talker.GSV) -> None:
        """Parses GSV message content and aggregates satellite data."""
        # Safely parse header info with error handling
        try:
            num_sentences = int(msg.num_messages) if msg.num_messages else 0
            sentence_num = int(msg.msg_num) if msg.msg_num else 0
            num_sv_in_view = int(msg.num_sv_in_view) if msg.num_sv_in_view else 0
        except (ValueError, TypeError, AttributeError) as e:
            logger.debug(f"Could not parse GSV header info from {msg}: {e}")
            return # Cannot proceed without header info

        # Validate parsed header info
        if sentence_num < 1 or num_sentences < 1:
             logger.debug(f"Invalid GSV sequence numbers: msg {sentence_num}/{num_sentences}")
             return # Ignore invalid sequence

        is_first_sentence = (sentence_num == 1)
        is_last_sentence = (sentence_num == num_sentences)

        # Reset sequence data on first message
        if is_first_sentence:
            self._current_gsv_sequence_sats = {}
            self._current_gsv_systems = Counter()

        # Map talker ID to satellite system name
        talker = msg.talker
        sat_system_map = {
            'GP': "GPS",
            'GL': "GLONASS",
            'GA': "Galileo",
            'GB': "BeiDou",
            'GQ': "QZSS",
            'GI': "NavIC" # Indian Constellation
            # Add others if needed (e.g., SBAS talkers like WA, SD, etc.)
        }
        sat_system = sat_system_map.get(talker, f"UNK-{talker}") # Mark unknown talkers

        # Process up to 4 satellites per GSV message
        for i in range(1, 5):
            sv_id_key = f'sv_prn_num_{i}'
            elevation_key = f'elevation_deg_{i}'
            azimuth_key = f'azimuth_{i}'
            snr_key = f'snr_{i}'

            # Check if the fields exist in the parsed message object
            if not all(hasattr(msg, key) for key in [sv_id_key, elevation_key, azimuth_key, snr_key]):
                # logger.debug(f"GSV sentence {sentence_num} missing fields for sat index {i}.")
                continue # Skip to next satellite if fields are missing

            # Get values safely, converting empty strings/None to appropriate defaults
            prn = getattr(msg, sv_id_key)
            if not prn: # Skip if PRN is missing (shouldn't happen often)
                logger.debug(f"GSV sentence {sentence_num} missing PRN for sat index {i}.")
                continue

            try: elev = int(getattr(msg, elevation_key)) if getattr(msg, elevation_key) else None
            except (ValueError, TypeError): elev = None

            try: azim = int(getattr(msg, azimuth_key)) if getattr(msg, azimuth_key) else None
            except (ValueError, TypeError): azim = None

            try: snr = int(getattr(msg, snr_key)) if getattr(msg, snr_key) else 0 # Default SNR to 0 if missing
            except (ValueError, TypeError): snr = 0

            # Construct unique key for the satellite
            sat_key = f"{talker}-{prn}"

            # Store satellite data in the temporary sequence dict
            self._current_gsv_sequence_sats[sat_key] = {
                'prn': prn,
                'snr': snr,
                'elevation': elev,
                'azimuth': azim,
                'system': sat_system,
                'active': False # Default to not active, GSA will update
            }
            # Count system only if SNR indicates signal reception
            if snr > 0:
                self._current_gsv_systems[sat_system] += 1

        # Update state after the last sentence of the sequence is received
        if is_last_sentence:
            # Check for potential mismatches in total count (optional debug)
            if len(self._current_gsv_sequence_sats) != num_sv_in_view:
                 logger.debug(f"GSV mismatch: Header reported {num_sv_in_view} SVs, found {len(self._current_gsv_sequence_sats)} in sequence.")

            snr_stats = self._calculate_snr_stats(self._current_gsv_sequence_sats)

            # Prepare updates for the main state object
            updates = {
                'num_satellites_in_view': num_sv_in_view, # Use header value as authoritative count
                'max_satellites_seen': max(self._state.get_state_snapshot().get('max_satellites_seen', 0), num_sv_in_view),
                'satellites_info': self._current_gsv_sequence_sats.copy(), # Update with latest full sequence
                'satellite_systems': self._current_gsv_systems.copy(), # Update system counts
                'snr_stats': snr_stats
            }
            self._state.update(**updates)

            # Clear temporary storage after processing the last sentence
            # Consider clearing vs. not clearing. Clearing prevents using stale data if a sequence is missed.
            # Not clearing allows GSA messages to update status even if GSV is intermittent.
            # Current choice: Don't clear, rely on GSV to overwrite.
            # self._current_gsv_sequence_sats = {}
            # self._current_gsv_systems = Counter()

    def _calculate_snr_stats(self, satellites_info: Dict[str, Dict[str, Any]]) -> Dict[str, float]:
        """Calculates SNR statistics based on provided satellite info."""
        # Extract SNRs, filtering out those with 0 or None SNR
        snrs = [sat['snr'] for sat in satellites_info.values() if sat.get('snr') and sat['snr'] > 0]

        stats = {"min": 0.0, "max": 0.0, "avg": 0.0, "good_count": 0, "bad_count": 0}
        if not snrs:
            return stats # Return defaults if no valid SNRs found

        stats["min"] = float(min(snrs))
        stats["max"] = float(max(snrs))
        stats["avg"] = sum(snrs) / len(snrs)
        # Define SNR quality thresholds (could be moved to constants)
        stats["good_count"] = sum(1 for snr in snrs if snr >= SNR_THRESHOLD_GOOD)
        stats["bad_count"] = sum(1 for snr in snrs if snr <= SNR_THRESHOLD_BAD)
        return stats

    def _parse_gsa(self, msg: pynmea2.types.talker.GSA) -> None:
        """Parses GSA message to mark which satellites are actively used in the fix."""
        active_sat_keys = set()
        talker = msg.talker # e.g., 'GP', 'GL', 'GA', 'GN'

        # Iterate through the 12 possible satellite ID fields in the GSA message
        for i in range(1, 13):
            sat_id_field = f'sv_id{i:02}' # Field names are sv_id01, sv_id02, ...
            if hasattr(msg, sat_id_field):
                prn = getattr(msg, sat_id_field)
                # Only process if PRN is present (not empty string or None)
                if prn:
                    # Construct the key used in satellites_info
                    sat_key = f"{talker}-{prn}"

                    # Handle 'GN' talker (indicates satellites from multiple constellations)
                    # We need to find the actual talker prefix stored in our state
                    if talker == 'GN':
                        found = False
                        # Access state safely - lock needed if GSV runs concurrently
                        # Assume state access within a single NMEA processing thread is safe for now
                        # If GSV/GSA can interleave from different threads, lock is needed here!
                        # with self._state._lock: # <-- Add if concurrent modification possible
                        current_sats = self._state.satellites_info # Get snapshot (or direct access if safe)
                        # Iterate through known satellites to find matching PRN
                        for key, sat_info in current_sats.items():
                            if sat_info.get('prn') == prn:
                                active_sat_keys.add(key) # Add the correctly prefixed key (e.g., 'GP-10')
                                found = True
                                break # Found the satellite, no need to check further
                        if not found:
                            # This can happen if GSV hasn't reported the satellite yet
                            logger.debug(f"GNGSA referenced PRN {prn}, but it was not found in current GSV info.")
                    else:
                        # For specific talkers (GP, GL, etc.), the key is straightforward
                        active_sat_keys.add(sat_key)

        # Update the 'active' status in the main state dictionary
        # Again, consider locking if state can be modified by other threads concurrently
        # with self._state._lock: # <-- Add if concurrent modification possible
        satellites_info_state = self._state.satellites_info # Direct access or snapshot
        keys_to_check = list(satellites_info_state.keys())
        updated_count = 0
        deactivated_count = 0

        for key in keys_to_check:
            # Ensure the key still exists (might be removed by GSV updates concurrently?)
             if key in satellites_info_state:
                current_status = satellites_info_state[key].get('active', False)
                is_relevant_talker = (talker == 'GN' or key.startswith(talker + '-'))

                # Check if this satellite was listed in the current GSA message
                if key in active_sat_keys:
                    if not current_status: # Mark active only if not already marked
                       satellites_info_state[key]['active'] = True
                       updated_count += 1
                # If the satellite was NOT in the GSA message, AND the GSA talker matches the satellite's system
                # (or GSA talker is 'GN'), mark it as inactive.
                elif is_relevant_talker:
                     if current_status: # Mark inactive only if previously active
                        satellites_info_state[key]['active'] = False
                        deactivated_count += 1

        # logger.debug(f"GSA ({talker}): Marked {updated_count} active, {deactivated_count} inactive.")
        # No self._state.update() needed here if we modified the state dict directly (inside lock if needed)
