"""MIDI → 오선 악보 데이터: 마디마다 음표·쉼표·붙임줄·빔.

악보 보기(perform/sheet.py)가 이것으로 오선 악보를 그린다. 음은 정답지
(judgement.get_answer_sheet)와 같은 기준으로 고른다 — C4(60) 이상, 화음 안의
중복 제거, 덩어리 묶음까지 같아서 악보의 음표 덩어리 번호가 판정의 덩어리
번호와 그대로 맞는다.

■ 칸 (길이의 최소 단위)
  4분음표를 4칸(16분음표 단위)으로 나누는 것이 기본이다. 음이 그보다 촘촘해
  두 덩어리가 같은 칸에 떨어지는 곡은 8칸(32분), 16칸(64분)으로 더 잘게 나눈다.

■ 길이 정하기
  - MIDI 는 보통 음 길이의 80~95% 만 누르고 뗀다. 그래서 누른 길이는 그
    이상인 가장 짧은 음표 길이로 올려 맞춘다(2분음표가 '점4분 + 16분'으로
    쪼개지지 않게). 그 음표가 다음 덩어리 직전(16분음표 미만 틈)까지 가거나
    간격의 80% 이상 눌렀으면 다음 덩어리까지 이어지는 음표로 보고, 아니면
    남는 자리를 쉼표로 둔다.
  - 마디를 넘는 음은 마디선에서 잘라 붙임줄로 잇는다.
  - 마디 안의 길이는 표기할 수 있는 음표(온·점2분·2분·점4분·4분·점8분·8분·…)로
    나눈다. 박 중간에서 시작한 음은 먼저 그 박 끝까지 끊고, 4분음표 이상은 박
    위에서만 시작하게 한다. 겹박자(6/8 등)는 박(점4분음표) 경계를 넘지 않는다.
  - 빈 마디는 온쉼표 하나로 쓴다.
  - 4분음표보다 짧은 음표는 같은 박 안에서 빔으로 묶는다(쉼표가 끼면 끊는다).
"""

import math
import os
import sys
from dataclasses import dataclass, field
from typing import Optional

import mido

_PIANO_SCORE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "piano-score")
if _PIANO_SCORE_DIR not in sys.path:
    sys.path.insert(0, _PIANO_SCORE_DIR)

from judgement import dedupe_chord_notes, group_answers  # noqa: E402
from perform.meter import read_meter                       # noqa: E402

GRIDS = (4, 8, 16)              # 4분음표 하나를 몇 칸으로 나누나 — 음이 몰리면 더 잘게
# 표기할 수 있는 길이 (4분음표 = 1): 온·점2분·2분·점4분·4분·점8분·8분·점16분·16분·점32분·32분·64분
_FRACTIONS = (4, 3, 2, 1.5, 1, 0.75, 0.5, 0.375, 0.25, 0.1875, 0.125, 0.0625)
_DOTTED_FRACTIONS = (3, 1.5, 0.75, 0.375, 0.1875)


def _units(fractions, upq):
    return tuple(int(f * upq) for f in fractions
                 if f * upq >= 1 and float(f * upq).is_integer())


@dataclass
class Event:
    """마디 안의 음표(화음 포함) 또는 쉼표 하나."""

    pos: int                    # 마디 시작부터 칸
    length: int                 # 칸 (표기할 수 있는 길이, 온마디 쉼표는 마디 길이)
    pitches: tuple              # MIDI 음 (오름차순). 비면 쉼표
    group: Optional[int]        # 정답지 덩어리 번호 (쉼표는 None)
    time: float                 # 시작 시각 (악보상 초)
    tie_prev: bool = False      # 앞 음에서 붙임줄로 이어진 음인가
    tie_next: bool = False      # 다음 음으로 붙임줄이 이어지는가
    bar_rest: bool = False      # 온마디 쉼표
    beam: Optional[int] = None  # 같은 번호끼리 하나의 빔

    @property
    def is_rest(self):
        return not self.pitches


@dataclass
class Bar:
    index: int
    start: int                  # 곡 시작부터 칸
    length: int                 # 칸
    meter: object               # perform.meter.Meter
    start_time: float           # 악보상 초
    end_time: float
    show_meter: bool = False    # 이 마디 앞에 박자표를 그린다
    events: list = field(default_factory=list)


