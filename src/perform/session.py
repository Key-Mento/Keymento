"""플레이 세션 오케스트레이션: '한 곡을 연주받아 판정을 끝내기까지'의
전 과정을 책임지는 계층.

■ 이 모듈의 역할
Keymento 의 다른 부품들은 각자 자기 일만 안다 — 판정 규칙
(piano-score/judgement.py)은 정답지와 등급 계산만, 입력 소스
(midi/inputs.py)는 건반 이벤트 수신만, 소리(midi/sound.py)는 에코 재생만,
설정(settings)은 곡/속도 값 보관만. 이들을 서로 연결하는 곳이 없으면
세션이 성립하지 않는데, 그 조립과 진행을 여기서 맡는다. 카운트다운 →
음별 판정 루프 → 최종 집계라는 한 세션의 생애 주기를 이 모듈이 소유하고,
진행 상황을 밖으로 알리는 것도 이 모듈의 책임이다.

즉 session.py 는 프론트엔드(웹 UI, CLI)와 저수준 부품들 사이의 경계다.
호출자는 이 모듈 위의 세부(판정 공식, 입력 프로토콜)를 몰라도 되고,
저수준 부품들은 자신이 어떤 UI 에서 쓰이는지 몰라도 된다.

■ 호출자와의 관계
  - webui/server.py 가 세션을 백그라운드 스레드로 띄울 때의 진입점.
    on_event 콜백으로 진행 이벤트(countdown/start/note/done/aborted)를
    받고, stop_event(threading.Event)를 set 해 세션을 중단시킨다.
  - `python session.py` 로 단독 실행도 가능하다(run_from_settings —
    Settings 가 고른 곡과 속도를 읽어 콘솔에서 판정을 돌린다).

■ 판정 동작의 요점
판정 자체는 기존 `piano-score/judgement.py` 를 건드리지 않고 그 함수
(`get_answer_sheet`, `note_to_name`)를 import 해서 재사용하며, main() 과
동일한 규칙(음정 일치, 음별 독립 간격 오차, ±300/600/900ms 임계값,
가중 점수)을 그대로 따른다. 그 위에 세 가지를 얹는다.
  1. 곡 선택   : Settings 가 고른 곡의 경로를 사용.
  2. 속도      : 판정 목표 간격을 (목표 간격 / speed) 로 스케일.
                 0.5x → 간격 2배(느리게 쳐도 정확), 2.0x → 절반(빠르게).
  3. 입력 소스 : 로컬 MIDI 키보드 또는 라즈베리파이 UDP 수신
                 (midi/inputs.py 의 MidiInputSource 로 추상화).

박자 판정은 이벤트가 '소스 클럭'으로 찍은 타건 시각(NoteEvent.timestamp)
간의 간격을 쓴다. UDP 소스는 라즈베리파이가 찍은 시각이 그대로 오므로
네트워크 지터가 판정 오차에 섞이지 않는다. 첫 음만은 소스 클럭 기준점이
없어 PC 도착 시각으로 판정한다(로컬 소스는 둘이 같다).
"""

import os
import sys
import time

