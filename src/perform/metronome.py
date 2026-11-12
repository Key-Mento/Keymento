"""[실험용] 판정 시계에 맞물린 메트로놈 — 카운트인과 연주 중 박 클릭.

■ 왜 필요한가
일반 모드는 음마다 '목표 시각 ±0.9초' 안에 쳐야 인정한다(session.TimedJudge).
AR 은 건반 위에 음을 제때 띄워 그 시각을 알려 주지만, 모니터에 계이름 악보만
띄운 조건은 판정 템포를 알 방법이 없다. AR 배율 0.35 를 거치면 80 BPM 곡도
실제로는 28 BPM(1박 2.14초)이라, 건반을 다 맞게 쳐도 템포가 조금만 다르면
판정 창을 벗어나 음정 점수까지 무너진다. 그래서 두 조건 모두에 같은 박 소리를
들려준다. 채점은 그대로다 — 이 모듈은 소리만 낸다.

■ 박 격자
MIDI 의 템포 맵과 박자표(perform/meter.py)로 악보상 박 시각을 만든다. 정답지와
같은 방식으로 틱을 초로 바꾸므로 템포가 바뀌는 곡에서도 박이 음과 맞는다.
실제 시각은 판정과 같이 (악보상 시각 / speed) 다.
  - 마디 첫 박은 강박 클릭, 나머지 박은 보통 클릭.
  - 실제 박 간격이 SUBDIVIDE_ABOVE 초보다 길면 박 사이에 작은 보조 클릭을
    넣는다(홑박자 2등분, 겹박자 3등분). 박 간격이 대략 1.8초를 넘으면 사람은
    다음 박을 예측하지 못하고 클릭을 들은 뒤에 반응해서 친다(Repp, 2005).
  - 클릭은 마지막 음까지만 울린다.

■ 카운트인
첫 음(악보 시각 0) 앞에 첫 마디의 박자·템포로 마디 단위 클릭을 넣고, 그다음
강박에서 곡이 시작된다. 마디 수는 카운트인이 카운트다운 초보다 짧지 않게
정한다(최소 1마디) — 메트로놈을 켜면 카운트인이 카운트다운을 대신한다.

■ 소리 지연 보정
소리는 출력 장치의 지연만큼 늦게 들린다. 들리는 시각이 박에 맞도록 그만큼
일찍 울린다(NotePlayer.output_latency).
"""

import math
from dataclasses import dataclass
from typing import Optional

from perform.meter import read_meter

ACCENT = "accent"       # 마디 첫 박
BEAT = "beat"           # 나머지 박
SUB = "sub"             # 박 사이 보조 클릭

# 실제 박 간격(초)이 이보다 길면 보조 클릭을 넣는다.
SUBDIVIDE_ABOVE = 1.2

# 루프가 잠깐 멈칫해 이만큼(초) 넘게 늦어진 클릭은 울리지 않고 건너뛴다.
# 늦게 몰아서 울리면 박을 틀리게 알려 주는 셈이기 때문이다.
LATE_SKIP = 0.08

_EPS = 1e-6


@dataclass(frozen=True)
class Click:
    """울릴 클릭 하나."""

    at: float                   # 곡 시작(악보 시각 0) 기준 실제 시각(초). 카운트인은 음수
    kind: str                   # ACCENT / BEAT / SUB
    count: Optional[int] = None  # 카운트인 박이면 남은 박 수(화면 표시용)


def _score_clicks(tempo_map, meters, end_sec, speed):
    """곡 안의 클릭 [(악보상 초, 종류)] — 마지막 음(end_sec)까지, 시각 순."""
    clicks = []
    for index, meter in enumerate(meters):
        stop_tick = (meters[index + 1].tick if index + 1 < len(meters)
                     else math.inf)
        bar = meter.tick
        while bar < stop_tick:
            for p in range(meter.pulses):
                tick = bar + p * meter.pulse_ticks
                if tick >= stop_tick:
                    break
                sec = tempo_map.seconds(tick)
                if sec > end_sec + _EPS:
                    return clicks
                clicks.append((sec, ACCENT if p == 0 else BEAT))

                # 보조 클릭은 박마다 그 박의 실제 길이로 정한다 — 템포가 바뀌는
                # 곡에서도 느린 구간에만 들어간다.
                length = tempo_map.seconds(tick + meter.pulse_ticks) - sec
                if length / speed <= SUBDIVIDE_ABOVE:
                    continue
                for s in range(1, meter.subdivision):
                    sub_tick = tick + meter.pulse_ticks * s / meter.subdivision
                    sub_sec = tempo_map.seconds(sub_tick)
                    if sub_tick >= stop_tick or sub_sec > end_sec + _EPS:
                        break
                    clicks.append((sub_sec, SUB))
            bar += meter.bar_ticks
    return clicks


