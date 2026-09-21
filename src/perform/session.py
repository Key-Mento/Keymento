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
  - gui/server.py 가 세션을 백그라운드 스레드로 띄울 때의 진입점.
    on_event 콜백으로 진행 이벤트(countdown/start/note/done/aborted)를
    받고, stop_event(threading.Event)를 set 해 세션을 중단시킨다.
  - `python session.py` 로 단독 실행도 가능하다(run_from_settings —
    Settings 가 고른 곡과 속도를 읽어 콘솔에서 판정을 돌린다).
    건반이 0번 포트가 아니면 `--midi-port` 로 지정한다:
        python src/perform/session.py --list-ports
        python src/perform/session.py --midi-port 2 --practice

■ 판정 동작의 요점
판정 자체는 기존 `piano-score/judgement.py` 를 건드리지 않고 그 함수
(`get_answer_sheet`, `note_to_name`)를 import 해서 재사용하며, 등급
임계값(±300/600/900ms)과 가중 점수는 main() 과 같다. 그 위에 네 가지를
얹는다.
  1. 곡 선택   : Settings 가 고른 곡의 경로를 사용.
  2. 속도      : 악보상 시각을 (시각 / speed) 로 스케일.
                 0.5x → 간격 2배(느리게 쳐도 정확), 2.0x → 절반(빠르게).
  3. 입력 소스 : 로컬 MIDI 키보드 또는 라즈베리파이 UDP 수신
                 (midi/inputs.py 의 MidiInputSource 로 추상화).
  4. 화음      : 정답지는 note_on 을 늘어놓은 1차원 배열이라 화음도 그냥
                 나란히 들어간다. 한 음씩 대조하면 화음을 파일에 적힌
                 순서대로 쳐야만 통과하는데, 동시에 누른 음의 도착 순서는
                 매번 다르다. 그래서 같은 시각의 음들을 한 덩어리로 묶어
                 (judgement.group_answers) 그 안에서는 순서를 따지지 않고,
                 박자도 덩어리 하나를 한 박으로 매긴다.

타건 시각은 이벤트가 '소스 클럭'으로 찍은 값(NoteEvent.timestamp)을
SourceClock 으로 PC 시계에 옮겨 쓴다. UDP 소스는 라즈베리파이가 찍은
시각이 그대로 오므로 네트워크 지터가 판정 오차에 섞이지 않는다.

■ 두 가지 모드
  - 일반 모드(practice=False) : 리듬게임처럼 '시각'으로 판정한다
    (TimedJudge). 음마다 목표 시각 ±0.9초의 판정 창이 있고, 타건은 창이
    열린 같은 음에 붙는다. 틀린 타건은 어떤 음도 소진하지 않으므로
    잘못 여러 번 쳐도 판정 위치가 밀리지 않는다. 창이 닫힐 때까지 못 친
    음은 Miss 다.
  - 연습 모드(practice=True)  : 정답 건반을 칠 때까지 같은 음에 머무른다.
    악보 시계로 인한 자동 진행이 없다. 기다리는 만큼 간격이 늘어나
    박자 판정은 의미가 없어지므로 아예 하지 않고, 점수는 음정만으로
    낸다. 틀린 타건은 오답으로 기록하되(그 음의 첫 실패만 정확도에
    반영) 진행은 시키지 않는다.
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

from judgement import (  # noqa: E402
    get_answer_sheet,
    group_answers,
    note_to_name,
)
from settings import Settings                          # noqa: E402
from midi.inputs import (  # noqa: E402
    LocalMidiInput,
    MidiSystemStuck,
    UDP_DEFAULT_PORT,
    create_input_source,
    list_midi_ports,
    midi_stuck_help,
    resolve_midi_port,
)
from midi.sound import NotePlayer                      # noqa: E402


