"""판정 세션용 MIDI 입력 소스 추상화.

로컬 MIDI 장치(rtmidi)와 라즈베리파이 UDP 수신(hw/pi_midi)을 같은
인터페이스로 제공한다. 세션 쪽은 소스가 무엇이든 `poll()` 로
정규화된 NoteEvent 를 받는다.

timestamp 는 '그 소스의 클럭'으로 찍은 타건 시각이다.
  - 로컬  : PC 의 time.time()
  - UDP   : 라즈베리파이가 패킷에 실어 보낸 타건 시각(Pi 클럭, 상대값)
클럭 기준점이 서로 달라 절대 시각 비교는 불가능하지만, 판정이 쓰는 것은
'직전 음과의 간격'이므로 같은 소스 안의 차이값은 유효하다. UDP 소스에서
도착 시각 대신 이 값을 쓰면 Wi-Fi 지터가 판정 오차에 섞이지 않는다.

파일 구성:
  1. 공통 계약  : NoteEvent(정규화된 이벤트), MidiInputSource(인터페이스)
  2. 구현체     : LocalMidiInput(rtmidi), PiUdpInput(라즈베리파이 UDP)
  3. 팩토리     : create_input_source — 설정 문자열로 구현체 선택

사용 예:
    with create_input_source("udp") as source:
        event = source.poll()   # NoteEvent 또는 None
"""

import os
import sys
import threading
import time
from dataclasses import dataclass

import rtmidi

# hw/pi_midi 패키지를 찾기 위해 프로젝트 루트를 경로에 추가
_PROJECT_ROOT = os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from hw.pi_midi.pc_receiver import MidiReceiver          # noqa: E402
from hw.pi_midi.protocol import (                        # noqa: E402
    DEFAULT_PORT as UDP_DEFAULT_PORT,
    EVENT_NOTE_ON,
)

INPUT_SOURCES = ("local", "udp")

# 자동 탐지에서 '진짜 건반이 아니다'라고 볼 이름 조각. loopMIDI 는 테스트용
# 가상 케이블이고, wavetable 류는 출력 전용 신디사이저가 입력에도 뜨는 것.
_VIRTUAL_PORT_HINTS = ("loopmidi", "wavetable", "microsoft gs")

# ── MIDI 상태 바이트 해석용 상수 ────────────────────────────────────
_STATUS_KIND_MASK = 0xF0    # 상위 4비트 = 메시지 종류 (하위 4비트는 채널)
_NOTE_ON = 0x90
_NOTE_OFF = 0x80


# ════════════════════════════════════════════════════════════════════
# 1. 공통 계약 — 모든 입력 소스가 따르는 규격
# ════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class NoteEvent:
    """소스와 무관하게 정규화된 노트 이벤트."""
    is_on: bool        # True = note on, False = note off
    note: int          # MIDI 노트 번호
    velocity: int
    timestamp: float   # 소스 클럭 기준 타건 시각(초). 간격 계산에만 사용.


class MidiInputSource:
    """입력 소스 공통 인터페이스.

    서브클래스는 poll() 과 close() 만 구현하면 된다. with 문을
    지원하므로 호출자가 close() 를 직접 챙기지 않아도 된다.
    """

    name = "base"

    def poll(self):
        """대기 중인 이벤트가 있으면 NoteEvent, 없으면 None."""
        raise NotImplementedError

    def close(self):
        raise NotImplementedError

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


# ════════════════════════════════════════════════════════════════════
# 2. 구현체
# ════════════════════════════════════════════════════════════════════

