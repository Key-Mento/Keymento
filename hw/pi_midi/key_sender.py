"""라즈베리파이에 꽂은 일반 USB 자판을 임시 MIDI 건반으로 쓰는 송신기.

MIDI 키보드가 아직 없을 때 pi_sender.py 대신 실행한다. 패킷 형식·
브로드캐스트 목적지·타임스탬프 규칙이 pi_sender 와 동일해서 PC 세션의
"udp" 입력은 진짜 MIDI 키보드가 꽂힌 Pi 와 구분하지 못한다.

터미널 입력이 아니라 evdev 로 /dev/input 키 이벤트를 직접 읽는다:
  - SSH 로 실행해도 'Pi 에 꽂힌' 자판을 읽는다 (SSH 치는 쪽 자판이 아님).
  - 누르면 note_on, 떼면 note_off — 키 자동 반복(hold)은 무시한다.
  - 기본으로 자판을 독점(grab)해 콘솔/로그인 화면에 글자가 새지 않는다.
    (Pi 화면에서 직접 실행할 때 자판이 잠기면 ESC 로 빠져나온다)

자판 배치 (sim_sender.py 와 동일, 도4~미5):
  흰건반   a  s  d  f  g  h  j  k  l  ;
           도  레  미  파  솔  라  시  도  레  미
  검은건반 w(도#) e(레#) t(파#) y(솔#) u(라#) o(도#) p(레#)
  ESC = 종료

사용 (Pi 에서):
  sudo systemctl stop keymento-midi    # MIDI 대기 서비스와 중복 방지(선택)
  ~/Keymento/.venv/bin/python ~/Keymento/hw/pi_midi/key_sender.py
  ... --pc-ip 10.42.0.17   # 브로드캐스트 대신 특정 PC 로만
  ... --list               # 인식된 입력 장치 확인

권한: /dev/input 읽기에는 input 그룹이 필요하다.
  sudo usermod -aG input $USER 후 재로그인, 또는 sudo 로 실행.
"""

from __future__ import annotations

import argparse
import select
import socket
import time

try:
    from .protocol import DEFAULT_PORT, EVENT_NOTE_OFF, EVENT_NOTE_ON, MidiEvent, pack_event
    from .sim_sender import KEY_TO_NOTE, note_name
except ImportError:
    from protocol import DEFAULT_PORT, EVENT_NOTE_OFF, EVENT_NOTE_ON, MidiEvent, pack_event
    from sim_sender import KEY_TO_NOTE, note_name

try:
    from evdev import InputDevice, ecodes, list_devices
except ImportError:
    raise SystemExit(
        "evdev 모듈이 없습니다. Pi 에서 설치해주세요:\n"
        "  ~/Keymento/.venv/bin/pip install evdev\n"
        "(pi_setup.sh 를 다시 실행해도 됩니다)")

# pi_sender 와 동일 — Pi 핫스팟(10.42.0.1/24) 서브넷 브로드캐스트.
# rtmidi 의존을 피하려고 pi_sender 에서 import 하지 않고 값을 맞춘다.
DEFAULT_DEST_IP = "10.42.0.255"

SCAN_INTERVAL = 2.0     # 자판 탐색 주기(초)
VELOCITY = 90           # 자판에는 세기가 없으므로 고정값

# evdev 키 이벤트 value: 0 = 뗌, 1 = 누름, 2 = 자동 반복(무시)
_KEY_UP, _KEY_DOWN = 0, 1


def _build_code_map() -> dict[int, int]:
    """sim_sender 의 문자 배치를 evdev 키 코드 → 노트 번호로 변환한다."""
    special = {';': 'KEY_SEMICOLON'}
    mapping = {}
    for ch, note in KEY_TO_NOTE.items():
        name = special.get(ch, f"KEY_{ch.upper()}")
        mapping[ecodes.ecodes[name]] = note
    return mapping


CODE_TO_NOTE = _build_code_map()


def _is_keyboard(dev: InputDevice) -> bool:
    """문자 자판만 고른다 (전원 버튼·마우스 등 잡다한 입력 장치 제외)."""
    keys = dev.capabilities().get(ecodes.EV_KEY) or []
    return ecodes.KEY_A in keys and ecodes.KEY_SPACE in keys


