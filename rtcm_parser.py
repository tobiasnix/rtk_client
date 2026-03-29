"""RTCM3 message type extraction from binary data."""

import logging

logger = logging.getLogger(__name__)


def extract_rtcm_message_types(data: bytes) -> list[int]:
    """Extracts RTCM3 message types from received binary data."""
    types_found = []
    i = 0
    data_len = len(data)
    while i < data_len - 5: # Need at least preamble + header bytes
        # Look for RTCM3 preamble (0xD3)
        if data[i] == 0xD3 and (data[i+1] & 0xFC) == 0: # Check reserved bits are zero
            try:
                payload_length = ((data[i+1] & 0x03) << 8) | data[i+2]
                total_length = 3 + payload_length + 3 # Preamble+Header + Payload + CRC
                if i + total_length <= data_len:
                    # Extract message type (12 bits starting at byte 3, bit 0)
                    message_type = (data[i+3] << 4) | (data[i+4] >> 4)
                    types_found.append(message_type)
                    i += total_length # Move to the next potential message
                else:
                    # Incomplete message at the end of the buffer
                    logger.debug(f"Incomplete RTCM message found at index {i}. Need {total_length}, have {data_len-i}.")
                    break # Stop parsing this chunk
            except IndexError:
                logger.debug(f"IndexError parsing RTCM header at index {i}.")
                break # Corrupted data or index issue
        else:
            i += 1 # Move to the next byte if not a preamble
    return types_found
