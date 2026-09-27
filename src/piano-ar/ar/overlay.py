import math
import time

import cv2


# 워프된 화면의 맨 왼쪽 흰건반이 내는 MIDI 번호. 이 값이 실제 건반과
# 어긋나면 마커가 통째로 옥타브 단위로 밀려, 짚어 준 건반을 눌러도 판정이
# 안 맞는다(연습 모드는 그 자리에서 영원히 대기한다).
#
# 반드시 도(C)여야 한다 — 아래 WHITE_STEPS/BLACK_STEPS 가 한 옥타브를
# 도에서 시작하는 것으로 보고 자리를 센다.
#
# 기본 48(C3)인 이유: 여기 쓰는 Keystation Mini 32 의 맨 왼쪽 키가 48 을
# 보낸다(run base 로 실측). 흰 19 + 검은 13 = 32키가 C3~G5(48~79)로
# 그 건반과 정확히 맞는다.
#
# songs/ 의 곡들은 60 부터 시작하므로 화면 왼쪽 한 옥타브에는 마커가 뜨지
# 않는다. 이는 기준음이 틀려서가 아니라 곡이 그 음역을 안 쓰기 때문이다.
# 건반이 다르면 run base 로 다시 재면 된다.
DEFAULT_BASE_MIDI_NOTE = 48  # C3
FIRST_VISIBLE_MIDI_NOTE = DEFAULT_BASE_MIDI_NOTE
BASE_MIDI_NOTE = FIRST_VISIBLE_MIDI_NOTE  # Backward-compatible alias

# 일반 모드 타이밍 표시. 경계는 perform/session.py 의 판정과 같은 값이다
# (PERFECT_WINDOW ±0.3초, 판정 창 HIT_WINDOW ±0.9초). 모두 실제 시간(초)
# 기준 — 악보 시각 차를 속도 배율로 나눠 비교한다.
#
#   목표 1.5초 전   하늘색 점이 뜨고 바깥 링이 줄어들기 시작한다
#   ±0.9초 안       노랑 — 지금 쳐도 판정은 되지만 Great/Good
#   ±0.3초 안       초록 — Perfect. 링이 점에 닿는 순간이 정확한 타이밍
#   +0.9초 뒤       사라진다(Miss)
# 친 음은 곧바로 지우고 등급 색으로 잠깐 번쩍인다.
PERFECT_WINDOW = 0.3
HIT_WINDOW = 0.9
APPROACH_SECONDS = 1.5
APPROACH_RING_SCALE = 3.0       # 링이 줄어들기 시작할 때 반지름(점의 배수)
HIT_FLASH_SECONDS = 0.35

COLOR_APPROACH = (255, 170, 60)    # 하늘색 — 흰건반·검은건반 위 모두 보인다
COLOR_IN_WINDOW = (0, 220, 255)
COLOR_PERFECT = (0, 255, 0)
GRADE_COLORS = {
    "Perfect": COLOR_PERFECT,
    "Great": COLOR_IN_WINDOW,
    "Good": (0, 140, 255),
}

# Practice mode holds one target until it is played, so the marker pulses
# instead of fading along the score clock. Amber keeps it distinct from the
# green markers driven by playback time.
ACTIVE_COLOR = (0, 190, 255)
ACTIVE_PULSE_HZ = 1.6
ACTIVE_ALPHA_RANGE = (0.45, 0.85)

WHITE_STEPS = {
    0: 0,
    2: 1,
    4: 2,
    5: 3,
    7: 4,
    9: 5,
    11: 6,
}
BLACK_STEPS = {
    1: 0,
    3: 1,
    6: 2,
    8: 3,
    10: 4,
}

TEST_SEQUENCE = [
    {"note": 60, "time": 0.0, "duration": 1.0},
    {"note": 62, "time": 1.0, "duration": 1.0},
    {"note": 64, "time": 2.0, "duration": 1.0},
]

_start_time = None