def load_judgement_notes(song_path):
    """Return AR note events from the exact answer sheet used for judgement."""
    answers = get_answer_sheet(song_path)
    notes = []

    for index, answer in enumerate(answers):
        if index + 1 < len(answers):
            duration = max(answers[index + 1]["time"] - answer["time"], 0.25)
        else:
            duration = 0.5
        notes.append({
            "note": answer["note"],
            "time": answer["time"],
            "duration": duration,
        })

    return notes


def _emit(on_event, payload):
    """콜백 오류가 판정 세션을 죽이지 않도록 감싸서 호출한다."""
    if on_event is None:
        return
    try:
        on_event(payload)
    except Exception as exc:  # noqa: BLE001
        print(f"(on_event 콜백 오류 무시: {exc})")


def _names(notes):
    """여러 음을 한 덩어리로 읽히게 잇는다 ('도4+미4+솔4'). 비면 None."""
    return "+".join(note_to_name(n) for n in notes) if notes else None


# 일반 모드의 판정 창(초). 목표 시각 ± 이 안에 친 음만 그 음으로 인정한다.
# Good 등급의 경계(900ms)와 같다 — 창 안에 치면 최소 Good 이다.
HIT_WINDOW = 0.9

# 창이 닫힌 뒤 Miss 로 확정하기까지 더 기다리는 시간(초). 판정은 타건
# 시각으로 하지만 이벤트는 그보다 늦게 도착한다(UDP 지연, 폴링 간격).
# 창 끝자락에 친 음이 도착하기 전에 Miss 로 먼저 처리되지 않게 한다.
ARRIVAL_GRACE = 0.2


def _grade(abs_diff_ms):
    """오차(ms 절대값) → (등급, 표시용 이모지)."""
    if abs_diff_ms <= 300:
        return "Perfect", "✨"
    if abs_diff_ms <= 600:
        return "Great", "👍"
    if abs_diff_ms <= 900:
        return "Good", "👌"
    return "Miss", "☁️"


class SourceClock:
    """소스 클럭(NoteEvent.timestamp)을 PC 시계(time.time())로 옮긴다.

    UDP 소스의 타임스탬프는 라즈베리파이 클럭이라 PC 시계와 기준점이
    다르다. (도착 시각 - 타건 시각)의 최솟값을 두 클럭의 차로 본다 —
    지연이 가장 적었던 이벤트가 실제 차에 가장 가깝다. 도착 시각이 아니라
    이렇게 옮긴 타건 시각으로 판정하므로 Wi-Fi 지터가 섞이지 않는다.
    로컬 소스는 두 클럭이 같아 차가 0 에 가깝다.
    """

    def __init__(self):
        self._offset = None

    def to_local(self, timestamp, arrived_at):
        offset = arrived_at - timestamp
        if self._offset is None or offset < self._offset:
            self._offset = offset
        return timestamp + self._offset


