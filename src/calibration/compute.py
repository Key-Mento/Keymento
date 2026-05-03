import numpy as np

WHITE_PITCH_CLASSES = {0, 2, 4, 5, 7, 9, 11}
BLACK_PITCH_CLASSES = {1, 3, 6, 8, 10}

_NOTE_NAMES = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B']

BLACK_WIDTH_RATIO = 0.6
BLACK_HEIGHT_RATIO = 0.6


def is_black(note: int) -> bool:
    return note % 12 in BLACK_PITCH_CLASSES


def note_name(note: int) -> str:
    return f"{_NOTE_NAMES[note % 12]}{note // 12 - 1}"


def white_notes_sequence(start_note: int, count: int) -> list:
    if start_note % 12 in BLACK_PITCH_CLASSES:
        raise ValueError(f"start_note {start_note} ({note_name(start_note)})는 흑건입니다")
    notes = []
    n = start_note
    while len(notes) < count:
        if not is_black(n):
            notes.append(n)
        n += 1
    return notes


def compute_key_regions(
    corners: list,
    num_white_keys: int,
    start_note: int,
) -> dict:
    """
    4점(TL→TR→BR→BL)과 건반 정보로 각 건반의 화면 좌표를 계산한다.
    반환: {midi_note: [(x,y), (x,y), (x,y), (x,y)]}  — 좌상→우상→우하→좌하 순
    """
    if len(corners) != 4:
        raise ValueError("corners는 정확히 4점이어야 합니다")
    if num_white_keys < 1:
        raise ValueError("num_white_keys는 1 이상이어야 합니다")

    white_notes = white_notes_sequence(start_note, num_white_keys)
    tl, tr, br, bl = [np.array(p, dtype=np.float32) for p in corners]
    top_step = (tr - tl) / num_white_keys
    bot_step = (br - bl) / num_white_keys

    regions = {}

    for i, note in enumerate(white_notes):
        p = [
            tl + top_step * i,
            tl + top_step * (i + 1),
            bl + bot_step * (i + 1),
            bl + bot_step * i,
        ]
        regions[note] = [tuple(v.astype(int)) for v in p]

    half_top = top_step * BLACK_WIDTH_RATIO / 2
    half_bot = bot_step * BLACK_WIDTH_RATIO / 2

    for i in range(num_white_keys - 1):
        if white_notes[i + 1] - white_notes[i] != 2:
            continue  # E-F, B-C 사이에는 흑건 없음

        black_note = white_notes[i] + 1
        mid_top = tl + top_step * (i + 1)
        mid_bot = bl + bot_step * (i + 1)

        top_l = mid_top - half_top
        top_r = mid_top + half_top
        bot_l = top_l + (mid_bot - half_bot - top_l) * BLACK_HEIGHT_RATIO
        bot_r = top_r + (mid_bot + half_bot - top_r) * BLACK_HEIGHT_RATIO

        regions[black_note] = [tuple(v.astype(int)) for v in (top_l, top_r, bot_r, bot_l)]

    return regions