@dataclass
class Notation:
    bars: list
    group_notes: list           # 정답지 덩어리마다 음 수 (연습 모드 진행 표시용)
    group_times: list           # 정답지 덩어리마다 시작 시각 (악보상 초)
    upq: int = 4                # 4분음표 하나의 칸 수
    durations: tuple = ()       # 표기할 수 있는 길이(칸), 긴 것부터
    dotted: frozenset = frozenset()


def _records(song_path):
    """정답지와 같은 기준으로 고른 음 [{note, time, tick, off}] (시각 순)."""
    mid = mido.MidiFile(song_path)
    records, sounding = [], {}
    tick, sec = 0, 0.0
    # mido 의 MidiFile 순회(초)와 merge_tracks(틱)는 같은 순서로 메시지를 낸다.
    for timed, raw in zip(mid, mido.merge_tracks(mid.tracks)):
        tick += raw.time
        sec += timed.time
        if raw.type == "note_on" and raw.velocity > 0:
            if raw.note >= 60:
                rec = {"note": raw.note, "time": sec, "tick": tick, "off": None}
                records.append(rec)
                sounding.setdefault((raw.channel, raw.note), []).append(rec)
        elif raw.type in ("note_on", "note_off"):          # velocity 0 = off
            waiting = sounding.get((raw.channel, raw.note))
            if waiting:
                waiting.pop(0)["off"] = tick
    return dedupe_chord_notes(records), mid.ticks_per_beat


def _pow2_floor(n):
    return 1 << (int(n).bit_length() - 1)


def _snap_up(units, durations):
    """실제로 누른 길이(칸, 소수) → 그 이상인 가장 짧은 음표 길이(칸)."""
    for d in reversed(durations):
        if d >= units - 0.25:
            return d
    return int(math.ceil(units))


def _pieces(pos, length, bar_len, beat, align, durations, compound=False):
    """마디 안 pos 부터 length 칸 → [(pos, 칸)] 표기할 수 있는 길이로 나눈다.

    겹박자(6/8 등)는 박(점4분음표) 경계를 넘는 음표를 쓰지 않는다 — 박 안에
    들어가거나, 박 위에서 시작해 박 단위(점4분·점2분)로 끝나야 한다.
    """
    out = []
    while length > 0:
        limit = length if pos % beat == 0 else min(length, beat - pos % beat)
        chosen = 1
        for d in durations:
            if d > limit or d > bar_len or pos % min(_pow2_floor(d), align):
                continue
            if (compound and d > beat - pos % beat
                    and not (pos % beat == 0 and d % beat == 0)):
                continue
            chosen = d
            break
        out.append((pos, chosen))
        pos += chosen
        length -= chosen
    return out


