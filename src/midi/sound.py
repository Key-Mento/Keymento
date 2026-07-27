"""건반 입력을 소리로 되돌려주는 출력 계층.

Keystation Mini32 같은 MIDI 컨트롤러는 자체 음원이 없어 건반을 눌러도
소리가 나지 않는다. 이 모듈은 받은 노트 이벤트를 실제 소리로 바꾼다.

■ 백엔드 두 가지
  1. **synth** (기본) — `synth.py` 의 SoftSynth 가 파형을 직접 만들어
     오디오 장치로 보낸다. OS 신디사이저에 의존하지 않으므로
     Windows·macOS 어디서든 똑같이 소리가 나고, 블록 크기를 직접 잡아
     지연이 작다(약 10ms 안쪽).
  2. **midi** (폴백) — OS 의 MIDI 출력 포트로 노트를 흘려보낸다.
     Windows 는 "Microsoft GS Wavetable Synth" 가 내장되어 있지만 출력
     지연이 크고, macOS 는 IAC 드라이버/GarageBand 를 따로 켜지 않으면
     포트 자체가 없다. 오디오 장치를 못 열 때만 쓴다.

`backend="auto"` 는 synth → midi → 무음 순으로 시도한다. 환경변수
`KEYMENTO_SOUND` 에 synth / midi / none 을 넣으면 강제할 수 있다.
어느 경로로도 소리를 못 내면 예외 없이 '무음 모드'로 동작한다 —
소리가 안 난다고 판정 세션까지 죽으면 안 되기 때문이다.

사용 예:
    player = NotePlayer()
    player.note_on(60, 100)   # 도(C4) 소리
    player.note_off(60)
    player.close()
"""

import os
import sys
import time

import rtmidi

try:
    from .synth import SoftSynth, is_available as synth_available
except ImportError:     # `python src\midi\sound.py` 처럼 직접 실행할 때
    from synth import SoftSynth, is_available as synth_available

# 악기 종류
DEFAULT_INSTRUMENT = 0          # General MIDI 프로그램 0번 = Acoustic Grand Piano

BACKENDS = ("auto", "synth", "midi", "none")
DEFAULT_BACKEND = "auto"
_BACKEND_ENV = "KEYMENTO_SOUND"

# ── MIDI 프로토콜 상수 ──────────────────────────────────────────────
# 상태 바이트: 상위 4비트가 메시지 종류, 하위 4비트가 채널 번호.
_NOTE_ON = 0x90
_NOTE_OFF = 0x80
_CONTROL_CHANGE = 0xB0
_PROGRAM_CHANGE = 0xC0

_CC_ALL_NOTES_OFF = 123         # Control Change 123번 = All Notes Off

_CHANNEL_MASK = 0x0F            # 채널은 0~15
_DATA_MASK = 0x7F               # 데이터 바이트(노트/벨로시티/악기)는 7비트

# 출력 포트 자동 선택 시 우선적으로 찾는 이름 조각 (소문자 비교)
_PREFERRED_PORT_KEYWORDS = (
    "microsoft gs wavetable",   # Windows 내장 신디사이저
    "fluid",                    # FluidSynth
    "iac",                      # macOS IAC 드라이버
    "synth",
)


class _MidiOutBackend:
    """OS 의 MIDI 출력 포트로 노트를 보내는 백엔드.

    포트를 못 찾으면 __init__ 에서 RuntimeError 를 던진다.
    """

    name = "midi"

    def __init__(self, port=None, instrument=DEFAULT_INSTRUMENT, channel=0):
        self.channel = channel & _CHANNEL_MASK
        self._midi_out = rtmidi.MidiOut()

        port_index = self._resolve_port(port)
        if port_index is None:
            self._midi_out.delete()
            raise RuntimeError("MIDI 출력 포트가 없습니다.")

        self._midi_out.open_port(port_index)
        self.port_name = self._midi_out.get_port_name(port_index)
        self._send(_PROGRAM_CHANGE, instrument)         # 악기 선택

    def _resolve_port(self, port):
        """열 포트 번호를 결정한다. port 가 None 이면 이름으로 자동 탐색."""
        names = self._midi_out.get_ports()
        if not names:
            return None
        if port is not None:
            return port if 0 <= port < len(names) else None

        lowered = [name.lower() for name in names]
        for keyword in _PREFERRED_PORT_KEYWORDS:
            for index, name in enumerate(lowered):
                if keyword in name:
                    return index
        return 0                                        # 키워드에 안 걸리면 첫 포트

    def note_on(self, note, velocity=100):
        self._send(_NOTE_ON, note, velocity)

    def note_off(self, note):
        self._send(_NOTE_OFF, note, 0)

    def all_notes_off(self):
        self._send(_CONTROL_CHANGE, _CC_ALL_NOTES_OFF, 0)

    def close(self):
        self.all_notes_off()
        self._midi_out.close_port()
        self._midi_out.delete()

    def _send(self, status, data1, data2=None):
        """채널/7비트 마스킹을 한곳에서 처리한다."""
        message = [status | self.channel, data1 & _DATA_MASK]
        if data2 is not None:
            message.append(data2 & _DATA_MASK)
        self._midi_out.send_message(message)


