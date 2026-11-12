"""[실험용] 웹 UI 의 악보 보기 — 곡을 오선 악보(SVG)로 그린다.

악보 조건(run nocam --sheet)에서 AR 영상 자리에 띄우는 악보다. 높은음자리표·
박자표·음표(머리·기둥·꼬리·빔)·쉼표·붙임줄·임시표·덧줄을 그리고(음표 데이터는
perform/notation.py), 브라우저(gui/index.html)가 판정 시계에 맞춰 지나간 자리를
빨갛게 칠한다.

■ 진행 칠하기
  줄마다 '시각 → 가로 위치' 기준점(timeline 의 anchors)을 준다. 기준점은 음표·
  쉼표의 시작 시각과 그 머리 위치라서, 기준점 사이를 직선으로 이으면 칠의 끝이
  음표 머리에 닿는 순간이 곧 그 음의 목표 시각이다. 마디 안에서는 음표를 시간에
  비례한 자리에 놓았으므로(2분음표는 4분음표의 두 배 자리) 칠이 일정한 속도로
  나아간다. 지나간 음표는 빨갛게 바꾼다(events).

■ 글꼴 기호
  높은음자리표·4분/8분/16분쉼표·박자표 숫자는 글꼴 글자(text.glyph)로 넣는다.
  글꼴마다 글자 크기가 달라서, 브라우저가 실제 잉크 크기를 재어 data-h(높이)와
  data-cx/data-cy(가운데)에 맞춘다(index.html 의 fitGlyphs). 나머지는 도형이다.
"""

from perform.notation import build_notation

BARS_PER_LINE = 4               # 한 줄에 놓는 마디 수 (실험곡은 4마디 프레이즈)

S = 10.0                        # 오선 한 칸(줄 사이) — SVG 좌표 단위
WIDTH = 1200.0
LINE_H = 14 * S                 # 한 줄(오선 + 위아래 여백) 높이
STAFF_TOP = 5 * S               # 줄 맨 위에서 오선 첫 줄까지
MARGIN_X = 12.0
CLEF_W = 4.4 * S
METER_W = 3.0 * S
BAR_PAD_L = 1.9 * S
BAR_PAD_R = 1.4 * S
INK = "#1d2026"

HEAD_RX, HEAD_RY, HEAD_TILT = 0.64 * S, 0.46 * S, -20
STEM_LEN = 3.5 * S
BEAM_T = 0.5 * S
BEAM_GAP = 0.75 * S

TOP_STEP, MID_STEP, BOTTOM_STEP = 38, 34, 30    # F5, B4, E4 (오선 맨 위·가운데·맨 아래 줄)
_LETTER = (0, 0, 1, 1, 2, 3, 3, 4, 4, 5, 5, 6)  # 반음 → 음이름(C=0 … B=6), 검은건반은 #
_SHARP = (False, True, False, True, False, False, True, False, True, False, True, False)

GLYPH_FONT = "'Segoe UI Symbol','Noto Music','Apple Symbols','Bravura',serif"
DIGIT_FONT = "'Times New Roman',Georgia,serif"
CLEF = "\U0001D11E"
# 4분·8분·16분·32분·64분쉼표 (꼬리 개수 0~4 순)
REST_GLYPHS = ("\U0001D13D", "\U0001D13E", "\U0001D13F", "\U0001D140", "\U0001D141")
# 글꼴 기호의 (잉크 높이, 잉크 가운데 y) — 오선 맨 윗줄에서 S 단위
CLEF_BOX = (7.2, 1.9)
REST_BOX = ((2.9, 2.0), (1.9, 2.0), (2.9, 2.4), (3.9, 2.4), (4.9, 2.4))


def _n(value):
    text = f"{value:.1f}"
    text = text.rstrip("0").rstrip(".") if "." in text else text
    return "0" if text in ("-0", "") else text