class LocalMidiInput(MidiInputSource):
    """PC 에 직접 연결된 MIDI 키보드(rtmidi) 입력."""

    name = "local"

    def __init__(self, port=0):
        self._midi_in = rtmidi.MidiIn()
        ports = self._midi_in.get_ports()

        error = None
        if not ports:
            error = ("로컬 MIDI 입력 장치가 없습니다. 키보드 연결을 확인하거나 "
                     "입력 소스를 'udp'(라즈베리파이)로 바꿔주세요.")
        elif not 0 <= port < len(ports):
            error = f"MIDI 포트 {port}이 없습니다. 사용 가능: 0~{len(ports) - 1}"
        if error:
            self._midi_in.delete()
            raise RuntimeError(error)

        self._midi_in.open_port(port)
        self.port_name = ports[port]

    def poll(self):
        msg = self._midi_in.get_message()
        if not msg:
            return None
        message, _ = msg
        if len(message) < 3:
            return None
        status, note, velocity = message[0], message[1], message[2]
        kind = status & _STATUS_KIND_MASK

        if kind == _NOTE_ON and velocity > 0:
            return NoteEvent(True, note, velocity, time.time())
        # velocity 0 인 Note On 은 Note Off 로 취급 (MIDI 관례)
        if kind == _NOTE_OFF or (kind == _NOTE_ON and velocity == 0):
            return NoteEvent(False, note, velocity, time.time())
        return None

    def close(self):
        self._midi_in.close_port()
        self._midi_in.delete()


class DemoInput(MidiInputSource):
    """정답지를 스스로 연주하는 가짜 소스 — 건반 없이 흐름만 볼 때 쓴다.

    실제 건반도 loopMIDI 도 없이 카운트다운 → 진행 → 결과까지 전 과정을
    확인할 수 있다. wrong_every 를 주면 그 주기마다 일부러 틀린 음을 한 번
    끼워 넣어 오답 표시와 연습 모드의 '다시' 동작도 볼 수 있다.

    Args:
        notes:       [{"note": int, "time": float}, ...] — 정답지.
        speed:       속도 배율. 세션에 넘긴 값과 같아야 박자가 맞는다.
        wrong_every: N 개마다 한 번 틀린 음을 넣는다. 0 이면 전부 정확히.
        retry_after_wrong:
                     틀린 뒤에 정답을 이어서 칠지. 연습 모드에서는 True 여야
                     한다(맞출 때까지 대기하므로 안 치면 영원히 멈춘다).
                     일반 모드에서는 반드시 False — 틀려도 세션이 정답 음을
                     소진하고 넘어가므로, 정답을 또 치면 그게 '다음 음'
                     자리를 먹어 이후 전부 한 칸씩 밀린다.
    """

    name = "demo"

    def __init__(self, notes, speed=1.0, wrong_every=0,
                 retry_after_wrong=True):
        self._notes = sorted(notes, key=lambda n: n["time"])
        self._speed = speed if speed > 0 else 1.0
        self._wrong_every = wrong_every
        self._retry_after_wrong = retry_after_wrong
        self._index = 0
        self._pending_off = None       # (발송 시각, note)
        self._retry_note = None        # 틀린 음 뒤에 이어서 칠 정답
        self._started_at = None        # 첫 poll 때 잡는다

    def _now(self):
        return (time.time() - self._started_at) * self._speed

    def poll(self):
        now = time.time()

        # 시계는 첫 poll 에서 시작한다. 소스는 카운트다운 전에 만들어지므로
        # 생성 시각을 기준으로 잡으면 카운트다운만큼 앞서 나가 버린다.
        if self._started_at is None:
            self._started_at = now

        # 앞서 낸 note_on 을 잠깐 뒤에 꺼 준다 (소리·판정 모두 정상 동작).
        if self._pending_off is not None and now >= self._pending_off[0]:
            note = self._pending_off[1]
            self._pending_off = None
            return NoteEvent(False, note, 0, now)

        if self._pending_off is not None:
            return None

        # 일부러 틀린 뒤에는 곧바로 정답을 쳐서 진행이 막히지 않게 한다.
        if self._retry_note is not None:
            note = self._retry_note
            self._retry_note = None
            self._pending_off = (now + 0.05, note)
            return NoteEvent(True, note, 100, now)

        if self._index >= len(self._notes):
            return None

        entry = self._notes[self._index]

        if self._now() < entry["time"]:
            return None

        self._index += 1
        note = entry["note"]

        if (self._wrong_every
                and self._index % self._wrong_every == 0):
            # 반음 아래를 친다. 연습 모드에서만 이어서 정답을 고쳐 친다.
            wrong = note - 1 if note > 0 else note + 1
            if self._retry_after_wrong:
                self._retry_note = note
            self._pending_off = (now + 0.05, wrong)
            return NoteEvent(True, wrong, 100, now)

        self._pending_off = (now + 0.05, note)
        return NoteEvent(True, note, 100, now)

    def close(self):
        pass