class NotePlayer:
    """노트를 소리로 바꾸는 래퍼. 백엔드 선택과 무음 폴백을 담당한다.

    백엔드를 하나도 열지 못해도 예외를 던지지 않고 '무음 모드'로
    동작한다 — note_on/note_off 호출을 그대로 받되 아무 소리도 내지 않는다.
    """

    def __init__(self, backend=None, port=None,
                 instrument=DEFAULT_INSTRUMENT, channel=0):
        self._impl = None
        self.backend = "none"

        requested = (backend or os.environ.get(_BACKEND_ENV)
                     or DEFAULT_BACKEND).lower()
        if requested not in BACKENDS:
            print(f"⚠️  알 수 없는 소리 백엔드 {requested!r} → auto 로 진행합니다.")
            requested = "auto"

        if requested == "none":
            print("🔇 소리 출력이 꺼져 있습니다 (backend=none).")
            return

        errors = []
        for candidate in self._candidates(requested):
            try:
                self._impl = self._create(candidate, port, instrument, channel)
            except Exception as exc:   # noqa: BLE001 — 다음 후보로 넘어간다
                errors.append(f"{candidate}: {exc}")
                continue
            self.backend = candidate
            self._print_ready()
            return

        self._print_no_output(errors)

    # ── 공개 API: 연주 ───────────────────────────────────────────
    @property
    def enabled(self):                                  # 소리가 나는 상태인가 (False 면 무음 모드).
        return self._impl is not None

    def note_on(self, note, velocity=100):              # 소리를 울림
        if self._impl is not None:
            self._impl.note_on(note, velocity)

    def note_off(self, note):                           # 소리를 끈다.
        if self._impl is not None:
            self._impl.note_off(note)

    def all_notes_off(self):                            # 울리고 있는 모든 음을 끈다.
        if self._impl is not None:
            self._impl.all_notes_off()

    def close(self):                                    # 울리는 음을 정리하고 백엔드를 닫는다.
        if self._impl is None:
            return
        impl, self._impl = self._impl, None
        try:
            impl.all_notes_off()
        finally:
            impl.close()

    def describe(self):
        """지금 소리가 어디로 어떻게 나가는지 한 줄로 설명한다 (진단용)."""
        if self._impl is None:
            return "무음 — 소리 출력 없음"
        if self.backend == "synth":
            return (f"내장 신디 → {self._impl.device_name} "
                    f"(지연 약 {self._impl.latency_ms:.0f}ms)")
        return f"{self._impl.port_name} (MIDI 포트)"

    # ── 내부: 백엔드 선택 ────────────────────────────────────────
    @staticmethod
    def _candidates(requested):
        """시도할 백엔드 순서. auto 는 synth 를 먼저 본다."""
        if requested == "auto":
            return ("synth", "midi")
        return (requested,)

    @staticmethod
    def _create(name, port, instrument, channel):
        if name == "synth":
            return SoftSynth()
        return _MidiOutBackend(port=port, instrument=instrument,
                               channel=channel)

    def _print_ready(self):
        print(f"🔊 소리 출력: {self.describe()}")
        if self.backend == "midi":
            print("   지연이 크게 느껴지면 내장 신디를 쓰세요: "
                  "pip install sounddevice")

    @staticmethod
    def _print_no_output(errors):
        print("🔇 소리를 낼 수 없어 무음으로 진행합니다.")
        for line in errors:
            print(f"   - {line}")
        if not synth_available():
            print("   내장 신디를 쓰려면: pip install sounddevice")
        elif sys.platform == "darwin":
            print("   macOS: 시스템 설정에서 이 터미널의 마이크/오디오 권한과")
            print("   출력 장치가 잡혀 있는지 확인해 주세요.")


def list_output_ports():
    """사용 가능한 MIDI 출력 포트 목록을 반환한다."""
    return rtmidi.MidiOut().get_ports()


if __name__ == "__main__":
    # 간단 동작 확인: 도레미파솔 재생
    print("MIDI 출력 포트 목록:", list_output_ports())
    player = NotePlayer()
    print("선택된 백엔드:", player.backend)
    if player.enabled:
        for n in (60, 62, 64, 65, 67):
            player.note_on(n)
            time.sleep(0.4)
            player.note_off(n)
        time.sleep(0.3)
    player.close()
