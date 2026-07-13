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

사용 예:
    with create_input_source("udp") as source:
        event = source.poll()   # NoteEvent 또는 None
"""

import os
import sys
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


@dataclass(frozen=True)
class NoteEvent:
    """소스와 무관하게 정규화된 노트 이벤트."""
    is_on: bool        # True = note on, False = note off
    note: int          # MIDI 노트 번호
    velocity: int
    timestamp: float   # 소스 클럭 기준 타건 시각(초). 간격 계산에만 사용.


class MidiInputSource:
    """입력 소스 공통 인터페이스. 서브클래스가 poll/close 를 구현한다."""

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


class LocalMidiInput(MidiInputSource):
    """PC 에 직접 연결된 MIDI 키보드(rtmidi) 입력."""

    name = "local"

    def __init__(self, port=0):
        self._midi_in = rtmidi.MidiIn()
        ports = self._midi_in.get_ports()
        if not ports:
            self._midi_in.delete()
            raise RuntimeError(
                "로컬 MIDI 입력 장치가 없습니다. 키보드 연결을 확인하거나 "
                "입력 소스를 'udp'(라즈베리파이)로 바꿔주세요.")
        if not 0 <= port < len(ports):
            self._midi_in.delete()
            raise RuntimeError(
                f"MIDI 포트 {port}이 없습니다. 사용 가능: 0~{len(ports) - 1}")
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
        kind = status & 0xF0

        if kind == 0x90 and velocity > 0:
            return NoteEvent(True, note, velocity, time.time())
        if kind == 0x80 or (kind == 0x90 and velocity == 0):
            return NoteEvent(False, note, velocity, time.time())
        return None

    def close(self):
        self._midi_in.close_port()
        self._midi_in.delete()


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


def create_input_source(name, midi_port=0, udp_host="0.0.0.0",
                        udp_port=UDP_DEFAULT_PORT):
    """이름("local" | "udp")으로 입력 소스를 생성한다."""
    if name == "local":
        return LocalMidiInput(port=midi_port)
    if name == "udp":
        return PiUdpInput(host=udp_host, port=udp_port)
    raise ValueError(f"알 수 없는 입력 소스: {name!r} (가능: {INPUT_SOURCES})")
