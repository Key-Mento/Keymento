"""건반 입력을 소리로 되돌려주는 소프트웨어 신디사이저 출력.

Keystation Mini32 같은 MIDI 컨트롤러는 자체 음원이 없어 건반을 눌러도
소리가 나지 않는다. 이 모듈은 받은 노트 이벤트를 OS 내장 소프트웨어
신디사이저의 MIDI 출력 포트로 그대로 흘려보내 소리를 낸다.

- Windows : "Microsoft GS Wavetable Synth" 가 기본 내장 → 별도 설치 불필요.
- macOS   : 기본 MIDI 출력 포트가 없어서 IAC 드라이버 활성화 후 GarageBand
            등으로 받거나, FluidSynth/SimpleSynth 같은 소프트 신디를 띄워야
            포트가 생긴다. 포트가 없으면 소리 없이(판정만) 진행한다.

사용 예:
    player = NotePlayer()
    player.note_on(60, 100)   # 도(C4) 소리
    player.note_off(60)
    player.close()
"""

import sys
import time

import rtmidi
# 악기 종류
DEFAULT_INSTRUMENT = 0          # General MIDI 프로그램 0번 = Acoustic Grand Piano

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


class NotePlayer:
    """MIDI 출력 포트로 노트를 보내 소리를 내는 래퍼.

    출력 포트를 찾지 못해도 예외를 던지지 않고 '무음 모드'로 동작한다 —
    note_on/note_off 호출을 그대로 받되 아무 소리도 내지 않는다.
    """

    def __init__(self, port=None, instrument=DEFAULT_INSTRUMENT, channel=0):
        self.channel = channel & _CHANNEL_MASK
        self._midi_out = rtmidi.MidiOut()
        self._opened = False

        port_index = self._resolve_port(port)
        if port_index is None:
            self._print_no_port_help()
            return
        self._open(port_index, instrument)

    # ── 공개 API: 연주 ───────────────────────────────────────────
    @property
    def enabled(self):                                  # 소리가 나는 상태인가 (False 면 무음 모드).
        return self._opened

    def note_on(self, note, velocity=100):              # 소리를 울림
        self._send(_NOTE_ON, note, velocity)

    def note_off(self, note):                           # 소리를 끈다.
        self._send(_NOTE_OFF, note, 0)

    def all_notes_off(self):                            # 울리고 있는 모든 음을 끈다.
        self._send(_CONTROL_CHANGE, _CC_ALL_NOTES_OFF, 0)

    def close(self):                                    # 울리는 음을 정리하고 포트를 닫는다. 이후 호출은 무음 처리.
        if not self._opened:
            return
        self.all_notes_off()
        self._midi_out.close_port()
        self._opened = False

    # ── 내부: 포트 선택과 열기 (__init__ 전용) ───────────────────
    def _resolve_port(self, port):                      # 열 포트 번호를 결정한다. port 가 None 이면 이름으로 자동 탐색.
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
        return 0                                        # 키워드에 안 걸리면 첫 포트 사용

    def _open(self, port_index, instrument):            # 준비되었으면 열기
        self._midi_out.open_port(port_index)
        self._opened = True
        self._send(_PROGRAM_CHANGE, instrument)         # 악기 선택
        print(f"🔊 소리 출력: {self._midi_out.get_port_name(port_index)}")

    @staticmethod
    def _print_no_port_help():
        print("🔇 MIDI 출력 포트가 없어 소리 없이 진행합니다.")
        if sys.platform == "darwin":
            print("   macOS: GarageBand 를 켜 두거나, Audio MIDI 설정에서")
            print("   IAC 드라이버를 활성화하면 소리를 낼 수 있습니다.")

    # ── 내부: MIDI 메시지 전송 ───────────────────────────────────
    def _send(self, status, data1, data2=None):
        """무음 모드 무시 + 채널/7비트 마스킹을 한곳에서 처리한다."""
        if not self._opened:
            return
        message = [status | self.channel, data1 & _DATA_MASK]
        if data2 is not None:
            message.append(data2 & _DATA_MASK)
        self._midi_out.send_message(message)


def list_output_ports():
    """사용 가능한 MIDI 출력 포트 목록을 반환한다."""
    return rtmidi.MidiOut().get_ports()


if __name__ == "__main__":
    # 간단 동작 확인: 도레미파솔 재생
    print("출력 포트 목록:", list_output_ports())
    player = NotePlayer()
    if player.enabled:
        for n in (60, 62, 64, 65, 67):
            player.note_on(n)
            time.sleep(0.4)
            player.note_off(n)
    player.close()