class TimedJudge:
    """일반 모드 판정 — 타건을 '순서'가 아니라 '시각'으로 음에 붙인다.

    순서로 대조하면 틀린 타건도 목표 음을 하나씩 소진한다. 한순간에 두 번
    잘못 치면 지금 음과 다음 음이 함께 넘어가고, 그 뒤로는 제때 친 음도
    엉뚱한 목표와 대조된다.

    그래서 리듬게임처럼 음마다 목표 시각 ±HIT_WINDOW 의 판정 창을 둔다.
      - 타건은 창이 열린 같은 음 중 가장 이른 음에 붙고, 박자 등급은 목표
        시각과의 차이로 매긴다. 화음은 덩어리의 첫 타건에서 한 번만 매긴다.
      - 붙을 음이 없으면 오타다. 어떤 음도 소진하지 않는다.
      - 창이 닫힐 때까지 치지 않은 음은 Miss 다.
    같은 음이 곧 다시 나오면 두 창이 겹치므로 경계를 좁힌다.
      - 앞 음의 창은 뒤 음의 목표 시각에 닫는다 — 앞 음을 건너뛰고 뒤 음을
        제때 친 것을 앞 음으로 오인하지 않게 한다.
      - 뒤 음의 창은 두 목표 시각의 중간부터 열린다 — 옆 건반을 잘못 친 것이
        마침 뒤 음이라 그 음을 미리 가져가면, 제때 친 뒤 음이 그다음 같은
        음을 또 미리 가져가며 줄줄이 밀린다. 늦게 치는 쪽은 앞 음의 창이
        뒤 음 시각까지 열려 있어 그대로 받아 준다.

    Args:
        answers:    get_answer_sheet() 결과.
        groups:     group_answers() 결과.
        speed:      속도 배율. 목표 시각을 (악보상 시각 / speed) 로 스케일.
        start_time: 곡 시작 시각(PC 시계).
        emit:       진행 이벤트 dict 를 받는 함수.
    """

    def __init__(self, answers, groups, speed, start_time, emit):
        self._start = start_time
        self._emit = emit
        self._targets = [answers[start]["time"] / speed
                         for start, _ in groups]
        self._remaining = [[answer["note"] for answer in answers[start:end]]
                           for start, end in groups]
        self._opens, self._closes = self._windows()
        self._grades = [None] * len(groups)    # 덩어리별 (등급, 오차 ms)
        self._head = 0                         # 아직 안 끝난 첫 덩어리
        self._last_hit = 0                     # 마지막으로 친 덩어리

        self.total_notes = len(answers)
        self.done_notes = 0         # 판정이 끝난 음 수 (진행률 표시용)
        self.pitch_correct = 0
        self.pitch_wrong = 0        # 창 안에 치지 못한 음
        self.wrong_attempts = 0     # 어떤 음에도 붙지 못한 타건(오타)
        self.timing_stats = {"Perfect": 0, "Great": 0, "Good": 0, "Miss": 0}

    def _windows(self):
        """덩어리별 {음: 판정 창이 열리는 시각}, {음: 닫히는 시각}."""
        opens = [{} for _ in self._remaining]
        closes = [{} for _ in self._remaining]
        last_group = {}
        for index, notes in enumerate(self._remaining):
            target = self._targets[index]
            for note in notes:
                opens[index][note] = target - HIT_WINDOW
                closes[index][note] = target + HIT_WINDOW
                prev = last_group.get(note)
                if prev is not None:
                    prev_target = self._targets[prev]
                    opens[index][note] = max(opens[index][note],
                                             (prev_target + target) / 2)
                    closes[prev][note] = min(closes[prev][note], target)
                last_group[note] = index
        return opens, closes

    @property
    def finished(self):
        return self._head >= len(self._remaining)

    def next_notes(self):
        """다음에 칠 음 — 마지막으로 친 덩어리부터 아직 안 끝난 첫 덩어리.

        놓친 음은 창이 닫힐 때까지 남아 있지만, 연주자가 이미 그 뒤를
        치고 있으면 다음 목표로 보여 주지 않는다.
        """
        start = max(self._head, self._last_hit)
        for index in range(start, len(self._remaining)):
            if self._remaining[index]:
                return list(self._remaining[index])
        return []

    def press(self, note, pressed_at):
        """타건 하나를 판정한다. pressed_at 은 PC 시계 기준 타건 시각."""
        elapsed = pressed_at - self._start
        index = self._open_group_for(note, elapsed)
        if index is None:
            self._wrong(note, elapsed)
        else:
            self._hit(index, note, elapsed)

    def expire(self, now):
        """창이 닫혔는데 치지 않은 음을 Miss 로 확정한다."""
        elapsed = now - self._start
        for index in range(self._head, len(self._remaining)):
            if self._targets[index] >= elapsed:
                break
            remaining = self._remaining[index]
            if not remaining:
                continue
            closes_at = max(self._closes[index][note] for note in remaining)
            if elapsed > closes_at + ARRIVAL_GRACE:
                self._miss(index, elapsed)

    # ── 내부 ─────────────────────────────────────────────────────
    def _open_group_for(self, note, elapsed):
        for index in range(self._head, len(self._remaining)):
            if self._targets[index] - HIT_WINDOW > elapsed:
                break
            if (note in self._remaining[index]
                    and self._opens[index][note] <= elapsed
                    <= self._closes[index][note]):
                return index
        return None

    def _nearest_open_group(self, elapsed):
        """오타 표시용 — 그 시각에 쳤어야 할 덩어리. 없으면 None."""
        best, best_gap = None, None
        for index in range(self._head, len(self._remaining)):
            gap = abs(self._targets[index] - elapsed)
            if best_gap is not None and gap >= best_gap:
                if self._targets[index] > elapsed:
                    break           # 이후 덩어리는 더 멀다
                continue
            if self._remaining[index]:
                best, best_gap = index, gap
        return best

    def _advance_head(self):
        while not self.finished and not self._remaining[self._head]:
            self._head += 1

    def _hit(self, index, note, elapsed):
        expected_notes = list(self._remaining[index])
        self._remaining[index].remove(note)
        self._last_hit = max(self._last_hit, index)
        self.pitch_correct += 1
        self.done_notes += 1

        # 박자는 덩어리의 첫 타건에서 한 번만 매기고, 화음의 나머지 음은
        # 그 등급을 그대로 쓴다 — 동시에 친 음들은 한 박이다.
        if self._grades[index] is None:
            diff_ms = (elapsed - self._targets[index]) * 1000
            grade, _ = _grade(abs(diff_ms))
            self._grades[index] = (grade, diff_ms)
            self.timing_stats[grade] += 1
        grade, diff_ms = self._grades[index]
        _, emoji = _grade(abs(diff_ms))

        self._advance_head()
        next_notes = self.next_notes()

        sign = f"+{diff_ms:.0f}" if diff_ms >= 0 else f"{diff_ms:.0f}"
        print(f"[{self.done_notes}/{self.total_notes}] "
              f"✅ [음정 O] {note_to_name(note)}  |  "
              f"{emoji} {grade:<7} ({sign}ms)")
        self._emit({
            "type": "note",
            "index": self.done_notes,       # 판정이 끝난 음 수 (1부터)
            "total": self.total_notes,
            "pitch_ok": True,
            "played": note_to_name(note),
            "played_note": note,
            "expected": _names(expected_notes),
            "expected_notes": expected_notes,
            "grade": grade,
            "diff_ms": round(diff_ms),
            "next": _names(next_notes),
            "next_notes": next_notes,
        })

    def _wrong(self, note, elapsed):
        self.wrong_attempts += 1
        nearest = self._nearest_open_group(elapsed)
        expected_notes = ([] if nearest is None
                          else list(self._remaining[nearest]))
        next_notes = self.next_notes()

        print(f"[{self.done_notes}/{self.total_notes}] "
              f"❌ [오타] 입력:{note_to_name(note)} "
              f"정답:{_names(expected_notes) or '-'}")
        self._emit({
            "type": "note",
            "index": self.done_notes,
            "total": self.total_notes,
            "pitch_ok": False,
            "played": note_to_name(note),
            "played_note": note,
            "expected": _names(expected_notes),
            "expected_notes": expected_notes,
            "grade": None,
            "diff_ms": None,
            "next": _names(next_notes),
            "next_notes": next_notes,
            "wrong": True,
        })

    def _miss(self, index, elapsed):
        missed_notes = self._remaining[index]
        self._remaining[index] = []
        self.pitch_wrong += len(missed_notes)
        self.done_notes += len(missed_notes)

        # 화음 일부를 쳤으면 박자는 이미 매겼다 — 한 음도 못 친 덩어리만 Miss.
        diff_ms = (elapsed - self._targets[index]) * 1000
        if self._grades[index] is None:
            self._grades[index] = ("Miss", diff_ms)
            self.timing_stats["Miss"] += 1

        self._advance_head()
        next_notes = self.next_notes()

        print(f"[{self.done_notes}/{self.total_notes}] "
              f"MISSED {_names(missed_notes)} (no input, +{diff_ms:.0f}ms)")
        self._emit({
            "type": "note",
            "index": self.done_notes,
            "total": self.total_notes,
            "pitch_ok": False,
            "played": "-",
            "played_note": None,
            "expected": _names(missed_notes),
            "expected_notes": missed_notes,
            "grade": "Miss",
            "diff_ms": round(diff_ms),
            "next": _names(next_notes),
            "next_notes": next_notes,
            "timed_out": True,
        })


