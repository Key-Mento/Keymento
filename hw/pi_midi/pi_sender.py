"""Raspberry Pi-side MIDI sender.

USB MIDI 키보드 입력을 읽어 UDP 로 PC 에 전송한다. 전원만 꽂으면
동작하는 무인 운용을 전제로 설계했다 (keymento-midi.service 로 부팅 시
자동 시작, pi_setup.sh 참고):

  - MIDI 키보드가 안 꽂혀 있으면 종료하지 않고 꽂힐 때까지 기다린다.
  - 동작 중 키보드가 뽑히면 다시 대기 상태로 돌아가 재연결한다.
  - 목적지 기본값이 Pi 핫스팟(Keymento AP, 10.42.0.1/24) 서브넷의
    브로드캐스트 주소라서 PC 의 IP 를 몰라도 된다 — 같은 Wi-Fi 에
    붙은 모든 기기에 뿌리고, PC 쪽 수신기(pc_receiver)가 받는다.

수동 실행/디버깅:
  python pi_sender.py                     # 브로드캐스트로 전송
  python pi_sender.py --pc-ip 10.42.0.17  # 특정 PC 로만 전송
  python pi_sender.py --list-ports        # MIDI 장치 확인
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

# Pi 핫스팟(NetworkManager shared, 10.42.0.1/24) 기준 브로드캐스트 주소.
# PC 가 DHCP 로 어떤 IP 를 받든 이 주소로 보내면 도달한다.
DEFAULT_DEST_IP = "10.42.0.255"

# 키보드 탐색/생존 확인 주기(초)
SCAN_INTERVAL = 2.0


def _keyboard_ports(names: list[str]) -> list[int]:
    """실제 키보드일 수 있는 포트 인덱스만 남긴다.

    ALSA 는 장치가 없어도 'Midi Through' 가상 포트를 항상 노출하므로
    걸러내지 않으면 빈 포트에 붙어 조용히 아무것도 못 받는다.
    """
    return [i for i, n in enumerate(names) if "midi through" not in n.lower()]


def list_midi_ports():
    midi_in = rtmidi.MidiIn()
    ports = midi_in.get_ports()
    if not ports:
        print("MIDI 입력 장치를 찾을 수 없습니다.")
    else:
        print("사용 가능한 MIDI 입력 장치:")
        for i, name in enumerate(ports):
            marker = "" if i in _keyboard_ports(ports) else "  (가상 포트, 자동 선택 제외)"
            print(f"  [{i}] {name}{marker}")
    midi_in.delete()
    return ports


def wait_for_keyboard(preferred: int | None = None) -> tuple[rtmidi.MidiIn, str]:
    """키보드가 꽂힐 때까지 기다렸다가 열어서 반환한다."""
    announced = False
    while True:
        midi_in = rtmidi.MidiIn()
        ports = midi_in.get_ports()
        candidates = _keyboard_ports(ports)
        if preferred is not None:
            candidates = [i for i in candidates if i == preferred]

        if candidates:
            idx = candidates[0]
            midi_in.open_port(idx)
            print(f"MIDI 포트 열림: [{idx}] {ports[idx]}")
            return midi_in, ports[idx]

        midi_in.delete()
        if not announced:
            print("MIDI 키보드 대기 중... (꽂히면 자동으로 시작합니다)")
            announced = True
        time.sleep(SCAN_INTERVAL)


def _port_still_present(port_name: str) -> bool:
    probe = rtmidi.MidiIn()
    present = port_name in probe.get_ports()
    probe.delete()
    return present


def send_loop(midi_in: rtmidi.MidiIn, port_name: str,
              sock: socket.socket, dest: tuple[str, int]) -> None:
    """키보드가 뽑힐 때까지 이벤트를 전송한다. 뽑히면 반환한다."""
    start_time = time.time()
    last_scan = time.time()

    while True:
        msg = midi_in.get_message()
        if msg:
            message, _ = msg
            if len(message) >= 3:
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

        # 키보드가 뽑혔는지 주기적으로 확인 (rtmidi 는 뽑혀도 조용히
        # 이벤트만 끊기므로 포트 목록에서 사라졌는지로 감지한다)
        if time.time() - last_scan > SCAN_INTERVAL:
            last_scan = time.time()
            if not _port_still_present(port_name):
                print("MIDI 키보드 연결 끊김. 재연결 대기로 전환합니다.")
                return

        time.sleep(0.001)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Read MIDI from USB keyboard and send to PC over UDP.")
    parser.add_argument("--pc-ip", default=DEFAULT_DEST_IP,
                        help=f"PC 주소. 기본값 {DEFAULT_DEST_IP} 은 Pi 핫스팟 "
                             "서브넷 브로드캐스트라 PC IP 를 몰라도 된다.")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT,
                        help=f"UDP port (default: {DEFAULT_PORT}).")
    parser.add_argument("--midi-port", type=int, default=None,
                        help="MIDI 입력 포트 인덱스 (기본: 자동 — 가상 포트를 "
                             "제외한 첫 장치)")
    parser.add_argument("--list-ports", action="store_true",
                        help="List available MIDI ports and exit.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    if args.list_ports:
        list_midi_ports()
        return

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    # 브로드캐스트 주소 전송 허용 (유니캐스트 목적지여도 무해)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    dest = (args.pc_ip, args.port)
    print(f"PC로 전송 중: {args.pc_ip}:{args.port}")

    try:
        while True:
            midi_in, port_name = wait_for_keyboard(args.midi_port)
            try:
                send_loop(midi_in, port_name, sock, dest)
            finally:
                midi_in.close_port()
                midi_in.delete()
    except KeyboardInterrupt:
        print("\n전송 종료.")
    finally:
        sock.close()


if __name__ == "__main__":
    main()
