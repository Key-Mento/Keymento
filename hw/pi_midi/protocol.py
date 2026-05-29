"""UDP MIDI event transport for the Keymento pi_midi module.

Packet format (16 bytes):
  MAGIC (4B) + event_type (1B) + note (1B) + velocity (1B) + padding (1B) + timestamp (8B double)
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Optional

MAGIC = b"KMM1"
PACKET_FORMAT = "!4sBBBxd"
PACKET_SIZE = struct.calcsize(PACKET_FORMAT)
DEFAULT_PORT = 9998

EVENT_NOTE_ON = 0x01
EVENT_NOTE_OFF = 0x02


@dataclass(frozen=True)
class MidiEvent:
    event_type: int
    note: int
    velocity: int
    timestamp: float


def pack_event(event: MidiEvent) -> bytes:
    return struct.pack(
        PACKET_FORMAT,
        MAGIC,
        event.event_type,
        event.note,
        event.velocity,
        event.timestamp,
    )


def unpack_event(data: bytes) -> Optional[MidiEvent]:
    if len(data) < PACKET_SIZE:
        return None

    magic, event_type, note, velocity, timestamp = struct.unpack(
        PACKET_FORMAT, data[:PACKET_SIZE]
    )
    if magic != MAGIC:
        return None

    return MidiEvent(
        event_type=event_type,
        note=note,
        velocity=velocity,
        timestamp=timestamp,
    )