def visible_range(whites, blacks, base_note=None):
    """화면에 그릴 수 있는 MIDI 음의 (최저, 최고) 범위.

    건반 배치(흰 19 + 검은 13)가 정하는 실제 한계를 되돌려 준다. 곡의 음이
    이 밖으로 나가면 그 음은 마커가 아예 안 뜨므로, 곡을 고를 때 미리
    걸러 내는 데 쓴다.
    """
    low = FIRST_VISIBLE_MIDI_NOTE if base_note is None else base_note
    high = low
    note = low

    while True:
        resolved = _midi_note_to_key(note, base_note)
        if resolved is None:
            break
        is_white, index = resolved
        if index >= len(whites if is_white else blacks):
            break
        high = note
        note += 1

    return low, high


def _midi_note_to_key(note, base_note=None):
    offset = note - (FIRST_VISIBLE_MIDI_NOTE if base_note is None
                     else base_note)

    if offset < 0:
        return None

    octave = offset // 12
    semitone = offset % 12

    if semitone in WHITE_STEPS:
        return True, octave * 7 + WHITE_STEPS[semitone]

    if semitone in BLACK_STEPS:
        return False, octave * 5 + BLACK_STEPS[semitone]

    return None


def _marker_geometry(key, is_white):
    """건반 안에 마커를 그릴 중심과 반지름."""
    x1, y1, x2, y2 = key
    cx = int((x1 + x2) / 2)
    cy = int(y1 + (y2 - y1) * 0.75) if is_white else int((y1 + y2) / 2)
    radius = int(min(x2 - x1, y2 - y1) * (0.22 if is_white else 0.25))
    return (cx, cy), radius


def _draw_marker(output, key, is_white, alpha, color):
    center, radius = _marker_geometry(key, is_white)

    layer = output.copy()
    cv2.circle(layer, center, radius, color, -1)
    cv2.addWeighted(layer, alpha, output, 1.0 - alpha, 0, output)


def _timing_style(dt, close):
    """목표 시각 기준 경과 dt(초, 음수 = 전) → (색, 불투명도)."""
    if dt < -HIT_WINDOW:
        # 다가오는 중 — 창이 열릴 때까지 조금씩 진해진다.
        lead = APPROACH_SECONDS - HIT_WINDOW
        near = 1.0 - min((-dt - HIT_WINDOW) / lead, 1.0) if lead > 0 else 1.0
        return COLOR_APPROACH, 0.3 + 0.3 * near

    if dt < -PERFECT_WINDOW:
        return COLOR_IN_WINDOW, 0.7

    if dt <= PERFECT_WINDOW:
        return COLOR_PERFECT, 0.95

    # 늦음 — 창이 닫히는 쪽으로 옅어진다.
    span = max(close - PERFECT_WINDOW, 1e-6)
    return COLOR_IN_WINDOW, 0.7 - 0.5 * min((dt - PERFECT_WINDOW) / span, 1.0)


def _draw_timed_note(output, key, is_white, dt, close):
    """타이밍 마커: 점의 색으로 판정 구간을, 줄어드는 링으로 남은 시간을."""
    color, alpha = _timing_style(dt, close)
    _draw_marker(output, key, is_white, alpha, color)
    center, radius = _marker_geometry(key, is_white)

    if dt < 0:
        # 링은 목표 시각에 정확히 점 가장자리에 닿는다.
        shrink = min(-dt / APPROACH_SECONDS, 1.0)
        ring = int(radius * (1.0 + (APPROACH_RING_SCALE - 1.0) * shrink))
        cv2.circle(output, center, ring, color, 2, cv2.LINE_AA)
    elif dt <= PERFECT_WINDOW:
        cv2.circle(output, center, radius + 2, (255, 255, 255), 2, cv2.LINE_AA)


