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

# 프로그램 번호 0 = Acoustic Grand Piano (General MIDI)
DEFAULT_INSTRUMENT = 0

# 출력 포트 자동 선택 시 우선적으로 찾는 이름 조각 (소문자 비교)
_PREFERRED_PORT_KEYWORDS = (
    "microsoft gs wavetable",  # Windows 내장 신디사이저
    "fluid",                   # FluidSynth
    "iac",                     # macOS IAC 드라이버
    "synth",
)


class NotePlayer:
    """MIDI 출력 포트로 노트를 보내 소리를 내는 래퍼.

    출력 포트를 찾지 못하면 예외를 던지지 않고 '무음 모드'로 동작한다.
    (소리가 안 나도 판정 세션은 계속 진행돼야 하므로.)
    """

    def __init__(self, port=None, instrument=DEFAULT_INSTRUMENT, channel=0):
        self.channel = channel & 0x0F
        self._midi_out = rtmidi.MidiOut()
        self._opened = False

        port_index = self._resolve_port(port)
        if port_index is None:
            self._print_no_port_help()
            return

        self._midi_out.open_port(port_index)
        self._opened = True
        # 악기 선택 (Program Change)
        self._midi_out.send_message([0xC0 | self.channel, instrument & 0x7F])
        name = self._midi_out.get_port_name(port_index)
        print(f"🔊 소리 출력: {name}")

    # ── 포트 선택 ────────────────────────────────────────────────
    def _resolve_port(self, port):
        """포트 번호를 결정한다. port 가 None 이면 자동 탐색."""
        names = self._midi_out.get_ports()
        if not names:
            return None
        if port is not None:
            return port if 0 <= port < len(names) else None

        lowered = [n.lower() for n in names]
        for keyword in _PREFERRED_PORT_KEYWORDS:
            for i, name in enumerate(lowered):
                if keyword in name:
                    return i
        # 키워드에 안 걸리면 첫 포트 사용
        return 0

    @staticmethod
    def _print_no_port_help():
        print("🔇 MIDI 출력 포트가 없어 소리 없이 진행합니다.")
        if sys.platform == "darwin":
            print("   macOS: GarageBand 를 켜 두거나, Audio MIDI 설정에서")
            print("   IAC 드라이버를 활성화하면 소리를 낼 수 있습니다.")

    # ── 노트 입출력 ───────────────────────────────────────────────
    @property
    def enabled(self):
        return self._opened

    def note_on(self, note, velocity=100):
        if self._opened:
            self._midi_out.send_message(
                [0x90 | self.channel, note & 0x7F, velocity & 0x7F])

    def note_off(self, note):
        if self._opened:
            self._midi_out.send_message([0x80 | self.channel, note & 0x7F, 0])

    def all_notes_off(self):
        """울리고 있는 모든 음을 끈다 (CC 123)."""
        if self._opened:
            self._midi_out.send_message([0xB0 | self.channel, 123, 0])

    def close(self):
        if self._opened:
            self.all_notes_off()
            self._midi_out.close_port()
            self._opened = False


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