def _count_in_clicks(pulse, meter, min_seconds):
    """카운트인 클릭(실제 시각, 음수). pulse 는 첫 박의 실제 길이(초)."""
    bar = pulse * meter.pulses
    bars = max(1, math.ceil(min_seconds / bar - _EPS))
    total = bars * meter.pulses
    subdivide = pulse > SUBDIVIDE_ABOVE

    clicks = []
    for i in range(total):
        at = -(total - i) * pulse
        kind = ACCENT if i % meter.pulses == 0 else BEAT
        clicks.append(Click(at, kind, total - i))
        if subdivide:
            for s in range(1, meter.subdivision):
                clicks.append(Click(at + pulse * s / meter.subdivision, SUB))
    return clicks


class Metronome:
    """곡의 박 격자를 판정 시계에 맞춰 울린다.

    사용 순서:
        metro = Metronome.for_song(path, answers[-1]["time"], speed, 5, player)
        origin = metro.start(time.time())   # 곡 시작 시각(PC 시계)
        while ...:
            metro.tick(time.time())         # 차례가 된 클릭을 울린다

    Args:
        clicks: Click 목록(시각 순).
        player: click(kind) 를 가진 소리 출력(sound.NotePlayer).
        lead:   소리 출력 지연(초). 그만큼 일찍 울린다.
        info:   콘솔 안내용 설명.
    """

    def __init__(self, clicks, player, lead=0.0, info=""):
        self._clicks = list(clicks)
        self._player = player
        self.lead = max(float(lead), 0.0)
        self.info = info
        self._origin = None
        self._next = 0

        count_in = [click for click in self._clicks if click.at < 0]
        self.count_in_seconds = -count_in[0].at if count_in else 0.0
        self.count_in_beats = sum(1 for c in count_in if c.count is not None)
        # 카운트인 숫자 하나가 버티는 시간(초) — 화면이 박에 맞춰 숫자를 센다.
        self.count_in_step = (self.count_in_seconds / self.count_in_beats
                              if self.count_in_beats else 1.0)

    @classmethod
    def for_song(cls, song_path, end_time, speed, min_count_in, player):
        """곡 파일로 메트로놈을 만든다.

        Args:
            song_path:    MIDI 경로(판정에 쓰는 곡과 같은 파일).
            end_time:     마지막 음의 악보상 시각(초) — answers[-1]["time"].
            speed:        판정에 넘기는 속도 배율(설정 × AR 배율).
            min_count_in: 카운트인의 최소 길이(초). 보통 카운트다운 초.
            player:       소리 출력. output_latency 가 있으면 지연을 보정한다.
        """
        if speed <= 0:
            raise ValueError(f"속도는 0보다 커야 합니다: {speed}")

        tempo_map, meters = read_meter(song_path)
        first = meters[0]
        pulse = (tempo_map.seconds(first.pulse_ticks) - tempo_map.seconds(0)) / speed

        clicks = _count_in_clicks(pulse, first, min_count_in)
        clicks += [Click(sec / speed, kind)
                   for sec, kind in _score_clicks(tempo_map, meters,
                                                  end_time, speed)]

        lead = getattr(player, "output_latency", 0.0) or 0.0
        notes = [f"박 {60 / pulse:.0f} BPM(1박 {pulse:.2f}초)", first.label]
        if pulse > SUBDIVIDE_ABOVE:
            notes.append(f"보조 클릭 {first.subdivision}등분")
        if tempo_map.changes:
            notes.append("곡 중 템포 변화 반영")
        if lead:
            notes.append(f"소리 지연 {lead * 1000:.0f}ms 보정")
        return cls(clicks, player, lead=lead, info=" · ".join(notes))

    def describe(self):
        bars = "" if not self.count_in_beats else (
            f" · 카운트인 {self.count_in_beats}박({self.count_in_seconds:.1f}초)")
        return self.info + bars

    def start(self, now):
        """카운트인 첫 클릭이 지금 울리도록 시계를 잡고, 곡 시작 시각을 돌려준다.

        반환값은 PC 시계(time.time()) 기준으로 악보 시각 0 이 되는 순간이다.
        판정기(TimedJudge)의 start_time 으로 그대로 쓴다.
        """
        self._origin = now + self.count_in_seconds + self.lead
        self._next = 0
        return self._origin

    def tick(self, now):
        """now 까지 차례가 된 클릭을 울린다.

        카운트인 박을 지났으면 그 남은 박 수를, 아니면 None 을 돌려준다
        (화면의 카운트다운 숫자를 박에 맞춰 바꾸는 데 쓴다).
        """
        if self._origin is None:
            return None

        counted = None
        while self._next < len(self._clicks):
            click = self._clicks[self._next]
            due = self._origin + click.at - self.lead
            if now < due:
                break
            self._next += 1
            if click.count is not None:
                counted = click.count
            if now - due > LATE_SKIP:
                continue
            self._player.click(click.kind)
        return counted

    @property
    def finished(self):
        return self._next >= len(self._clicks)
