"""PC-side MIDI receiver.

Listens for MIDI events sent from the Raspberry Pi via UDP.
Can be used standalone or imported as a library for integration with judgement logic.
"""

from __future__ import annotations

import argparse
import socket
import threading
import queue
from typing import Callable, Optional

try:
    from .protocol import DEFAULT_PORT, EVENT_NOTE_ON, EVENT_NOTE_OFF, MidiEvent, PACKET_SIZE, unpack_event
except ImportError:
    from protocol import DEFAULT_PORT, EVENT_NOTE_ON, EVENT_NOTE_OFF, MidiEvent, PACKET_SIZE, unpack_event


class MidiReceiver:
    """Receives MIDI events from Pi over UDP. Thread-safe queue interface."""

    def __init__(self, host: str = "0.0.0.0", port: int = DEFAULT_PORT):
        self.host = host
        self.port = port
        self._queue: queue.Queue[MidiEvent] = queue.Queue()
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._sock: Optional[socket.socket] = None

    def start(self) -> None:
        if self._running:
            return
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind((self.host, self.port))
        self._sock.settimeout(0.5)
        self._running = True
        self._thread = threading.Thread(target=self._listen, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._sock:
            self._sock.close()
        if self._thread:
            self._thread.join(timeout=2.0)
        self._sock = None
        self._thread = None

    def get_event(self, timeout: float = 0.01) -> Optional[MidiEvent]:
        try:
            return self._queue.get(timeout=timeout)
        except queue.Empty:
            return None

    def _listen(self) -> None:
        while self._running:
            try:
                data, _ = self._sock.recvfrom(PACKET_SIZE * 2)
            except socket.timeout:
                continue
            except OSError:
                break

            event = unpack_event(data)
            if event:
                self._queue.put(event)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Receive MIDI events from Raspberry Pi.")
    parser.add_argument("--host", default="0.0.0.0", help="Address to bind (default: 0.0.0.0).")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"UDP port (default: {DEFAULT_PORT}).")
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    receiver = MidiReceiver(host=args.host, port=args.port)
    receiver.start()
    print(f"MIDI 수신 대기 중: {args.host}:{args.port}")
    print("Ctrl+C로 종료\n")

    note_count = 0
    try:
        while True:
            event = receiver.get_event(timeout=0.05)
            if event is None:
                continue

            if event.event_type == EVENT_NOTE_ON:
                note_count += 1
                print(f"  ← [{note_count}] Note ON  : {event.note} (vel={event.velocity}, t={event.timestamp:.3f}s)")
            elif event.event_type == EVENT_NOTE_OFF:
                print(f"  ← Note OFF : {event.note}")

    except KeyboardInterrupt:
        print(f"\n수신 종료. 총 {note_count}개 노트 수신.")
    finally:
        receiver.stop()


if __name__ == "__main__":
    main()
