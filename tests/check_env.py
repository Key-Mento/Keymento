# tests/check_env.py
import cv2
import mido
import os
import rtmidi
import sys
from importlib.metadata import version

# src/ 를 경로에 넣어야 midi.sound 를 임포트할 수 있다.
sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

print("=" * 50)
print("KeyMento 환경 체크")
print("=" * 50)
print(f"Python: {sys.version.split()[0]}")
print(f"OpenCV: {cv2.__version__}")
print(f"mido:   {version('mido')}")           # 이렇게 변경
print(f"rtmidi: {version('python-rtmidi')}")  # 이것도 추가

# 웹캠 확인
print("\n[웹캠]")
cap = cv2.VideoCapture(0, cv2.CAP_DSHOW)
if cap.isOpened():
    ret, frame = cap.read()
    if ret:
        h, w = frame.shape[:2]
        print(f"  ✓ OK ({w}x{h})")
    cap.release()
else:
    print("  ✗ 웹캠 인식 실패 — 다른 앱이 사용 중인지 확인")

# MIDI 장치 확인
print("\n[MIDI 입력 장치]")
midi_in = rtmidi.MidiIn()
ports = midi_in.get_ports()
if ports:
    for i, p in enumerate(ports):
        print(f"  [{i}] {p}")
else:
    print("  - 미연결 (지금은 OK, Phase 3에서 필요)")
del midi_in

# 소리 출력 확인 — 어느 백엔드로 얼마나 빠르게 나가는지
print("\n[소리 출력]")
from midi.sound import NotePlayer  # noqa: E402

player = NotePlayer()
print(f"  {'✓' if player.enabled else '✗'} {player.describe()}")
if player.backend == "midi":
    print("    (내장 신디보다 지연이 큽니다: pip install sounddevice)")
player.close()

print("\n" + "=" * 50)
print("환경 구축 완료")
print("=" * 50)