def run_judgement(song_path, speed=1.0, port=0, countdown=20, sound=True,
                  input_source=None, on_event=None, stop_event=None,
                  practice=False):
    """곡을 연주받아 음정·박자를 판정한다. (judgement.main() 을 파라미터화)

    Args:
        song_path:    연주할 MIDI 파일 경로.
        speed:        속도 배율. 악보상 시각을 (시각 / speed) 로 스케일.
        port:         input_source 미지정 시 사용할 로컬 rtmidi 포트 번호.
        countdown:    시작 전 카운트다운(초).
        sound:        True 면 누른 건반을 소프트웨어 신디사이저로 소리 낸다.
        input_source: MidiInputSource. None 이면 LocalMidiInput(port) 사용.
        on_event:     진행 이벤트 콜백. dict 하나를 받는다. (GUI 연동용)
        stop_event:   threading.Event. set 되면 세션을 중단한다.
        practice:     True 면 연습 모드 — 정답을 칠 때까지 다음 음으로
                      넘어가지 않고, 박자는 판정하지 않는다.

    Returns:
        완주 시 결과 요약 dict, 중단/실패 시 None.
    """
    if speed <= 0:
        raise ValueError(f"속도는 0보다 커야 합니다: {speed}")

    answers = get_answer_sheet(song_path)

    if not answers:
        print("정답지가 비어있습니다. MIDI 파일을 확인해주세요.")
        return None

    # 동시에 눌러야 하는 음들을 한 덩어리로 묶는다. 덩어리 안에서는
    # 치는 순서를 따지지 않고, 박자도 덩어리 하나를 한 박으로 본다.
    groups = group_answers(answers)

    def group_notes(index):
        """index 번째 덩어리에서 쳐야 할 음 목록."""
        start, end = groups[index]
        return [answer["note"] for answer in answers[start:end]]

    def upcoming(remaining, index):
        """다음에 쳐야 할 음 — 화음이 덜 끝났으면 그 나머지, 아니면 다음 덩어리."""
        if remaining:
            return list(remaining)
        return group_notes(index) if index < total_groups else []

    # === 통계 변수 초기화 ===
    # 연습 모드는 아래 변수로 진행하고, 일반 모드는 시작 시점에 만드는
    # TimedJudge 가 자기 통계를 가진다(요약 때 옮겨 온다).
    total_notes = len(answers)
    total_groups = len(groups)
    pitch_correct = 0
    pitch_wrong = 0
    timing_stats = {'Perfect': 0, 'Great': 0, 'Good': 0, 'Miss': 0}
    group_idx = 0           # 지금 치고 있는 덩어리
    done_notes = 0          # 판정이 끝난 음 수 (진행률 표시용)
    pending = []            # 현재 덩어리에서 아직 치지 않은 음
    # 연습 모드 전용: 총 오타 횟수와 '지금 음에서 이미 틀렸는가'
    wrong_attempts = 0
    missed_current = False
    judge = None            # 일반 모드 판정기

    own_source = input_source is None
    source = input_source or LocalMidiInput(port=port)

    # 컨트롤러(Keystation 등)는 자체 음원이 없으므로 소프트웨어
    # 신디사이저로 에코해서 소리를 낸다. 출력 포트가 없으면 무음 진행.
    player = NotePlayer() if sound else None

    def _stopped():
        return stop_event is not None and stop_event.is_set()

    def _progress():
        return judge.done_notes if judge is not None else done_notes

    def _finished():
        if judge is not None:
            return judge.finished
        return group_idx >= total_groups

    mode_note = " · 연습 모드(맞을 때까지 대기)" if practice else ""
    chord_count = sum(1 for start, end in groups if end - start > 1)
    chord_note = f" · 화음 {chord_count}곳" if chord_count else ""
    print(f"\n🎵 총 {total_notes}개의 노트를 연주해야 합니다. "
          f"(속도 {speed:g}x{chord_note}{mode_note})")
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

        first_notes = group_notes(0)
        print("\n🎹 [START] 연주 시작!")
        print(f"👉 첫 번째 목표 건반: {_names(first_notes)}")
        _emit(on_event, {"type": "start", "total": total_notes,
                         "next": _names(first_notes),
                         "next_notes": first_notes,
                         "practice": practice})

        start_time = time.time()

        if not practice:
            judge = TimedJudge(answers, groups, speed, start_time,
                               emit=lambda payload: _emit(on_event, payload))
            clock = SourceClock()

        while not _finished():
            if _stopped():
                _emit(on_event, {"type": "aborted", "index": _progress(),
                                 "total": total_notes})
                return None

            if practice and not pending:
                pending = group_notes(group_idx)
                missed_current = False

            event = source.poll()

            # Note Off 는 판정에 쓰이지 않는다. 큐에 쌓인 채로 두면 그
            # 뒤의 Note On 처리가 밀려 소리가 늦게 나므로, 여기서 즉시
            # 소진하며 소리만 꺼 준다.
            while event is not None and not event.is_on:
                if player is not None:
                    player.note_off(event.note)
                event = source.poll()
            arrived_at = time.time()

            if event is not None:
                # ── 소리 에코: 판정보다 먼저 — 판정 계산·출력이 소리를
                #    늦추지 않도록 poll 직후 곧바로 울린다 ──────────────
                if player is not None:
                    player.note_on(event.note, event.velocity)

                if not practice:
                    judge.press(event.note,
                                clock.to_local(event.timestamp, arrived_at))
                else:
                    note = event.note
                    expected_notes = list(pending)
                    expected_name = _names(expected_notes)

                    if note not in pending:
                        # 틀린 음: 오답으로 남기되 같은 덩어리에 머무른다.
                        # 정확도에는 그 음의 '첫 실패'만 반영한다.
                        wrong_attempts += 1
                        if not missed_current:
                            missed_current = True
                            pitch_wrong += 1

                        print(f"[{done_notes}/{total_notes}] "
                              f"🔁 [다시] 입력:{note_to_name(note)} "
                              f"정답:{expected_name}")
                        _emit(on_event, {
                            "type": "note",
                            "index": done_notes,
                            "total": total_notes,
                            "pitch_ok": False,
                            "played": note_to_name(note),
                            "played_note": note,
                            "expected": expected_name,
                            "expected_notes": expected_notes,
                            "grade": None,
                            "diff_ms": None,
                            "next": expected_name,
                            "next_notes": expected_notes,
                            "retry": True,
                        })
                    else:
                        first_try = not missed_current
                        if first_try:
                            pitch_correct += 1
                        missed_current = False
                        pending.remove(note)
                        done_notes += 1
                        if not pending:
                            group_idx += 1

                        next_notes = upcoming(pending, group_idx)

                        print(f"[{done_notes}/{total_notes}] "
                              f"{'✅' if first_try else '☑️'} "
                              f"[음정 O] {note_to_name(note)}")
                        if next_notes:
                            print(f"👉 다음 목표: {_names(next_notes)}")

                        _emit(on_event, {
                            "type": "note",
                            "index": done_notes,
                            "total": total_notes,
                            "pitch_ok": True,
                            "first_try": first_try,
                            "played": note_to_name(note),
                            "played_note": note,
                            "expected": expected_name,
                            "expected_notes": expected_notes,
                            "grade": None,
                            "diff_ms": None,
                            "next": _names(next_notes),
                            "next_notes": next_notes,
                        })

            # 창이 닫힌 음을 악보 시계에 맞춰 Miss 로 확정한다. 연습 모드는
            # 시간이 지나도 넘어가지 않고 정답을 칠 때까지 기다린다.
            if judge is not None:
                judge.expire(time.time())

            time.sleep(0.001)

        # === 최종 결과 통계 산출 ===
        print("\n" + "=" * 50)
        print("🎉 곡 완주! 최종 분석 결과를 확인하세요 🎉")
        print("=" * 50)

        if practice:
            # 분모는 '악보의 음 수'다. 한 음을 여러 번 치더라도 첫 시도에
            # 맞춘 음만 정답으로 센다.
            pitch_accuracy = (pitch_correct / total_notes) * 100
            # 정답을 칠 때까지 기다린 시간이 간격에 그대로 섞이므로
            # 박자는 판정하지 않았다. 점수는 음정만으로 낸다.
            timing_accuracy = None
            overall_accuracy = pitch_accuracy
        else:
            pitch_correct = judge.pitch_correct
            pitch_wrong = judge.pitch_wrong
            wrong_attempts = judge.wrong_attempts
            timing_stats = judge.timing_stats

            # 오타는 어떤 음도 소진하지 않으므로 '악보 음 중 맞춘 비율'만
            # 보면 건반을 마구 눌러도 깎이지 않는다. '누른 건반 중 맞은
            # 비율'과 비교해 낮은 쪽을 쓴다 — 정답 대신 틀린 음을 하나 친
            # 것은 한 번만 깎이고, 쓸데없이 더 누른 만큼 더 깎인다.
            presses = pitch_correct + wrong_attempts
            pitch_accuracy = (pitch_correct / max(total_notes, presses)) * 100

            # 박자의 분모는 '음 수'가 아니라 '덩어리 수'다 — 화음은 한 박이라
            # 등급도 한 번만 매겼으므로 음 수로 나누면 점수가 깎여 나온다.
            timing_score_total = (timing_stats['Perfect'] * 100
                                  + timing_stats['Great']  * 80
                                  + timing_stats['Good']   * 50)
            timing_accuracy = timing_score_total / total_groups
            overall_accuracy = (pitch_accuracy + timing_accuracy) / 2

        chord_summary = f" (화음 {chord_count}곳 포함)" if chord_count else ""
        print(f"🎵 전체 건반 수: {total_notes}개{chord_summary}")
        print("-" * 50)
        print(f"🎹 [음계 분석] 정확도: {pitch_accuracy:.1f}%")
        if practice:
            print(f"   - 한 번에 맞춘 건반: {pitch_correct}개")
            print(f"   - 틀렸던 건반: {pitch_wrong}개")
            print(f"   - 총 오타 횟수: {wrong_attempts}회")
            print("-" * 50)
            print("⏱️  [박자 분석] 연습 모드에서는 판정하지 않습니다.")
        else:
            print(f"   - 정답 건반: {pitch_correct}개")
            print(f"   - 놓친 건반: {pitch_wrong}개")
            print(f"   - 오타: {wrong_attempts}회")
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
            "mode": "practice" if practice else "normal",
            "total": total_notes,
            "total_groups": total_groups,   # 화음을 한 덩어리로 센 박 수
            "chords": chord_count,
            "pitch_correct": pitch_correct,
            "pitch_wrong": pitch_wrong,
            "wrong_attempts": wrong_attempts,
            "timing": dict(timing_stats),
            "pitch_accuracy": round(pitch_accuracy, 1),
            "timing_accuracy": (None if timing_accuracy is None
                                else round(timing_accuracy, 1)),
            "overall": round(overall_accuracy, 1),
        }
        _emit(on_event, {"type": "done", "result": result})
        return result

    except KeyboardInterrupt:
        print("\n연주가 중단되었습니다.")
        _emit(on_event, {"type": "aborted", "index": _progress(),
                         "total": total_notes})
        return None
    finally:
        if player is not None:
            player.close()
        if own_source:
            source.close()


