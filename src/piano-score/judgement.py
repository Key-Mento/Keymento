import mido
import rtmidi
import time

# MIDI 노트 번호 → 음이름 변환
NOTE_NAMES = ['도', '도#', '레', '레#', '미', '파', '파#', '솔', '솔#', '라', '라#', '시']

def note_to_name(note):
    return NOTE_NAMES[note % 12]

def get_answer_sheet(file_path):
    mid = mido.MidiFile(file_path)
    sheet = []
    absolute_time = 0.0
    for msg in mid:
        absolute_time += msg.time
        if msg.type == 'note_on' and msg.velocity > 0 and msg.note >= 60:
            sheet.append({'note': msg.note, 'time': absolute_time})
    return sheet

def main():
    filename = "head-shoulder-knee-and-toe.mid"
    answers = get_answer_sheet(filename)

    if not answers:
        print("정답지가 비어있습니다. MIDI 파일을 확인해주세요.")
        return

    # === 통계 변수 초기화 ===
    total_notes = len(answers)
    pitch_correct = 0
    pitch_wrong = 0
    timing_stats = {'Perfect': 0, 'Great': 0, 'Good': 0, 'Miss': 0}
    current_idx = 0

    midi_in = rtmidi.MidiIn()
    midi_in.open_port(0)

    print(f"\n🎵 총 {total_notes}개의 노트를 연주해야 합니다.")
    print("준비하세요! 20초 뒤 연주를 시작합니다...")
    for i in range(20, 0, -1):
        print(f"{i}...")
        time.sleep(1)

    print("\n🎹 [START] 연주 시작!")
    print(f"👉 첫 번째 목표 건반: {note_to_name(answers[0]['note'])}")

    start_time = time.time()

    # ── 핵심 변경: 직전 음이 실제로 눌린 시간 추적 ──────────────────
    # 첫 음은 곡 시작(start_time)을 기준으로 삼음
    last_actual_time = start_time

    try:
        while current_idx < total_notes:
            timer_event = midi_in.get_message()

            if timer_event:
                message, _ = timer_event
                status, note, velocity = message

                if status == 144 and velocity > 0:
                    actual_time = time.time()

                    target_note = answers[current_idx]['note']

                    # ── 1. 음정(Pitch) 판정 ──────────────────────────
                    if note == target_note:
                        pitch_correct += 1
                        pitch_msg = f"✅ [음정 O] {note_to_name(note)}"
                    else:
                        pitch_wrong += 1
                        pitch_msg = (f"❌ [음정 X] "
                                     f"입력:{note_to_name(note)} "
                                     f"정답:{note_to_name(target_note)}")

                    # ── 2. 박자(Timing) 판정 ─────────────────────────
                    # 각 음에 대한 독립 오차 계산:
                    #   실제 간격 = 이 음을 누른 시각 - 직전 음을 누른 시각
                    #   목표 간격 = MIDI 기준 이 음의 시각 - 직전 음의 시각
                    #   오차 = 실제 간격 - 목표 간격
                    actual_interval  = actual_time - last_actual_time

                    if current_idx == 0:
                        # 첫 음은 곡 시작부터의 절대 시간과 비교
                        target_interval = answers[0]['time']
                    else:
                        target_interval = (answers[current_idx]['time']
                                           - answers[current_idx - 1]['time'])

                    # 음수(-) = 빠름, 양수(+) = 늦음
                    time_diff_ms = (actual_interval - target_interval) * 1000
                    abs_diff_ms  = abs(time_diff_ms)
                    sign = (f"+{time_diff_ms:.0f}"
                            if time_diff_ms >= 0
                            else f"{time_diff_ms:.0f}")

                    # 임계값: 300 / 600 / 900 / 1200 ms
                    if abs_diff_ms <= 300:
                        timing_stats['Perfect'] += 1
                        timing_msg = f"✨ Perfect! ({sign}ms)"
                    elif abs_diff_ms <= 600:
                        timing_stats['Great'] += 1
                        timing_msg = f"👍 Great!  ({sign}ms)"
                    elif abs_diff_ms <= 900:
                        timing_stats['Good'] += 1
                        timing_msg = f"👌 Good    ({sign}ms)"
                    else:
                        timing_stats['Miss'] += 1
                        timing_msg = f"☁️ Miss    ({sign}ms)"

                    print(f"[{current_idx + 1}/{total_notes}] "
                          f"{pitch_msg}  |  {timing_msg}")

                    # 다음 음을 위해 직전 실제 타건 시각 갱신
                    last_actual_time = actual_time

                    current_idx += 1
                    if current_idx < total_notes:
                        print(f"👉 다음 목표: "
                              f"{note_to_name(answers[current_idx]['note'])}")

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

    except KeyboardInterrupt:
        print("\n연주가 중단되었습니다.")
    finally:
        midi_in.close_port()

if __name__ == "__main__":
    main()