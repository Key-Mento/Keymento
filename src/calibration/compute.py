# ──────────────────────────────────────────────────────────────────────────────
# compute.py — 건반 좌표 계산 (순수 함수 모듈)
#
# 이 파일은 OpenCV나 UI에 전혀 의존하지 않는다.
# 덕분에 웹캠 없이도 단독 테스트가 가능하고,
# 나중에 UI를 바꿔도 이 파일은 그대로 쓸 수 있다.
# ──────────────────────────────────────────────────────────────────────────────

import numpy as np

# 한 옥타브(0~11) 안에서 흰건/흑건에 해당하는 pitch class
# 예) C=0, C#=1, D=2 ... B=11
WHITE_PITCH_CLASSES = {0, 2, 4, 5, 7, 9, 11}
BLACK_PITCH_CLASSES = {1, 3, 6, 8, 10}

# MIDI 노트 번호 → 이름 변환용 (C4 = 60)
_NOTE_NAMES = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B']

# 흑건의 크기 비율 (흰건 대비)
# 실제 피아노 기준: 너비 약 60%, 높이 약 60%
BLACK_WIDTH_RATIO = 0.6
BLACK_HEIGHT_RATIO = 0.6


def is_black(note: int) -> bool:
    """MIDI 노트가 흑건인지 확인한다."""
    return note % 12 in BLACK_PITCH_CLASSES


def note_name(note: int) -> str:
    """MIDI 노트 번호를 이름으로 변환한다. 예) 60 → 'C4'"""
    return f"{_NOTE_NAMES[note % 12]}{note // 12 - 1}"


def white_notes_sequence(start_note: int, count: int) -> list:
    """
    start_note부터 시작해서 흰건만 count개 추려 MIDI 번호 리스트로 반환한다.

    예) start_note=60(C4), count=14 → [60, 62, 64, 65, 67, 69, 71, 72, ...]
    흑건(C#, D# 등)은 건너뛰고 흰건만 수집한다.
    """
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
    사용자가 클릭한 4점으로부터 각 건반의 화면 좌표(사각형 4점)를 계산한다.

    동작 원리:
      1. 4점(TL→TR→BR→BL)으로 건반 전체 영역을 정의한다.
      2. 윗변과 아랫변을 흰건 수만큼 균등 분할해 흰건 사각형을 만든다.
      3. 흰건 경계선을 기준으로 흑건 사각형을 계산한다.
         - 흑건은 두 흰건 경계선 중앙에, 흰건 너비의 60%로 배치
         - 높이는 건반 전체 높이의 60%만 차지 (나머지 아래는 흰건 영역)
         - E-F, B-C 사이에는 흑건이 없으므로 건너뜀

    반환값: {midi_note: [(x,y), (x,y), (x,y), (x,y)]}
            각 사각형은 좌상→우상→우하→좌하 순서
    """
    if len(corners) != 4:
        raise ValueError("corners는 정확히 4점이어야 합니다")
    if num_white_keys < 1:
        raise ValueError("num_white_keys는 1 이상이어야 합니다")

    white_notes = white_notes_sequence(start_note, num_white_keys)

    # 4점을 numpy 벡터로 변환 (소수점 연산을 위해 float32)
    tl, tr, br, bl = [np.array(p, dtype=np.float32) for p in corners]

    # 흰건 하나의 너비에 해당하는 벡터 (윗변/아랫변 각각)
    top_step = (tr - tl) / num_white_keys
    bot_step = (br - bl) / num_white_keys

    regions = {}

    # ── 흰건 좌표 계산 ──────────────────────────────────────────────────────────
    for i, note in enumerate(white_notes):
        # i번째 흰건의 좌상·우상·우하·좌하 좌표
        p = [
            tl + top_step * i,        # 좌상
            tl + top_step * (i + 1),  # 우상
            bl + bot_step * (i + 1),  # 우하
            bl + bot_step * i,        # 좌하
        ]
        regions[note] = [tuple(v.astype(int)) for v in p]

    # ── 흑건 좌표 계산 ──────────────────────────────────────────────────────────
    # 흑건의 반너비(흰건 경계선을 중심으로 좌우로 얼만큼 뻗을지)
    half_top = top_step * BLACK_WIDTH_RATIO / 2
    half_bot = bot_step * BLACK_WIDTH_RATIO / 2

    for i in range(num_white_keys - 1):
        # 인접한 두 흰건의 간격이 2반음이어야 흑건이 존재
        # 간격이 1이면 E-F 또는 B-C 사이 → 흑건 없음
        if white_notes[i + 1] - white_notes[i] != 2:
            continue

        black_note = white_notes[i] + 1  # 두 흰건 사이의 반음

        # 두 흰건의 경계선 위/아래 중점
        mid_top = tl + top_step * (i + 1)
        mid_bot = bl + bot_step * (i + 1)

        # 흑건의 4꼭짓점
        top_l = mid_top - half_top
        top_r = mid_top + half_top
        # 흑건 아랫변: 윗변에서 전체 높이의 60% 내려간 지점
        bot_l = top_l + (mid_bot - half_bot - top_l) * BLACK_HEIGHT_RATIO
        bot_r = top_r + (mid_bot + half_bot - top_r) * BLACK_HEIGHT_RATIO

        regions[black_note] = [tuple(v.astype(int)) for v in (top_l, top_r, bot_r, bot_l)]

    return regions
