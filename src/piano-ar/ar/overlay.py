import cv2
import time


BASE_MIDI_NOTE = 60
MIN_VISIBLE_DURATION = 0.25

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


def _midi_note_to_key(note):
    offset = note - BASE_MIDI_NOTE

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


def _draw_note(output, key, is_white, progress):
    x1, y1, x2, y2 = key
    cx = int((x1 + x2) / 2)
    cy = int(y1 + (y2 - y1) * 0.75) if is_white else int((y1 + y2) / 2)

    key_w = x2 - x1
    key_h = y2 - y1
    radius = int(min(key_w, key_h) * (0.22 if is_white else 0.25))

    alpha, color = _get_alpha_and_color(progress)

    layer = output.copy()
    cv2.circle(layer, (cx, cy), radius, color, -1)
    cv2.addWeighted(layer, alpha, output, 1.0 - alpha, 0, output)


def render(frame, whites, blacks, notes=None, playback_time=None):
    global _start_time

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

        key_ref = _midi_note_to_key(int(note_event["note"]))

        if key_ref is None:
            continue

        is_white, key_idx = key_ref
        keys = whites if is_white else blacks

        if key_idx >= len(keys):
            continue

        progress = elapsed / duration
        _draw_note(output, keys[key_idx], is_white, progress)

    return output
