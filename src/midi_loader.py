# src/midi_loader.py
from collections import defaultdict, deque

import mido


def load_score(midi_path):
    """
    MIDI 파일을 파싱하여 노트 시퀀스를 추출합니다.
    
    Returns:
        notes: [{note, time, velocity, duration}] 형태의 리스트
        bpm: 곡의 BPM
    """
    mid = mido.MidiFile(midi_path)
    
    # 기본 tempo (120 BPM = 500000 microseconds per beat)
    tempo = 500000
    
    notes = []
    note_on_dict = defaultdict(deque)  # 진행 중인 note 추적
    abs_time = 0.0
    
    # 모든 트랙을 병합해야 type 1 MIDI의 tempo track이 노트 시간에 올바르게 반영된다.
    for msg in mido.merge_tracks(mid.tracks):
        # tick 시간을 초 단위로 누적
        abs_time += mido.tick2second(msg.time, mid.ticks_per_beat, tempo)
        
        if msg.type == 'set_tempo':
            tempo = msg.tempo  # tempo 변경 반영
        
        elif msg.type == 'note_on' and msg.velocity > 0:
            # 노트 시작
            key = (getattr(msg, 'channel', 0), msg.note)
            note_on_dict[key].append({
                'note': msg.note,
                'time': abs_time,
                'velocity': msg.velocity
            })
        
        elif msg.type == 'note_off' or (msg.type == 'note_on' and msg.velocity == 0):
            # 노트 종료 — 시작 시각과 매칭하여 duration 계산
            key = (getattr(msg, 'channel', 0), msg.note)
            if note_on_dict[key]:
                start = note_on_dict[key].popleft()
                notes.append({
                    'note': start['note'],
                    'time': start['time'],
                    'velocity': start['velocity'],
                    'duration': abs_time - start['time']
                })
    
    # 시간순 정렬
    notes.sort(key=lambda n: n['time'])
    
    bpm = round(60_000_000 / tempo, 1)
    return notes, bpm


def midi_note_to_name(note_num):
    """MIDI 노트 번호를 이름으로 변환 (60 → C4)"""
    names = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B']
    octave = note_num // 12 - 1
    name = names[note_num % 12]
    return f"{name}{octave}"


# 직접 실행하면 테스트 모드
if __name__ == "__main__":
    import sys
    from collections import Counter
    
    midi_path = sys.argv[1] if len(sys.argv) > 1 else "songs/mario.mid"
    
    print(f"파일: {midi_path}")
    print("=" * 60)
    
    notes, bpm = load_score(midi_path)
    if not notes:
        print("노트가 없습니다")
        sys.exit()
    
    # 기본 정보
    print(f"BPM: {bpm}")
    print(f"총 노트 수: {len(notes)}")
    print(f"곡 길이: {notes[-1]['time'] + notes[-1]['duration']:.2f}초")
    print()
    
    # 사용된 음역대
    note_nums = [n['note'] for n in notes]
    print(f"음역대: {midi_note_to_name(min(note_nums))} ~ "
          f"{midi_note_to_name(max(note_nums))} "
          f"(MIDI {min(note_nums)} ~ {max(note_nums)})")
    
    # 사용된 노트 종류와 빈도
    note_counter = Counter(note_nums)
    print(f"고유 노트 수: {len(note_counter)}개")
    print()
    
    # 가장 많이 등장한 노트 Top 5
    print("가장 많이 등장한 노트 Top 5:")
    for n, count in note_counter.most_common(5):
        print(f"  {midi_note_to_name(n):>4} (MIDI {n}): {count}회")
    print()
    
    # 전체 노트 출력
    print("=" * 60)
    print("전체 노트 목록")
    print("=" * 60)
    print(f"{'#':>4}  {'시간(s)':>8}  {'노트':>5}  {'이름':>5}  "
          f"{'세기':>4}  {'길이(s)':>7}")
    print("-" * 60)
    
    for i, n in enumerate(notes, 1):
        print(f"{i:>4}  {n['time']:>8.3f}  {n['note']:>5}  "
              f"{midi_note_to_name(n['note']):>5}  "
              f"{n['velocity']:>4}  {n['duration']:>7.3f}")
    
    print("=" * 60)
    print(f"총 {len(notes)}개 노트 출력 완료")