# ── 기존 판정 모듈(piano-score/judgement.py) 재사용 ────────────────
# 하이픈이 들어간 폴더라 경로를 직접 추가한 뒤 import 한다.
_SRC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PIANO_SCORE_DIR = os.path.join(_SRC_DIR, "piano-score")
for _p in (_SRC_DIR, _PIANO_SCORE_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from judgement import get_answer_sheet, note_to_name  # noqa: E402
from settings import Settings                          # noqa: E402
from midi.inputs import LocalMidiInput, create_input_source  # noqa: E402
from midi.sound import NotePlayer                      # noqa: E402


def _emit(on_event, payload):
    """콜백 오류가 판정 세션을 죽이지 않도록 감싸서 호출한다."""
    if on_event is None:
        return
    try:
        on_event(payload)
    except Exception as exc:  # noqa: BLE001
        print(f"(on_event 콜백 오류 무시: {exc})")


def _grade(abs_diff_ms):
    """오차(ms 절대값) → (등급, 표시용 이모지)."""
    if abs_diff_ms <= 300:
        return "Perfect", "✨"
    if abs_diff_ms <= 600:
        return "Great", "👍"
    if abs_diff_ms <= 900:
        return "Good", "👌"
    return "Miss", "☁️"


def run_judgement(song_path, speed=1.0, port=0, countdown=20, sound=True,
                  input_source=None, on_event=None, stop_event=None):
    """곡을 연주받아 음정·박자를 판정한다. (judgement.main() 을 파라미터화)

    Args:
        song_path:    연주할 MIDI 파일 경로.
        speed:        속도 배율. 판정 목표 간격을 (목표 간격 / speed) 로 스케일.
        port:         input_source 미지정 시 사용할 로컬 rtmidi 포트 번호.
        countdown:    시작 전 카운트다운(초).
        sound:        True 면 누른 건반을 소프트웨어 신디사이저로 소리 낸다.
        input_source: MidiInputSource. None 이면 LocalMidiInput(port) 사용.
        on_event:     진행 이벤트 콜백. dict 하나를 받는다. (GUI 연동용)
        stop_event:   threading.Event. set 되면 세션을 중단한다.

    Returns:
        완주 시 결과 요약 dict, 중단/실패 시 None.
    """
    if speed <= 0:
        raise ValueError(f"속도는 0보다 커야 합니다: {speed}")

    answers = get_answer_sheet(song_path)

    if not answers:
        print("정답지가 비어있습니다. MIDI 파일을 확인해주세요.")
        return None

    # === 통계 변수 초기화 ===
    total_notes = len(answers)
    pitch_correct = 0
    pitch_wrong = 0
    timing_stats = {'Perfect': 0, 'Great': 0, 'Good': 0, 'Miss': 0}
    current_idx = 0

    own_source = input_source is None
    source = input_source or LocalMidiInput(port=port)

    # 컨트롤러(Keystation 등)는 자체 음원이 없으므로 소프트웨어
    # 신디사이저로 에코해서 소리를 낸다. 출력 포트가 없으면 무음 진행.
    player = NotePlayer() if sound else None

    def _stopped():
        return stop_event is not None and stop_event.is_set()

    print(f"\n🎵 총 {total_notes}개의 노트를 연주해야 합니다. (속도 {speed:g}x)")
    print(f"준비하세요! {countdown}초 뒤 연주를 시작합니다...")
    result = None

    try:
        for i in range(countdown, 0, -1):
            if _stopped():
                _emit(on_event, {"type": "aborted", "index": 0,
                                 "total": total_notes})
                return None
            print(f"{i}...")
            _emit(on_event, {"type": "countdown", "seconds": i})
            time.sleep(1)

        first_name = note_to_name(answers[0]['note'])
        print("\n🎹 [START] 연주 시작!")
        print(f"👉 첫 번째 목표 건반: {first_name}")
        _emit(on_event, {"type": "start", "total": total_notes,
                         "next": first_name})

        start_time = time.time()

        # ── 직전 음이 '소스 클럭'으로 눌린 시각 (간격 계산용) ────────
        last_note_ts = None

        while current_idx < total_notes:
            if _stopped():
                _emit(on_event, {"type": "aborted", "index": current_idx,
                                 "total": total_notes})
                return None

            event = source.poll()

            if event is not None:
                # ── 소리 에코: 판정과 무관하게 누른 건반을 그대로 재생 ──
                if player is not None:
                    if event.is_on:
                        player.note_on(event.note, event.velocity)
                    else:
                        player.note_off(event.note)

                if event.is_on:
                    note = event.note
                    target_note = answers[current_idx]['note']

                    # ── 1. 음정(Pitch) 판정 ──────────────────────────
                    if note == target_note:
                        pitch_correct += 1
                        pitch_ok = True
                        pitch_msg = f"✅ [음정 O] {note_to_name(note)}"
                    else:
                        pitch_wrong += 1
                        pitch_ok = False
                        pitch_msg = (f"❌ [음정 X] "
                                     f"입력:{note_to_name(note)} "
                                     f"정답:{note_to_name(target_note)}")

                    # ── 2. 박자(Timing) 판정 ─────────────────────────
                    # 각 음에 대한 독립 오차 계산:
                    #   실제 간격 = 이 음의 타건 시각 - 직전 음의 타건 시각
                    #               (같은 소스 클럭끼리의 차 → 지터 무관)
                    #   목표 간격 = MIDI 기준 간격 / speed  (속도 배율 적용)
                    #   오차 = 실제 간격 - 목표 간격
                    if current_idx == 0:
                        # 첫 음은 소스 클럭 기준점이 없어 곡 시작부터의
                        # PC 도착 시각으로 판정 (로컬 소스는 동일한 값)
                        actual_interval = time.time() - start_time
                        base_interval = answers[0]['time']
                    else:
                        actual_interval = event.timestamp - last_note_ts
                        base_interval = (answers[current_idx]['time']
                                         - answers[current_idx - 1]['time'])

                    # 속도 배율 적용: 느릴수록(speed<1) 목표 간격이 늘어난다
                    target_interval = base_interval / speed

                    # 음수(-) = 빠름, 양수(+) = 늦음
                    time_diff_ms = (actual_interval - target_interval) * 1000
                    abs_diff_ms = abs(time_diff_ms)
                    sign = (f"+{time_diff_ms:.0f}"
                            if time_diff_ms >= 0
                            else f"{time_diff_ms:.0f}")

                    # 임계값: 300 / 600 / 900 ms
                    grade, emoji = _grade(abs_diff_ms)
                    timing_stats[grade] += 1
                    timing_msg = f"{emoji} {grade:<7} ({sign}ms)"

                    print(f"[{current_idx + 1}/{total_notes}] "
                          f"{pitch_msg}  |  {timing_msg}")

                    # 다음 음을 위해 직전 타건 시각(소스 클럭) 갱신
                    last_note_ts = event.timestamp

                    current_idx += 1
                    next_name = (note_to_name(answers[current_idx]['note'])
                                 if current_idx < total_notes else None)
                    if next_name:
                        print(f"👉 다음 목표: {next_name}")

                    _emit(on_event, {
                        "type": "note",
                        "index": current_idx,          # 방금 판정된 음 (1부터)
                        "total": total_notes,
                        "pitch_ok": pitch_ok,
                        "played": note_to_name(note),
                        "expected": note_to_name(target_note),
                        "grade": grade,
                        "diff_ms": round(time_diff_ms),
                        "next": next_name,
                    })

            time.sleep(0.001)

        # === 최종 결과 통계 산출 ===
        print("\n" + "=" * 50)
        print("🎉 곡 완주! 최종 분석 결과를 확인하세요 🎉")
        print("=" * 50)

        pitch_accuracy = (pitch_correct / total_notes) * 100

        timing_score_total = (timing_stats['Perfect'] * 100
                              + timing_stats['Great']  * 80
                              + timing_stats['Good']   * 50)
        timing_accuracy = timing_score_total / total_notes

        overall_accuracy = (pitch_accuracy + timing_accuracy) / 2

        print(f"🎵 전체 건반 수: {total_notes}개")
        print("-" * 50)
        print(f"🎹 [음계 분석] 정확도: {pitch_accuracy:.1f}%")
        print(f"   - 정답 건반: {pitch_correct}개")
        print(f"   - 오답 건반: {pitch_wrong}개")
        print("-" * 50)
        print(f"⏱️  [박자 분석] 정확도: {timing_accuracy:.1f}%")
        print(f"   - ✨ Perfect (±300ms 이내) : {timing_stats['Perfect']}개")
        print(f"   - 👍 Great  (±600ms 이내) : {timing_stats['Great']}개")
        print(f"   - 👌 Good   (±900ms 이내) : {timing_stats['Good']}개")
        print(f"   - ☁️  Miss  (±900ms 초과) : {timing_stats['Miss']}개")
        print("=" * 50)
        print(f"🏆 [최종 종합 점수]: {overall_accuracy:.1f} 점 / 100 점")
        print("=" * 50)

        result = {
            "total": total_notes,
            "pitch_correct": pitch_correct,
            "pitch_wrong": pitch_wrong,
            "timing": dict(timing_stats),
            "pitch_accuracy": round(pitch_accuracy, 1),
            "timing_accuracy": round(timing_accuracy, 1),
            "overall": round(overall_accuracy, 1),
        }
        _emit(on_event, {"type": "done", "result": result})
        return result

    except KeyboardInterrupt:
        print("\n연주가 중단되었습니다.")
        _emit(on_event, {"type": "aborted", "index": current_idx,
                         "total": total_notes})
        return None
    finally:
        if player is not None:
            player.close()
        if own_source:
            source.close()


def run_from_settings(settings=None, **kwargs):
    """설정(곡 선택 + 속도)을 읽어 판정을 실행한다.

    선택된 곡이 없으면 목록의 첫 곡을 사용한다. GUI 는 원하는 대로
    Settings 를 구성해 넘기거나, 직접 run_judgement 를 호출하면 된다.
    kwargs 는 run_judgement 로 그대로 전달된다(input_source 등).
    """
    settings = settings or Settings()

    song = settings.get_selected_song()
    if song is None:
        songs = settings.list_songs()
        if not songs:
            print("곡을 찾을 수 없습니다. songs/ 폴더를 확인하세요.")
            return None
        song = songs[0]
        print(f"선택된 곡이 없어 기본 곡을 사용합니다: {song.name}")

    print(f"🎼 곡: {song.name}  |  ⏩ 속도: {settings.speed:g}x"
          f"  |  🎹 입력: {settings.input_source}")

    if "input_source" in kwargs:
        return run_judgement(song.path, speed=settings.speed, **kwargs)

    # 설정이 고른 소스(local/udp)를 만들어 세션에 넘긴다.
    with create_input_source(settings.input_source) as source:
        return run_judgement(song.path, speed=settings.speed,
                             input_source=source, **kwargs)


if __name__ == "__main__":
    run_from_settings()