class PiUdpInput(MidiInputSource):
    """라즈베리파이(hw/pi_midi/pi_sender.py)가 UDP 로 보내는 입력."""

    name = "udp"

    def __init__(self, host="0.0.0.0", port=UDP_DEFAULT_PORT):
        self._receiver = MidiReceiver(host=host, port=port)
        self._receiver.start()
        self.port_name = f"UDP {host}:{port}"

    def poll(self):
        event = self._receiver.get_event(timeout=0.001)
        if event is None:
            return None
        # Pi 가 찍은 타건 시각을 그대로 사용 → 네트워크 지터 제거
        return NoteEvent(
            is_on=(event.event_type == EVENT_NOTE_ON),
            note=event.note,
            velocity=event.velocity,
            timestamp=event.timestamp,
        )

    def close(self):
        self._receiver.stop()


# ════════════════════════════════════════════════════════════════════
# 3. 팩토리 — 설정 문자열("local" | "udp")로 구현체 선택
# ════════════════════════════════════════════════════════════════════

def is_virtual_port(name):
    """loopMIDI 같은 가상 포트(진짜 건반이 아님)인지."""
    return any(v in name.lower() for v in _VIRTUAL_PORT_HINTS)


class MidiSystemStuck(RuntimeError):
    """Windows MIDI 장치 열거가 응답하지 않을 때.

    USB 를 뽑았는데 장치 항목이 남는 등 반쯤 죽은 MIDI 장치가 있으면
    rtmidi 의 포트 열거가 C 레벨에서 멈춘다.
    """


PORT_QUERY_TIMEOUT = 6.0

# 포트 목록을 찍어 주는 한 줄짜리 프로그램. 별도 프로세스에서 돌린다.
_PORT_QUERY_CODE = (
    "import json,rtmidi;"
    "m=rtmidi.MidiIn();"
    "print(json.dumps(m.get_ports()));"
    "m.delete()"
)


