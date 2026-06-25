"""Raspberry Pi-side MIDI sender.

Reads MIDI input from a USB keyboard connected to the Pi
and forwards note events to the PC via UDP.
"""

from __future__ import annotations

import argparse
import socket
import time

import rtmidi

try:
    from .protocol import DEFAULT_PORT, EVENT_NOTE_OFF, EVENT_NOTE_ON, MidiEvent, pack_event
except ImportError:
    from protocol import DEFAULT_PORT, EVENT_NOTE_OFF, EVENT_NOTE_ON, MidiEvent, pack_event


def list_midi_ports():
    midi_in = rtmidi.MidiIn()
    ports = midi_in.get_ports()
    if not ports:
        print("MIDI 입력 장치를 찾을 수 없습니다.")
    else:
        print("사용 가능한 MIDI 입력 장치:")
        for i, name in enumerate(ports):
            print(f"  [{i}] {name}")
    midi_in.delete()
    return ports


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Read MIDI from USB keyboard and send to PC.")
    parser.add_argument("--pc-ip", required=True, help="PC (notebook) IP address.")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"UDP port (default: {DEFAULT_PORT}).")
    parser.add_argument("--midi-port", type=int, default=0, help="MIDI input port index (default: 0).")
    parser.add_argument("--list-ports", action="store_true", help="List available MIDI ports and exit.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    if args.list_ports:
        list_midi_ports()
        return

    midi_in = rtmidi.MidiIn()
    ports = midi_in.get_ports()

    if not ports:
        print("MIDI 입력 장치를 찾을 수 없습니다. --list-ports로 확인해주세요.")
        return

    if args.midi_port >= len(ports):
        print(f"포트 {args.midi_port}이 존재하지 않습니다. 사용 가능: 0~{len(ports)-1}")
        return

    midi_in.open_port(args.midi_port)
    print(f"MIDI 포트 열림: [{args.midi_port}] {ports[args.midi_port]}")

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    dest = (args.pc_ip, args.port)
    print(f"PC로 전송 중: {args.pc_ip}:{args.port}")

    start_time = time.time()

    try:
        while True:
            msg = midi_in.get_message()
            if msg:
                message, _ = msg
                if len(message) < 3:
                    continue
                status, note, velocity = message[0], message[1], message[2]

                if status & 0xF0 == 0x90 and velocity > 0:
                    event_type = EVENT_NOTE_ON
                elif status & 0xF0 == 0x80 or (status & 0xF0 == 0x90 and velocity == 0):
                    event_type = EVENT_NOTE_OFF
                else:
                    continue

                event = MidiEvent(
                    event_type=event_type,
                    note=note,
                    velocity=velocity,
                    timestamp=time.time() - start_time,
                )
                sock.sendto(pack_event(event), dest)

                if event_type == EVENT_NOTE_ON:
                    print(f"  → Note ON  : {note} (vel={velocity})")

            time.sleep(0.001)

    except KeyboardInterrupt:
        print("\n전송 종료.")
    finally:
        midi_in.close_port()
        midi_in.delete()
        sock.close()


if __name__ == "__main__":
    main()