def step_of(pitch):
    """MIDI 음 → 오선 위 자리(음이름 단위 번호). 검은건반은 # 으로 읽는다."""
    return (pitch // 12 - 1) * 7 + _LETTER[pitch % 12]


class _Engraver:
    def __init__(self, upq, dotted):
        self.upq = upq              # 4분음표 하나의 칸 수 (notation 의 길이 단위)
        self.dotted = dotted        # 점음표 길이(칸)
        self.wipes, self.staff, self.notes = [], [], []
        self.timeline, self.events = [], []
        self.pieces = []            # 그린 음표 조각(붙임줄용), 시각 순

    def level(self, length):
        """꼬리·빔 개수: 8분 1, 16분 2, 32분 3, 64분 4 (4분음표 이상은 0)."""
        base = length * 2 / 3 if length in self.dotted else length
        count = 0
        while base < self.upq - 1e-9:
            base *= 2
            count += 1
        return count

    # ── 기본 도형 ────────────────────────────────────────────────
    def line(self, x1, y1, x2, y2, width=1.0, cls=None, out=None):
        attr = f' class="{cls}"' if cls else ""
        (self.staff if out is None else out).append(
            f'<line{attr} x1="{_n(x1)}" y1="{_n(y1)}" x2="{_n(x2)}" y2="{_n(y2)}" '
            f'stroke="{INK}" stroke-width="{_n(width)}"/>')

    def glyph(self, text, cx, cy, height, font=GLYPH_FONT, bold=False):
        """글꼴 기호. 브라우저가 잉크 크기를 재어 크기·자리를 다시 맞춘다."""
        weight = ' font-weight="700"' if bold else ""
        self.staff.append(
            f'<text class="glyph" data-cx="{_n(cx)}" data-cy="{_n(cy)}" '
            f'data-h="{_n(height)}" x="{_n(cx - height * 0.25)}" '
            f'y="{_n(cy + height * 0.35)}" font-size="{_n(height)}" '
            f'font-family="{font}"{weight} fill="{INK}">{text}</text>')

    @staticmethod
    def y(top, step):
        return top + (TOP_STEP - step) * S / 2

    # ── 줄 ───────────────────────────────────────────────────────
    def system(self, k, bars, per, last_bar_index):
        top = k * LINE_H + STAFF_TOP
        x = MARGIN_X
        self.glyph(CLEF, x + CLEF_W * 0.45, top + CLEF_BOX[1] * S, CLEF_BOX[0] * S)
        x += CLEF_W
        if bars[0].show_meter:
            self.meter(bars[0].meter, x + METER_W / 2, top)
            x += METER_W
        inner = sum(METER_W for bar in bars[1:] if bar.show_meter)
        bar_w = (WIDTH - MARGIN_X - x - inner) / per
        x_start = x

        anchors = []
        for i, bar in enumerate(bars):
            if i > 0 and bar.show_meter:
                self.meter(bar.meter, x + METER_W / 2, top)
                x += METER_W
            anchors += self.measure(k, bar, top, x + BAR_PAD_L,
                                    bar_w - BAR_PAD_L - BAR_PAD_R)
            x += bar_w
            if bar.index == last_bar_index:     # 끝세로줄(겹세로줄)
                self.line(x - 0.7 * S, top, x - 0.7 * S, top + 4 * S, 1.2)
                self.staff.append(f'<rect x="{_n(x - 0.45 * S)}" y="{_n(top)}" '
                                  f'width="{_n(0.45 * S)}" height="{_n(4 * S)}" '
                                  f'fill="{INK}" stroke="none"/>')
            else:
                self.line(x, top, x, top + 4 * S, 1.3)

        for i in range(5):                      # 오선
            self.line(MARGIN_X, top + i * S, x, top + i * S, 1.0)
        self.line(MARGIN_X, top, MARGIN_X, top + 4 * S, 1.3)

        band_y, band_h = top - 2 * S, 8 * S
        self.wipes.append(f'<rect class="wipe" id="w{k}" x="{_n(x_start)}" '
                          f'y="{_n(band_y)}" width="0" height="{_n(band_h)}"/>')
        anchors.append((bars[-1].end_time, x))
        clean = []
        for t, ax in anchors:                   # 시각·위치가 모두 늘어나는 점만
            if not clean or (t > clean[-1][0] + 1e-9 and ax >= clean[-1][1]):
                clean.append((t, ax))
        self.timeline.append({
            "t0": round(bars[0].start_time, 6), "t1": round(bars[-1].end_time, 6),
            "x0": round(x_start, 1), "x1": round(x, 1),
            "y": round(band_y, 1), "h": round(band_h, 1),
            "anchors": [[round(t, 6), round(ax, 1)] for t, ax in clean],
        })
        return x

    def meter(self, meter, cx, top):
        self.glyph(str(meter.numerator), cx, top + S, 1.9 * S, DIGIT_FONT, True)
        self.glyph(str(meter.denominator), cx, top + 3 * S, 1.9 * S, DIGIT_FONT, True)

    # ── 마디 ─────────────────────────────────────────────────────
    def measure(self, k, bar, top, x0, usable):
        anchors, infos, accidentals = [], [], {}
        beams = {}
        for event in bar.events:
            if event.beam is not None:
                beams.setdefault(event.beam, []).append(event)
        beam_dir = {bid: self.stem_dir([p for e in grp for p in e.pitches])
                    for bid, grp in beams.items()}

        for event in bar.events:
            ex = x0 + usable * event.pos / bar.length
            if event.is_rest:
                self.rest(event, ex, top, x0 + usable / 2)
                anchors.append((event.time, x0 if event.bar_rest else ex))
                continue
            direction = beam_dir.get(event.beam) or self.stem_dir(event.pitches)
            info = self.note_head(event, ex, top, direction, accidentals)
            info["line"] = self.events[-1]["line"] = k
            infos.append(info)
            anchors.append((event.time, ex))

        for bid, group in beams.items():
            members = [info for info in infos if info["event"].beam == bid]
            self.beam(members)
        for info in infos:
            self.finish_note(info)
        return anchors

    @staticmethod
    def stem_dir(pitches):
        steps = [step_of(p) for p in pitches]
        # 가운데 줄에서 가장 먼 음이 아래쪽이면 기둥은 위로.
        return "up" if MID_STEP - min(steps) > max(steps) - MID_STEP else "down"

    def note_head(self, event, ex, top, direction, accidentals):
        steps = sorted(step_of(p) for p in event.pitches)
        ys = {s: self.y(top, s) for s in steps}
        parts = []
        # 2도 화음은 머리 하나를 기둥 반대쪽으로 비켜 놓는다.
        offsets, prev = {}, None
        for s in (steps if direction == "up" else reversed(steps)):
            clash = prev is not None and abs(s - prev) == 1 and offsets[prev] == 0
            offsets[s] = (1 if direction == "up" else -1) if clash else 0
            prev = s
        shift = 2 * HEAD_RX * 0.94

        whole = event.length >= 4 * self.upq
        hollow = event.length >= 2 * self.upq
        parts.append(f'<ellipse class="halo" cx="{_n(ex)}" '
                     f'cy="{_n((ys[steps[0]] + ys[steps[-1]]) / 2)}" '
                     f'rx="{_n(HEAD_RX * 2.2)}" '
                     f'ry="{_n(abs(ys[steps[0]] - ys[steps[-1]]) / 2 + HEAD_RY * 2.2)}"/>')

        # 덧줄 — 오선 밖의 음
        ledger = HEAD_RX * 1.7
        left = ex - ledger + min(0, min(offsets.values())) * shift
        right = ex + ledger + max(0, max(offsets.values())) * shift
        for s in range(BOTTOM_STEP - 2, steps[0] - 1, -2):
            self.line(left, self.y(top, s), right, self.y(top, s), 1.2)
        for s in range(TOP_STEP + 2, steps[-1] + 1, 2):
            self.line(left, self.y(top, s), right, self.y(top, s), 1.2)

        # 임시표 — 마디 안에서 같은 자리의 앞 음과 다를 때만 (붙임줄로 이어진 음은 생략)
        if not event.tie_prev:
            for p in event.pitches:
                s = step_of(p)
                want = "#" if _SHARP[p % 12] else "n"
                if accidentals.get(s, "n") != want:
                    acc_x = ex - HEAD_RX - 0.95 * S + min(0, offsets[s]) * shift
                    parts.append(self.accidental(want, acc_x, ys[s]))
                    accidentals[s] = want

        for s in steps:
            hx = ex + offsets[s] * shift
            if whole:
                parts.append(f'<ellipse class="ho" cx="{_n(hx)}" cy="{_n(ys[s])}" '
                             f'rx="{_n(0.82 * S)}" ry="{_n(0.5 * S)}" fill="none" '
                             f'stroke="{INK}" stroke-width="2.2"/>')
            else:
                tilt = f'transform="rotate({HEAD_TILT} {_n(hx)} {_n(ys[s])})"'
                if hollow:
                    parts.append(f'<ellipse class="ho" cx="{_n(hx)}" cy="{_n(ys[s])}" '
                                 f'rx="{_n(HEAD_RX - 0.6)}" ry="{_n(HEAD_RY - 0.6)}" '
                                 f'{tilt} fill="none" stroke="{INK}" stroke-width="1.6"/>')
                else:
                    parts.append(f'<ellipse class="hf" cx="{_n(hx)}" cy="{_n(ys[s])}" '
                                 f'rx="{_n(HEAD_RX)}" ry="{_n(HEAD_RY)}" {tilt} '
                                 f'fill="{INK}"/>')
            if event.length in self.dotted:     # 점 — 줄 위의 음이면 위 칸에
                dy = -S / 2 if (s - BOTTOM_STEP) % 2 == 0 else 0
                parts.append(f'<circle class="dt" cx="{_n(hx + HEAD_RX + 0.6 * S)}" '
                             f'cy="{_n(ys[s] + dy)}" r="{_n(0.19 * S)}" fill="{INK}"/>')

        low, high = ys[steps[0]], ys[steps[-1]]      # y 는 아래로 갈수록 크다
        middle = self.y(top, MID_STEP)
        stem = STEM_LEN + max(0, self.level(event.length) - 2) * 0.8 * S
        if direction == "up":
            sx, y_from = ex + HEAD_RX - 0.6, low
            y_tip = min(high - stem, middle)
        else:
            sx, y_from = ex - HEAD_RX + 0.6, high
            y_tip = max(low + stem, middle)
        info = {"event": event, "x": ex, "ys": ys, "steps": steps, "dir": direction,
                "sx": sx, "y_from": y_from, "y_tip": y_tip, "parts": parts,
                "whole": whole, "beamed": False, "id": f"e{len(self.events)}"}
        self.events.append({"id": info["id"], "t": round(event.time, 6),
                            "group": event.group})
        self.pieces.append(info)
        return info

    @staticmethod
    def accidental(kind, cx, cy):
        if kind == "#":
            return (f'<g class="ac" stroke="{INK}">'
                    f'<line x1="{_n(cx - 2.2)}" y1="{_n(cy - 12)}" x2="{_n(cx - 2.2)}" '
                    f'y2="{_n(cy + 13)}" stroke-width="1.3"/>'
                    f'<line x1="{_n(cx + 2.2)}" y1="{_n(cy - 13)}" x2="{_n(cx + 2.2)}" '
                    f'y2="{_n(cy + 12)}" stroke-width="1.3"/>'
                    f'<line x1="{_n(cx - 5)}" y1="{_n(cy - 2.5)}" x2="{_n(cx + 5)}" '
                    f'y2="{_n(cy - 5.5)}" stroke-width="3.2"/>'
                    f'<line x1="{_n(cx - 5)}" y1="{_n(cy + 5.5)}" x2="{_n(cx + 5)}" '
                    f'y2="{_n(cy + 2.5)}" stroke-width="3.2"/></g>')
        return (f'<g class="ac" stroke="{INK}">'
                f'<line x1="{_n(cx - 3)}" y1="{_n(cy - 13)}" x2="{_n(cx - 3)}" '
                f'y2="{_n(cy + 5)}" stroke-width="1.3"/>'
                f'<line x1="{_n(cx + 3)}" y1="{_n(cy - 5)}" x2="{_n(cx + 3)}" '
                f'y2="{_n(cy + 13)}" stroke-width="1.3"/>'
                f'<line x1="{_n(cx - 3)}" y1="{_n(cy - 2)}" x2="{_n(cx + 3)}" '
                f'y2="{_n(cy - 4)}" stroke-width="3"/>'
                f'<line x1="{_n(cx - 3)}" y1="{_n(cy + 4)}" x2="{_n(cx + 3)}" '
                f'y2="{_n(cy + 2)}" stroke-width="3"/></g>')

    def beam(self, members):
        """빔 — 기둥 끝을 한 높이(가장 바깥 기둥)로 맞추고 굵은 띠를 긋는다."""
        if len(members) < 2:
            return
        up = members[0]["dir"] == "up"
        tip = (min if up else max)(m["y_tip"] for m in members)
        inward = 1 if up else -1
        for m in members:
            m["y_tip"], m["beamed"] = tip, True
        first, last = members[0]["sx"], members[-1]["sx"]
        self.staff.append(self._band(first, last, tip, inward))
        # 16분음표부터는 빔을 한 줄씩 더 — 이웃도 그 이상이면 잇고, 혼자면 짧게
        levels = [self.level(m["event"].length) for m in members]
        for lv in range(2, max(levels) + 1):
            y_lv = tip + inward * BEAM_GAP * (lv - 1)
            for i, m in enumerate(members):
                if levels[i] < lv:
                    continue
                if i + 1 < len(members) and levels[i + 1] >= lv:
                    self.staff.append(self._band(m["sx"], members[i + 1]["sx"],
                                                 y_lv, inward))
                elif (i == 0 or levels[i - 1] < lv) and (i + 1 >= len(members)
                                                        or levels[i + 1] < lv):
                    stub = -1.1 * S if i == len(members) - 1 else 1.1 * S
                    self.staff.append(self._band(m["sx"], m["sx"] + stub,
                                                 y_lv, inward))

    @staticmethod
    def _band(x1, x2, y_outer, inward):
        a, b = sorted((x1, x2))
        y1, y2 = sorted((y_outer, y_outer + inward * BEAM_T))
        return (f'<rect x="{_n(a)}" y="{_n(y1)}" width="{_n(b - a + 0.6)}" '
                f'height="{_n(y2 - y1)}" fill="{INK}" stroke="none"/>')

    def finish_note(self, info):
        """기둥·꼬리를 붙여 음표 그룹을 완성한다."""
        event, parts = info["event"], info["parts"]
        if not info["whole"]:
            parts.append(f'<line class="st" x1="{_n(info["sx"])}" '
                         f'y1="{_n(info["y_from"])}" x2="{_n(info["sx"])}" '
                         f'y2="{_n(info["y_tip"])}" stroke="{INK}" stroke-width="1.2"/>')
            flags = self.level(event.length)
            if flags and not info["beamed"]:
                down = 1 if info["dir"] == "up" else -1  # 꼬리는 머리 쪽으로 늘어진다
                for f in range(flags):
                    parts.append(self._flag(info["sx"], info["y_tip"]
                                            + down * f * 0.8 * S, down))
        self.notes.append(f'<g class="ev" id="{info["id"]}">{"".join(parts)}</g>')

    @staticmethod
    def _flag(x, y, down):
        def p(dx, dy):
            return f"{_n(x + dx * S)},{_n(y + down * dy * S)}"
        return (f'<path class="fl" fill="{INK}" d="M{p(0, 0)} '
                f'C{p(0.1, 0.9)} {p(1.35, 1.25)} {p(1.2, 2.7)} '
                f'C{p(1.15, 1.75)} {p(0.5, 1.45)} {p(0, 1.15)}Z"/>')

    def rest(self, event, ex, top, bar_mid):
        if event.bar_rest or event.length >= 4 * self.upq:  # 온쉼표: 넷째 줄에 매달린다
            cx = bar_mid if event.bar_rest else ex
            self.staff.append(f'<rect x="{_n(cx - 0.6 * S)}" y="{_n(top + S)}" '
                              f'width="{_n(1.2 * S)}" height="{_n(0.5 * S)}" '
                              f'fill="{INK}" stroke="none"/>')
            return
        if event.length >= 2 * self.upq:              # 2분쉼표: 셋째 줄 위에 얹힌다
            self.staff.append(f'<rect x="{_n(ex - 0.6 * S)}" y="{_n(top + 1.5 * S)}" '
                              f'width="{_n(1.2 * S)}" height="{_n(0.5 * S)}" '
                              f'fill="{INK}" stroke="none"/>')
        else:
            kind = min(self.level(event.length), len(REST_GLYPHS) - 1)
            height, center = REST_BOX[kind]
            self.glyph(REST_GLYPHS[kind], ex, top + center * S, height * S)
        if event.length in self.dotted:
            self.staff.append(f'<circle cx="{_n(ex + 1.1 * S)}" cy="{_n(top + 1.5 * S)}" '
                              f'r="{_n(0.19 * S)}" fill="{INK}" stroke="none"/>')

    # ── 붙임줄 ───────────────────────────────────────────────────
    def ties(self, line_ends, line_starts):
        for i, info in enumerate(self.pieces):
            if not info["event"].tie_next or i + 1 >= len(self.pieces):
                continue
            nxt = self.pieces[i + 1]
            for s in info["steps"]:
                if s not in nxt["ys"]:
                    continue
                y = info["ys"][s]
                below = info["dir"] == "up"
                if nxt["line"] == info["line"]:
                    self.staff.append(self._tie(info["x"] + HEAD_RX * 0.8,
                                                nxt["x"] - HEAD_RX * 0.8, y, below))
                else:                           # 줄이 바뀌면 줄 끝과 다음 줄 처음에 반씩
                    self.staff.append(self._tie(info["x"] + HEAD_RX * 0.8,
                                                line_ends[info["line"]] - 0.3 * S,
                                                y, below))
                    ny = nxt["ys"][s]
                    self.staff.append(self._tie(line_starts[nxt["line"]],
                                                nxt["x"] - HEAD_RX * 0.8, ny, below))

    @staticmethod
    def _tie(x1, x2, y, below):
        sign = 1 if below else -1
        y0 = y + sign * 0.55 * S
        mid = (x1 + x2) / 2
        bend = sign * min(1.1 * S, 0.25 * abs(x2 - x1) + 0.4 * S)
        return (f'<path d="M{_n(x1)},{_n(y0)} Q{_n(mid)},{_n(y0 + bend)} {_n(x2)},{_n(y0)} '
                f'Q{_n(mid)},{_n(y0 + bend * 0.62)} {_n(x1)},{_n(y0)}Z" '
                f'fill="{INK}" stroke="{INK}" stroke-width="0.4"/>')


def build_sheet(song_path, bars_per_line=BARS_PER_LINE):
    """곡 파일 → 브라우저가 그릴 악보 dict (JSON 으로 그대로 보낼 수 있다).

    {
      "svg": "<svg …>",             # 오선 악보. viewBox 좌표 = width × height
      "width", "height", "line_height", "lines",
      "timeline": [{"t0", "t1", "x0", "x1", "y", "h", "anchors": [[초, x], …]}, …],
      "events": [{"id", "t", "group"}, …],   # 음표 조각(빨갛게 바꿀 대상), 시각 순
      "group_notes": [덩어리마다 음 수],     # 연습 모드 진행 표시용
      "end": 마지막 마디가 끝나는 초,
    }
    시각은 모두 악보상 초다(실제 시각 = 악보상 시각 / speed).
    """
    notation = build_notation(song_path)
    bars = notation.bars
    if not bars:
        return {"svg": "", "width": WIDTH, "height": 0, "line_height": LINE_H,
                "lines": 0, "timeline": [], "events": [], "group_notes": [],
                "end": 0.0}

    eng = _Engraver(notation.upq, notation.dotted)
    systems = [bars[i:i + bars_per_line] for i in range(0, len(bars), bars_per_line)]
    line_ends, line_starts = [], []
    for k, system in enumerate(systems):
        line_ends.append(eng.system(k, system, bars_per_line, bars[-1].index))
        line_starts.append(eng.timeline[-1]["x0"])
    eng.ties(line_ends, line_starts)

    height = len(systems) * LINE_H
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" class="score" '
           f'viewBox="0 0 {_n(WIDTH)} {_n(height)}" width="{_n(WIDTH)}" '
           f'height="{_n(height)}"><g class="wipes">{"".join(eng.wipes)}</g>'
           f'<g class="staff">{"".join(eng.staff)}</g>'
           f'<g class="notes">{"".join(eng.notes)}</g></svg>')
    return {
        "svg": svg,
        "width": WIDTH,
        "height": height,
        "line_height": LINE_H,
        "lines": len(systems),
        "timeline": eng.timeline,
        "events": eng.events,
        "group_notes": notation.group_notes,
        "end": round(bars[-1].end_time, 6),
    }