def build_notation(song_path):
    """곡 파일 → Notation (마디 목록 등)."""
    records, tpb = _records(song_path)
    tempo_map, meters = read_meter(song_path)
    groups = group_answers(records)
    if not groups:
        return Notation([], [], [])

    # 칸 크기: 두 덩어리가 같은 칸에 떨어지지 않는 가장 굵은 칸
    upq = GRIDS[-1]
    for grid in GRIDS:
        starts = [int(round(records[s]["tick"] * grid / tpb)) for s, _ in groups]
        if all(b > a for a, b in zip(starts, starts[1:])):
            upq = grid
            break
    unit = tpb / upq                                      # 한 칸의 틱 수
    durations = _units(_FRACTIONS, upq)
    dotted = frozenset(_units(_DOTTED_FRACTIONS, upq))

    def q(tick):
        return int(round(tick / unit))

    def meter_rules(meter):
        beat = int(round(meter.pulse_ticks / unit)) or 1
        align = max(1, {2: 2 * upq, 4: upq, 8: upq // 2,
                        16: upq // 4}.get(meter.denominator, 1))
        return beat, align, meter.subdivision == 3

    # ── 덩어리 → (시작 칸, 끝 칸, 음들, 덩어리 번호) 와 그 사이 쉼표 ──────
    starts = [q(records[s]["tick"]) for s, _ in groups]
    for i in range(1, len(starts)):                       # 그래도 몰리면 한 칸씩 민다
        starts[i] = max(starts[i], starts[i - 1] + 1)

    spans = []
    if starts[0] > 0:
        spans.append((0, starts[0], (), None))
    for i, (s, e) in enumerate(groups):
        start = starts[i]
        nxt = starts[i + 1] if i + 1 < len(groups) else None
        offs = [r["off"] for r in records[s:e] if r["off"] is not None]
        sounding = ((max(offs) - records[s]["tick"]) / unit if offs
                    else upq)                             # 실제로 누른 길이(칸)
        snapped = start + _snap_up(sounding, durations)
        if nxt is None:
            end = snapped
        elif sounding >= 0.8 * (nxt - start) or snapped >= nxt - upq / 4:
            end = nxt           # 다음 음까지 이어지는 음표 (사이 틈은 16분음표 미만)
        else:
            end = snapped       # 남는 자리는 쉼표
        pitches = tuple(sorted({r["note"] for r in records[s:e]}))
        spans.append((start, end, pitches, i))
        if nxt is not None and end < nxt:
            spans.append((end, nxt, (), None))
    total = spans[-1][1]

    # ── 마디 나누기 ─────────────────────────────────────────────────
    bars = []
    for index, meter in enumerate(meters):
        first = q(meter.tick)
        stop = q(meters[index + 1].tick) if index + 1 < len(meters) else math.inf
        bar_len = q(meter.bar_ticks)
        pos = first
        while pos < stop and pos < total:
            length = int(min(bar_len, stop - pos))
            bars.append(Bar(len(bars), pos, length, meter,
                            tempo_map.seconds(pos * unit),
                            tempo_map.seconds((pos + length) * unit),
                            show_meter=(pos == first)))
            pos += bar_len
    if not bars:
        return Notation([], [], [])
    bar_starts = [bar.start for bar in bars]

    def bar_at(unit_pos):
        lo, hi = 0, len(bars) - 1
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if bar_starts[mid] <= unit_pos:
                lo = mid
            else:
                hi = mid - 1
        return lo

    # ── 마디 안에서 표기 가능한 길이로 나누고 붙임줄 표시 ──────────────
    group_times = [records[s]["time"] for s, _ in groups]
    for start, end, pitches, group in spans:
        pieces = []
        pos = start
        while pos < end:
            bar = bars[bar_at(pos)]
            seg_end = min(end, bar.start + bar.length)
            beat, align, compound = meter_rules(bar.meter)
            in_bar = pos - bar.start
            if not pitches and in_bar == 0 and seg_end - pos == bar.length:
                pieces.append((bar, 0, bar.length, True))     # 온마디 쉼표
            else:
                for p, d in _pieces(in_bar, seg_end - pos, bar.length, beat,
                                    align, durations, compound):
                    pieces.append((bar, p, d, False))
            pos = seg_end
        for k, (bar, p, d, whole_rest) in enumerate(pieces):
            if k == 0 and group is not None:
                time = group_times[group]                   # 판정과 같은 시각
            else:
                time = tempo_map.seconds((bar.start + p) * unit)
            bar.events.append(Event(
                pos=p, length=d, pitches=pitches, group=group, time=time,
                tie_prev=bool(pitches) and k > 0,
                tie_next=bool(pitches) and k < len(pieces) - 1,
                bar_rest=whole_rest))

    # ── 마지막 마디의 남은 자리는 쉼표로 채우고, 빔을 묶는다 ────────────
    last = bars[-1]
    filled = max((e.pos + e.length for e in last.events), default=0)
    if filled < last.length:
        beat, align, compound = meter_rules(last.meter)
        for p, d in _pieces(filled, last.length - filled, last.length, beat,
                            align, durations, compound):
            last.events.append(Event(p, d, (), None,
                                     tempo_map.seconds((last.start + p) * unit)))

    beam_id = 0
    for bar in bars:
        bar.events.sort(key=lambda e: e.pos)
        beat = meter_rules(bar.meter)[0]
        run = []
        for event in bar.events + [None]:
            beamable = (event is not None and not event.is_rest
                        and event.length < upq)
            if beamable and run and event.pos // beat == run[0].pos // beat:
                run.append(event)
                continue
            if len(run) >= 2:
                for member in run:
                    member.beam = beam_id
                beam_id += 1
            run = [event] if beamable else []

    return Notation(bars, [e - s for s, e in groups], group_times,
                    upq=upq, durations=durations, dotted=dotted)