def list_input_devices() -> None:
    paths = list_devices()
    if not paths:
        print("입력 장치가 없습니다. (권한 문제라면: sudo usermod -aG input $USER 후 재로그인)")
        return
    print("입력 장치 목록:")
    for path in paths:
        try:
            dev = InputDevice(path)
        except OSError:
            continue
        marker = "" if _is_keyboard(dev) else "  (자판 아님, 자동 선택 제외)"
        print(f"  {path}: {dev.name}{marker}")
        dev.close()


def wait_for_keyboard() -> InputDevice:
    """자판이 꽂힐 때까지 기다렸다가 열어서 반환한다. (pi_sender 와 같은 패턴)"""
    announced = False
    while True:
        for path in list_devices():
            try:
                dev = InputDevice(path)
            except OSError:
                continue
            if _is_keyboard(dev):
                print(f"자판 연결됨: {dev.name} ({dev.path})")
                return dev
            dev.close()

        if not announced:
            print("USB 자판 대기 중... (꽂히면 자동으로 시작합니다)")
            announced = True
        time.sleep(SCAN_INTERVAL)


def send_loop(dev: InputDevice, sock: socket.socket, dest: tuple[str, int],
              grab: bool) -> bool:
    """자판 이벤트를 전송한다. 뽑히면 True(재연결), ESC 면 False(종료)."""
    start_time = time.time()

    if grab:
        try:
            dev.grab()   # 콘솔/데스크톱으로 키 입력이 새지 않게 독점
        except OSError as exc:
            print(f"자판 독점(grab) 실패 — 콘솔에 글자가 샐 수 있습니다: {exc}")

    def send(event_type: int, note: int, velocity: int) -> None:
        event = MidiEvent(event_type=event_type, note=note, velocity=velocity,
                          timestamp=time.time() - start_time)
        sock.sendto(pack_event(event), dest)

    try:
        while True:
            ready, _, _ = select.select([dev.fd], [], [], SCAN_INTERVAL)
            if not ready:
                continue
            for event in dev.read():
                if event.type != ecodes.EV_KEY:
                    continue
                if event.code == ecodes.KEY_ESC and event.value == _KEY_DOWN:
                    print("ESC — 종료합니다.")
                    return False

                note = CODE_TO_NOTE.get(event.code)
                if note is None:
                    continue
                if event.value == _KEY_DOWN:
                    send(EVENT_NOTE_ON, note, VELOCITY)
                    print(f"  → Note ON  : {note} ({note_name(note)})")
                elif event.value == _KEY_UP:
                    send(EVENT_NOTE_OFF, note, 0)
    except OSError:
        print("자판 연결 끊김. 재연결 대기로 전환합니다.")
        return True
    finally:
        try:
            dev.ungrab()
        except OSError:
            pass
        dev.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="일반 USB 자판을 임시 MIDI 건반으로 쓰는 UDP 송신기.")
    parser.add_argument("--pc-ip", default=DEFAULT_DEST_IP,
                        help=f"PC 주소. 기본값 {DEFAULT_DEST_IP} 은 Pi 핫스팟 "
                             "서브넷 브로드캐스트라 PC IP 를 몰라도 된다.")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT,
                        help=f"UDP 포트 (기본: {DEFAULT_PORT})")
    parser.add_argument("--no-grab", action="store_true",
                        help="자판 독점(grab)을 끈다 — 키가 콘솔에도 입력된다.")
    parser.add_argument("--list", action="store_true",
                        help="인식된 입력 장치 목록만 출력하고 종료.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    if args.list:
        list_input_devices()
        return

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    dest = (args.pc_ip, args.port)
    print(f"PC로 전송 중: {args.pc_ip}:{args.port}")
    print("자판 배치  흰건반: a s d f g h j k l ;  (도4~미5)")
    print("           검은건반: w e t y u o p  |  ESC = 종료")

    try:
        while True:
            dev = wait_for_keyboard()
            if not send_loop(dev, sock, dest, grab=not args.no_grab):
                break
    except KeyboardInterrupt:
        print("\n전송 종료.")
    finally:
        sock.close()


if __name__ == "__main__":
    main()