def _draw_hit_flash(output, key, is_white, grade, since):
    """방금 친 음 — 등급 색이 퍼지며 사라진다."""
    center, radius = _marker_geometry(key, is_white)
    fade = since / HIT_FLASH_SECONDS
    color = GRADE_COLORS.get(grade, COLOR_PERFECT)
    _draw_marker(output, key, is_white, 0.8 * (1.0 - fade), color)
    cv2.circle(output, center, int(radius * (1.0 + fade)), color, 2,
               cv2.LINE_AA)


def _resolve_key(note, whites, blacks, base_note=None):
    """Return (keys, index, is_white) for a MIDI note, or None if off-screen."""
    key_ref = _midi_note_to_key(int(note), base_note)

    if key_ref is None:
        return None

    is_white, key_idx = key_ref
    keys = whites if is_white else blacks

    if key_idx >= len(keys):
        return None

    return keys[key_idx], is_white


def _render_active(frame, whites, blacks, active_notes, base_note=None):
    """Pulse the keys that must be played right now (practice mode)."""
    output = frame.copy()

    low, high = ACTIVE_ALPHA_RANGE
    wave = 0.5 + 0.5 * math.sin(time.time() * ACTIVE_PULSE_HZ * 2 * math.pi)
    alpha = low + (high - low) * wave

    for note in active_notes:
        resolved = _resolve_key(note, whites, blacks, base_note)

        if resolved is None:
            continue

        key, is_white = resolved
        _draw_marker(output, key, is_white, alpha, ACTIVE_COLOR)

    return output


def render(frame, whites, blacks, notes=None, playback_time=None,
           active_notes=None, base_note=None, speed=1.0, hits=None):
    """Draw note markers over the warped keyboard view.

    active_notes overrides the score clock: the listed MIDI notes pulse until
    they are cleared. Practice mode needs this because its clock does not
    advance -- the same target must stay visible until it is played.

    Otherwise notes are drawn as timing markers around their target time
    (see PERFECT_WINDOW/HIT_WINDOW above). playback_time is the score time;
    speed converts score seconds to real seconds, which is what the
    judgement windows are measured in. hits maps (group, note) to
    (grade, hit_at) for notes already played; they are removed after a
    short flash in their grade color.

    base_note is the MIDI number of the leftmost white key. None uses the
    module default; pass the configured value so the markers line up with
    whatever octave the physical keyboard is actually sending.
    """
    global _start_time

    if active_notes is not None:
        return _render_active(frame, whites, blacks, active_notes, base_note)

    if _start_time is None:
        _start_time = time.time()

    if playback_time is None:
        playback_time = time.time() - _start_time

    if notes is None:
        notes = TEST_SEQUENCE

    speed = speed if speed > 0 else 1.0
    now = time.time()
    output = frame.copy()
    # 같은 건반에 음이 이어 나오면 앞 음이 끝날 때까지 앞 음만 그린다.
    claimed = set()

    for note_event in notes:
        start_time = float(note_event.get("time", 0.0))
        dt = (playback_time - start_time) / speed

        if dt < -APPROACH_SECONDS:
            break  # 악보는 시간순이다 — 뒤는 더 나중

        note = int(note_event["note"])

        if note in claimed:
            continue

        hit = hits.get((note_event.get("group"), note)) if hits else None

        if hit is not None:
            grade, hit_at = hit
            since = now - hit_at
            if since < HIT_FLASH_SECONDS:
                resolved = _resolve_key(note, whites, blacks, base_note)
                if resolved is not None:
                    claimed.add(note)
                    _draw_hit_flash(output, *resolved, grade, since)
            continue

        # 같은 음이 곧 다시 나오면 판정 창도 그 음의 목표 시각에 닫힌다
        # (session.TimedJudge._windows).
        next_same = note_event.get("next_same")
        close = HIT_WINDOW
        if next_same is not None:
            close = min(close, (float(next_same) - start_time) / speed)

        if dt > close:
            continue

        resolved = _resolve_key(note, whites, blacks, base_note)

        if resolved is None:
            continue

        claimed.add(note)
        _draw_timed_note(output, *resolved, dt, close)

    return output
