"""가상 MIDI 시뮬레이터: 실제 하드웨어 없이 두 입력 경로를 모두 흉내 낸다.

실제 하드웨어(MIDI 키보드, 라즈베리파이) 없이 판정 세션을 테스트하기
위한 도구다. 전송 경로(--out)를 고를 수 있다:

  --out midi (기본): loopMIDI 가상 포트로 MIDI 메시지를 보낸다.
     세션의 "local" 입력(LocalMidiInput/rtmidi)이 진짜 키보드처럼 받는다.
     loopMIDI(무료, https://www.tobias-erichsen.de/software/loopmidi.html)가
     실행 중이고 포트가 하나 만들어져 있어야 한다.
  --out udp: pi_sender.py 와 동일한 UDP 패킷(protocol.py)을 보낸다.
     세션의 "udp" 입력(PiUdpInput → MidiReceiver)은 진짜 Pi 와 구분하지
     못한다. 기본 목적지가 localhost 라 PC 한 대로 송수신을 모두 돌린다.

연주 방식도 두 가지:
  1. 재생 모드(기본): MIDI 파일을 정답 타이밍대로 자동 연주한다.
     --jitter 로 음마다 무작위 타이밍 오차를, --wrong-rate 로 일부러
     틀린 음정을 섞어 판정 등급(Perfect/Great/Good/Miss)을 검증할 수 있다.
  2. 키보드 모드(--keys): 컴퓨터 자판을 건반 삼아 직접 연주한다.
     (a s d f g h j k = 도~높은도 흰건반, w e t y u = 검은건반)

사용 순서:
  1. data/settings.json 의 input_source 를 경로에 맞게("local" 또는
     "udp") 두고 세션 실행
       python src/perform/session.py
  2. 다른 터미널에서 시뮬레이터 실행
       python hw/pi_midi/sim_sender.py                 # local 경로, 설정 곡·속도로 재생
       python hw/pi_midi/sim_sender.py --out udp       # udp(가짜 Pi) 경로
       python hw/pi_midi/sim_sender.py --jitter 400    # 타이밍 오차 주입
       python hw/pi_midi/sim_sender.py --keys          # 자판으로 직접 연주
  3. 세션 콘솔에 [START] 가 뜨는 순간 시뮬레이터에서 Enter
     (재생 모드는 그 시점부터 악보 타이밍대로 전송한다)

주의: 첫 음만은 Enter 를 누르는 사람의 반응 속도가 판정 오차에
섞인다(세션이 첫 음을 PC 도착 시각으로 판정하기 때문). 둘째 음부터는
패킷에 실린 송신 시각으로 간격을 판정하므로 오차는 --jitter 값만 따른다.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import socket
import time

try:
    from .protocol import DEFAULT_PORT, EVENT_NOTE_OFF, EVENT_NOTE_ON, MidiEvent, pack_event
except ImportError:
    from protocol import DEFAULT_PORT, EVENT_NOTE_OFF, EVENT_NOTE_ON, MidiEvent, pack_event

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_SONGS_DIR = os.path.join(_PROJECT_ROOT, "songs")
_SETTINGS_PATH = os.path.join(_PROJECT_ROOT, "data", "settings.json")

NOTE_NAMES = ['도', '도#', '레', '레#', '미', '파', '파#', '솔', '솔#', '라', '라#', '시']

# 키보드 모드 자판 배치: 흰건반(중간열) + 검은건반(윗열), C4~E5
KEY_TO_NOTE = {
    'a': 60, 's': 62, 'd': 64, 'f': 65, 'g': 67,
    'h': 69, 'j': 71, 'k': 72, 'l': 74, ';': 76,
    'w': 61, 'e': 63, 't': 66, 'y': 68, 'u': 70, 'o': 73, 'p': 75,
}


def note_name(note: int) -> str:
    return NOTE_NAMES[note % 12]


class UdpSender:
    """pi_sender 와 동일한 형식으로 노트 이벤트를 전송한다."""

    def __init__(self, host: str, port: int):
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._dest = (host, port)
        self._start = time.time()

    def _send(self, event_type: int, note: int, velocity: int) -> float:
        ts = time.time() - self._start
        event = MidiEvent(event_type=event_type, note=note,
                          velocity=velocity, timestamp=ts)
        self._sock.sendto(pack_event(event), self._dest)
        return ts

    def note_on(self, note: int, velocity: int = 90) -> float:
        return self._send(EVENT_NOTE_ON, note, velocity)

    def note_off(self, note: int) -> float:
        return self._send(EVENT_NOTE_OFF, note, 0)

    def close(self) -> None:
        self._sock.close()


class MidiOutSender:
    """loopMIDI 가상 포트로 MIDI 메시지를 보낸다 → 세션 "local" 입력용.

    Windows 는 OS 차원의 가상 MIDI 포트 생성을 지원하지 않으므로
    loopMIDI 가 만들어 둔 포트를 통해 루프백한다.
    """

    def __init__(self, port_hint: str = "loopMIDI"):
        import rtmidi

        self._out = rtmidi.MidiOut()
        ports = self._out.get_ports()
        idx = next((i for i, p in enumerate(ports)
                    if port_hint.lower() in p.lower()), None)
        if idx is None:
            self._out.delete()
            raise SystemExit(
                f"'{port_hint}' MIDI 출력 포트가 없습니다. loopMIDI 가 실행 중이고 "
                f"포트가 만들어져 있는지 확인하세요. (현재 출력 포트: {ports})")
        self._out.open_port(idx)
        self.port_name = ports[idx]

    def note_on(self, note: int, velocity: int = 90) -> None:
        self._out.send_message([0x90, note, velocity])

    def note_off(self, note: int) -> None:
        self._out.send_message([0x80, note, 0])

    def close(self) -> None:
        self._out.close_port()
        self._out.delete()


def load_notes(midi_path: str) -> list[dict]:
    """judgement.get_answer_sheet 와 동일한 규칙으로 노트를 추출한다.

    필터(note_on, velocity>0, note>=60)가 판정 정답지와 다르면 음 개수가
    어긋나 판정이 밀리므로 반드시 같은 규칙을 유지해야 한다.
    """
    import mido

    mid = mido.MidiFile(midi_path)
    notes = []
    absolute_time = 0.0
    for msg in mid:
        absolute_time += msg.time
        if msg.type == 'note_on' and msg.velocity > 0 and msg.note >= 60:
            notes.append({'note': msg.note, 'time': absolute_time})
    return notes


def resolve_song(song_arg: str | None) -> tuple[str, float]:
    """--song 인자(경로 또는 곡 id)와 속도를 결정한다.

    인자가 없으면 세션과 같은 곡·속도로 맞추기 위해 data/settings.json
    의 selected_song / speed 를 읽는다.
    """
    speed = 1.0
    if os.path.isfile(_SETTINGS_PATH):
        try:
            with open(_SETTINGS_PATH, encoding="utf-8") as f:
                data = json.load(f)
            speed = float(data.get("speed") or 1.0)
            if song_arg is None:
                song_arg = data.get("selected_song")
        except (json.JSONDecodeError, OSError, ValueError):
            pass

    if song_arg is None:
        raise SystemExit("곡이 지정되지 않았습니다. --song <경로|곡id> 를 사용하세요.")

    if os.path.isfile(song_arg):
        return song_arg, speed

    for ext in (".mid", ".midi"):
        candidate = os.path.join(_SONGS_DIR, song_arg + ext)
        if os.path.isfile(candidate):
            return candidate, speed

    raise SystemExit(f"곡을 찾을 수 없습니다: {song_arg!r} (songs/ 폴더 또는 파일 경로 확인)")


def wait_for_start() -> None:
    input("\n세션 콘솔에 '[START] 연주 시작!' 이 뜨는 순간 여기서 Enter ▶ ")


def run_replay(sender: UdpSender, midi_path: str, speed: float,
               jitter_ms: float, wrong_rate: float) -> None:
    notes = load_notes(midi_path)
    if not notes:
        raise SystemExit("재생할 노트가 없습니다. MIDI 파일을 확인해주세요.")

    print(f"🎼 곡: {os.path.basename(midi_path)}  |  노트 {len(notes)}개  "
          f"|  속도 {speed:g}x")
    if jitter_ms:
        print(f"   타이밍 오차 주입: ±{jitter_ms:g}ms (음마다 무작위)")
    if wrong_rate:
        print(f"   음정 오답 주입: 확률 {wrong_rate:.0%}")
    wait_for_start()

    start = time.time()
    prev_note = None
    for i, item in enumerate(notes):
        # 세션의 목표 간격 계산(목표 간격 / speed)과 같은 스케일로 재생
        target = item['time'] / speed + random.uniform(-jitter_ms, jitter_ms) / 1000
        delay = start + max(target, 0.0) - time.time()
        if delay > 0:
            time.sleep(delay)

        if prev_note is not None:
            sender.note_off(prev_note)

        note = item['note']
        wrong = wrong_rate > 0 and random.random() < wrong_rate
        if wrong:
            note += random.choice((-1, 1))
        sender.note_on(note)
        prev_note = note

        mark = "❌" if wrong else "→"
        print(f"  {mark} [{i + 1}/{len(notes)}] Note ON {note} ({note_name(note)})")

    sender.note_off(prev_note)
    print("\n재생 완료.")


def run_keyboard(sender: UdpSender) -> None:
    try:
        import msvcrt
    except ImportError:
        raise SystemExit("키보드 모드는 Windows 콘솔에서만 지원됩니다.")

    print("🎹 키보드 모드: 자판이 곧 건반입니다. (q = 종료)")
    print("   흰건반: a  s  d  f  g  h  j  k  l  ;")
    print("          도  레  미  파  솔  라  시  도  레  미")
    print("   검은건반: w(도#) e(레#) t(파#) y(솔#) u(라#) o(도#) p(레#)")
    wait_for_start()

    prev_note = None
    try:
        while True:
            if not msvcrt.kbhit():
                time.sleep(0.005)
                continue
            key = msvcrt.getwch().lower()
            if key == 'q':
                break
            note = KEY_TO_NOTE.get(key)
            if note is None:
                continue
            if prev_note is not None:
                sender.note_off(prev_note)
            sender.note_on(note)
            prev_note = note
            print(f"  → Note ON {note} ({note_name(note)})")
    except KeyboardInterrupt:
        pass
    finally:
        if prev_note is not None:
            sender.note_off(prev_note)
    print("\n키보드 모드 종료.")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="실제 하드웨어 없이 MIDI 입력을 흉내 내는 가상 송신기.")
    parser.add_argument("--out", choices=("midi", "udp"), default="midi",
                        help="전송 경로: midi = loopMIDI 가상 포트(세션 local 입력), "
                             "udp = 가짜 라즈베리파이(세션 udp 입력) (기본: midi)")
    parser.add_argument("--midi-port", default="loopMIDI", metavar="NAME",
                        help="--out midi 에서 사용할 출력 포트 이름 일부 "
                             "(기본: loopMIDI)")
    parser.add_argument("--host", default="127.0.0.1",
                        help="--out udp 의 수신 측 주소 (기본: 127.0.0.1 = 같은 PC)")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT,
                        help=f"--out udp 의 UDP 포트 (기본: {DEFAULT_PORT})")
    parser.add_argument("--song", default=None,
                        help="재생할 MIDI 파일 경로 또는 songs/ 의 곡 id "
                             "(기본: data/settings.json 의 선택 곡)")
    parser.add_argument("--speed", type=float, default=None,
                        help="재생 속도 배율 (기본: data/settings.json 의 값). "
                             "세션 속도와 같아야 Perfect 가 나온다.")
    parser.add_argument("--jitter", type=float, default=0.0, metavar="MS",
                        help="음마다 ±MS 범위의 무작위 타이밍 오차 주입 (기본: 0)")
    parser.add_argument("--wrong-rate", type=float, default=0.0, metavar="P",
                        help="음정을 반음 틀리게 칠 확률 0.0~1.0 (기본: 0)")
    parser.add_argument("--keys", action="store_true",
                        help="재생 대신 컴퓨터 자판으로 직접 연주")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.out == "udp":
        sender = UdpSender(args.host, args.port)
        print(f"UDP 전송 대상: {args.host}:{args.port} "
              f"(가짜 라즈베리파이 → 세션 입력 'udp')")
    else:
        sender = MidiOutSender(args.midi_port)
        print(f"MIDI 전송 대상: {sender.port_name} "
              f"(loopMIDI 가상 포트 → 세션 입력 'local')")

    try:
        if args.keys:
            run_keyboard(sender)
        else:
            midi_path, settings_speed = resolve_song(args.song)
            speed = args.speed if args.speed is not None else settings_speed
            if speed <= 0:
                raise SystemExit(f"속도는 0보다 커야 합니다: {speed}")
            run_replay(sender, midi_path, speed, args.jitter, args.wrong_rate)
    except KeyboardInterrupt:
        print("\n전송 중단.")
    finally:
        sender.close()


if __name__ == "__main__":
    main()
