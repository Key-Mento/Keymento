"""MIDI 의 박자 정보 — 템포 맵(틱 → 초)과 박자표(마디·박).

메트로놈(perform/metronome.py)과 악보 보기(perform/sheet.py)가 같이 쓴다.
틱을 초로 바꾸는 방식이 정답지(judgement.get_answer_sheet 가 쓰는 mido 순회)와
같아서, 여기서 구한 박·마디 시각은 판정의 음 시각과 그대로 맞는다.
모든 시각은 악보상 초다(실제 시각 = 악보상 시각 / speed).
"""

import bisect
import math
from dataclasses import dataclass

import mido

DEFAULT_TEMPO = 500000          # MIDI 기본값(120 BPM) — 템포 메시지가 없는 곡
DEFAULT_SIGNATURE = (4, 4)      # 박자표가 없는 곡

_EPS = 1e-6


@dataclass(frozen=True)
class Meter:
    """박자표 하나를 '박' 단위로 풀어 둔 것.

    홑박자는 분모 음표가 한 박이다(4/4 → 4분음표 4박, 2/4 → 2박).
    겹박자(6/8·9/8·12/8)는 점4분음표가 한 박이다(6/8 → 2박).
    """

    tick: int                   # 이 박자표가 시작되는 틱
    label: str                  # "4/4"
    pulses: int                 # 마디당 박 수
    pulse_ticks: float          # 한 박의 틱 수
    subdivision: int            # 박을 몇 등분해 세는가 (홑박자 2, 겹박자 3)

    @property
    def bar_ticks(self):
        return self.pulses * self.pulse_ticks

    @property
    def numerator(self):
        return int(self.label.split("/")[0])

    @property
    def denominator(self):
        return int(self.label.split("/")[1])


def make_meter(tick, numerator, denominator, ticks_per_beat):
    unit = ticks_per_beat * 4 / denominator      # 분모 음표 하나의 틱 수
    label = f"{numerator}/{denominator}"
    if denominator == 8 and numerator > 3 and numerator % 3 == 0:
        return Meter(tick, label, numerator // 3, unit * 3, 3)
    return Meter(tick, label, numerator, unit, 2)


class TempoMap:
    """틱 → 악보상 초.

    mido 는 MidiFile 을 순회할 때 템포 메시지 '다음' 메시지부터 새 템포로
    시각을 매긴다. 여기서도 템포가 바뀐 틱부터 새 템포를 적용하므로 같은 값이
    나온다. 같은 틱의 템포 메시지는 나중 것이 이긴다.
    """

    def __init__(self, ticks_per_beat, tempos):
        self._tpb = ticks_per_beat
        latest = {}
        for tick, tempo in tempos:
            latest[tick] = tempo
        points = sorted(latest.items())
        if not points or points[0][0] > 0:
            points.insert(0, (0, DEFAULT_TEMPO))

        self._ticks = [tick for tick, _ in points]
        self._tempos = [tempo for _, tempo in points]
        self._secs = [0.0]
        for i in range(1, len(points)):
            span = self._ticks[i] - self._ticks[i - 1]
            self._secs.append(self._secs[-1]
                              + self._span_seconds(span, self._tempos[i - 1]))

    def _span_seconds(self, ticks, tempo):
        return ticks * tempo / (self._tpb * 1_000_000)

    def _segment(self, tick):
        return max(bisect.bisect_right(self._ticks, tick) - 1, 0)

    def seconds(self, tick):
        i = self._segment(tick)
        return self._secs[i] + self._span_seconds(tick - self._ticks[i],
                                                  self._tempos[i])

    @property
    def changes(self):
        """곡 안에서 템포가 실제로 바뀌는가."""
        return len(set(self._tempos)) > 1


def read_meter(song_path):
    """MIDI 에서 (TempoMap, [Meter]) 를 읽는다. 박자표는 틱 순이다."""
    mid = mido.MidiFile(song_path)
    tpb = mid.ticks_per_beat
    tick = 0
    tempos, signatures = [], []
    for msg in mido.merge_tracks(mid.tracks):
        tick += msg.time
        if msg.type == "set_tempo":
            tempos.append((tick, msg.tempo))
        elif msg.type == "time_signature":
            signatures.append((tick, msg.numerator, msg.denominator))

    meters = []
    for sig_tick, numerator, denominator in signatures:
        meter = make_meter(sig_tick, numerator, denominator, tpb)
        if meters and meters[-1].tick == sig_tick:
            meters[-1] = meter                  # 같은 틱이면 나중 것
        elif not meters or meters[-1].label != meter.label:
            meters.append(meter)                # 같은 박자표 반복은 무시
    if not meters or meters[0].tick > 0:
        meters.insert(0, make_meter(0, *DEFAULT_SIGNATURE, tpb))
    return TempoMap(tpb, tempos), meters


@dataclass(frozen=True)
class Bar:
    """마디 하나."""

    start: float                # 시작 (악보상 초)
    end: float                  # 끝 (악보상 초)
    meter: Meter
    beat: float                 # 이 마디 첫 박의 길이 (악보상 초)


def bars(tempo_map, meters, until):
    """마디 목록 [Bar] — until(초)이 들어 있는 마디까지.

    박자표가 마디 중간에서 바뀌면 그 마디는 거기서 끊는다.
    """
    out = []
    for index, meter in enumerate(meters):
        stop_tick = (meters[index + 1].tick if index + 1 < len(meters)
                     else math.inf)
        tick = meter.tick
        while tick < stop_tick:
            start = tempo_map.seconds(tick)
            if start > until + _EPS:
                return out
            end_tick = min(tick + meter.bar_ticks, stop_tick)
            beat = tempo_map.seconds(tick + meter.pulse_ticks) - start
            out.append(Bar(start, tempo_map.seconds(end_tick), meter, beat))
            tick += meter.bar_ticks
    return out