def run_from_settings(settings=None, midi_port=0, udp_port=UDP_DEFAULT_PORT,
                      **kwargs):
    """설정(곡 선택 + 속도)을 읽어 판정을 실행한다.

    선택된 곡이 없으면 목록의 첫 곡을 사용한다. GUI 는 원하는 대로
    Settings 를 구성해 넘기거나, 직접 run_judgement 를 호출하면 된다.
    kwargs 는 run_judgement 로 그대로 전달된다(input_source 등).

    midi_port 는 설정이 'local' 일 때 열 rtmidi 포트 번호다. 이 값을
    넘기지 않으면 언제나 0번 포트가 열려, 실제 건반이 1번 이후에 잡힌
    PC 에서는 아무 입력도 들어오지 않는다(0번이 보통 loopMIDI 다).
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

    # 호출자가 practice 를 명시하지 않았으면 설정값을 따른다.
    kwargs.setdefault("practice", settings.practice_mode)

    mode_label = "연습(맞을 때까지)" if kwargs["practice"] else "일반"
    print(f"🎼 곡: {song.name}  |  ⏩ 속도: {settings.speed:g}x"
          f"  |  🎹 입력: {settings.input_source}  |  🎯 모드: {mode_label}")

    if "input_source" in kwargs:
        return run_judgement(song.path, speed=settings.speed, **kwargs)

    # 설정이 고른 소스(local/udp)를 만들어 세션에 넘긴다.
    with create_input_source(settings.input_source, midi_port=midi_port,
                             udp_port=udp_port) as source:
        return run_judgement(song.path, speed=settings.speed,
                             input_source=source, **kwargs)


def _list_midi_ports():
    """연결된 로컬 MIDI 입력 포트를 번호와 함께 출력한다."""
    try:
        ports = list_midi_ports()
    except MidiSystemStuck as exc:
        print(f"\n{exc}\n")
        print(midi_stuck_help())
        return

    if not ports:
        print("MIDI 입력 포트가 없습니다. 건반이 연결되어 있는지 확인하세요.")
        return

    print("사용 가능한 MIDI 입력 포트:")
    for index, name in enumerate(ports):
        print(f"  {index}: {name}")
    print("\n보통은 그냥 두면 됩니다 — 기본값 auto 가 진짜 건반을 찾습니다.")


def _parse_args():
    import argparse

    parser = argparse.ArgumentParser(
        description="Keymento 연주 세션 (콘솔 단독 실행)")
    parser.add_argument("--midi-port", default="auto",
                        help="MIDI 입력 포트. 기본 auto 는 가상 포트를 빼고 "
                             "진짜 건반을 찾는다. 번호(2)나 이름 조각"
                             "(keystation)으로 직접 고를 수도 있다.")
    parser.add_argument("--udp-port", type=int, default=UDP_DEFAULT_PORT,
                        help=f"UDP 입력 수신 포트 (기본: {UDP_DEFAULT_PORT})")
    parser.add_argument("--practice", dest="practice", action="store_true",
                        default=None,
                        help="연습 모드 — 정답을 칠 때까지 대기")
    parser.add_argument("--normal", dest="practice", action="store_false",
                        help="일반 모드 — 악보 시계대로 진행하며 박자도 판정")
    parser.add_argument("--countdown", type=int, default=5,
                        help="연주 시작 전 카운트다운 초 (기본: 5)")
    parser.add_argument("--no-sound", action="store_true",
                        help="건반 소리 에코 끄기")
    parser.add_argument("--list-ports", action="store_true",
                        help="MIDI 입력 포트 목록만 출력하고 종료")
    return parser.parse_args()


def _main():
    args = _parse_args()

    if args.list_ports:
        _list_midi_ports()
        return

    kwargs = {"countdown": args.countdown, "sound": not args.no_sound}
    # --practice/--normal 을 안 주면 설정값(settings.practice_mode)을 따른다.
    if args.practice is not None:
        kwargs["practice"] = args.practice

    run_from_settings(midi_port=resolve_midi_port(args.midi_port),
                      udp_port=args.udp_port, **kwargs)


if __name__ == "__main__":
    _main()