def list_midi_ports(timeout=PORT_QUERY_TIMEOUT):
    """연결된 로컬 MIDI 입력 포트 이름 목록.

    같은 프로세스에서 rtmidi 를 부르지 않고 자식 프로세스에 물어보는
    이유: 반응 없는 MIDI 장치가 있으면 rtmidi 의 열거가 GIL 을 쥔 채
    C 레벨에서 멈춘다. 그러면 감시 스레드조차 돌지 못해 프로그램 전체가
    얼어붙는다. 자식 프로세스는 시간이 지나면 죽일 수 있다.

    응답이 없으면 MidiSystemStuck 을 던진다.
    """
    import json
    import subprocess

    try:
        done = subprocess.run(
            [sys.executable, "-c", _PORT_QUERY_CODE],
            capture_output=True, text=True, timeout=timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except subprocess.TimeoutExpired:
        raise MidiSystemStuck(
            f"MIDI 장치 목록을 읽는 데 {timeout:g}초가 넘게 걸립니다. "
            "반응 없는 MIDI 장치가 있는 것 같습니다.")
    except OSError as exc:
        raise MidiSystemStuck(f"MIDI 조회 프로세스를 띄우지 못했습니다: {exc}")

    if done.returncode != 0:
        raise MidiSystemStuck(
            "MIDI 장치 조회가 실패했습니다: "
            f"{(done.stderr or '').strip()[:200]}")

    try:
        return json.loads(done.stdout.strip() or "[]")
    except json.JSONDecodeError:
        raise MidiSystemStuck(
            f"MIDI 장치 목록을 해석하지 못했습니다: {done.stdout[:120]!r}")


def midi_stuck_help():
    """MIDI 열거가 막혔을 때 사용자에게 보여 줄 복구 안내."""
    return "\n".join([
        "[!] MIDI 장치가 응답하지 않아 포트 목록을 읽지 못했습니다.",
        "",
        "    보통 USB 를 뽑았는데 장치 항목이 남아 생깁니다.",
        "    아래를 순서대로 시도하세요:",
        "      1) 건반 USB 를 뽑았다 다시 꽂기 (되도록 다른 USB 포트)",
        "      2) loopMIDI 를 껐다 켜기",
        "      3) 그래도 안 되면 재부팅",
        "",
        "    확인용 (PowerShell):",
        "      Get-PnpDevice -Class MEDIA | ? Status -ne OK",
        "    Status 가 Unknown/Error 인 항목이 원인입니다.",
        "",
        "    건반 없이 나머지를 확인하려면: run test",
    ])


def resolve_midi_port(spec="auto", verbose=True):
    """포트 지정을 실제 번호로 바꾼다.

    매번 --list-ports 로 번호를 확인해 넘기는 일을 없애기 위한 헬퍼다.
    USB 를 다시 꽂으면 번호가 바뀌므로 이름으로 고르는 편이 안전하다.

        "auto"  loopMIDI 같은 가상 포트를 빼고 남은 첫 장치(진짜 건반).
                진짜 건반이 없으면 0번으로 넘어간다.
        숫자     그 번호를 그대로 쓴다. ("2", 2)
        그 외    이름에 그 문자열이 든 첫 포트. ("keystation")

    Returns:
        포트 번호(int). 포트가 하나도 없으면 0 (열 때 RuntimeError 로 알림).
    """
    try:
        ports = list_midi_ports()
    except MidiSystemStuck as exc:
        if verbose:
            print(f"⚠️  {exc}")
        return 0

    if not ports:
        return 0

    def announce(index, why):
        if verbose:
            print(f"🎹 MIDI 입력: [{index}] {ports[index]}  ({why})")
        return index

    if isinstance(spec, int):
        return announce(spec, "지정") if 0 <= spec < len(ports) else spec

    text = str(spec).strip()

    if text.isdigit():
        index = int(text)
        return announce(index, "지정") if 0 <= index < len(ports) else index

    if text.lower() != "auto":
        for index, name in enumerate(ports):
            if text.lower() in name.lower():
                return announce(index, f"이름 '{text}' 일치")
        if verbose:
            print(f"⚠️  이름에 '{text}' 가 든 MIDI 포트가 없습니다. "
                  f"0번을 씁니다.")
            for index, name in enumerate(ports):
                print(f"     [{index}] {name}")
        return 0

    # auto: 가상 포트를 뺀 첫 장치가 진짜 건반일 가능성이 높다.
    for index, name in enumerate(ports):
        if not any(v in name.lower() for v in _VIRTUAL_PORT_HINTS):
            return announce(index, "자동 탐지")

    if verbose:
        print("⚠️  진짜 건반을 찾지 못했습니다 (가상 포트만 보임). "
              "0번을 씁니다.")
        for index, name in enumerate(ports):
            print(f"     [{index}] {name}")

    return 0


def create_input_source(name, midi_port=0,
                        udp_host="0.0.0.0", udp_port=UDP_DEFAULT_PORT):
    """이름으로 입력 소스를 생성한다. (가능한 이름: INPUT_SOURCES)"""
    if name == "local":
        return LocalMidiInput(port=midi_port)
    if name == "udp":
        return PiUdpInput(host=udp_host, port=udp_port)
    raise ValueError(f"알 수 없는 입력 소스: {name!r} (가능: {INPUT_SOURCES})")
