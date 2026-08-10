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
MIN_VISIBLE_DURATION = 0.25

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


def _get_alpha_and_color(progress):
    fade = 0.25

    if progress < fade:
        phase = progress / fade
    elif progress > 1.0 - fade:
        phase = (1.0 - progress) / fade
    else:
        phase = 1.0

    phase = max(0.0, min(phase, 1.0))

    b = int(50 * (1 - phase))
    g = int(255 * (1 - phase) + 200 * phase)
    r = int(154 * (1 - phase))

    return phase, (b, g, r)


def _draw_marker(output, key, is_white, alpha, color):
    x1, y1, x2, y2 = key
    cx = int((x1 + x2) / 2)
    cy = int(y1 + (y2 - y1) * 0.75) if is_white else int((y1 + y2) / 2)

    key_w = x2 - x1
    key_h = y2 - y1
    radius = int(min(key_w, key_h) * (0.22 if is_white else 0.25))

    layer = output.copy()
    cv2.circle(layer, (cx, cy), radius, color, -1)
    cv2.addWeighted(layer, alpha, output, 1.0 - alpha, 0, output)


def _draw_note(output, key, is_white, progress):
    alpha, color = _get_alpha_and_color(progress)
    _draw_marker(output, key, is_white, alpha, color)


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
           active_notes=None, base_note=None):
    """Draw note markers over the warped keyboard view.

    active_notes overrides the score clock: the listed MIDI notes pulse until
    they are cleared. Practice mode needs this because its clock does not
    advance -- the same target must stay visible until it is played.

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

    output = frame.copy()

    for note_event in notes:
        start_time = float(note_event.get("time", 0.0))
        duration = max(
            float(note_event.get("duration", 0.0)),
            MIN_VISIBLE_DURATION
        )
        elapsed = playback_time - start_time

        if elapsed < 0 or elapsed > duration:
            continue

        resolved = _resolve_key(note_event["note"], whites, blacks, base_note)

        if resolved is None:
            continue

        key, is_white = resolved
        progress = elapsed / duration
        _draw_note(output, key, is_white, progress)

    return output
