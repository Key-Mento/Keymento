import cv2
import time

# 테스트용 키 순서 (순서대로 애니메이션)
TEST_WHITE = [10, 11, 12]
TEST_BLACK = [0, 1, 2]

DURATION = 1.0      # 각 음 애니메이션 길이 (초)
INTERVAL = 1.0      # 다음 음까지 간격 (초)

# 시퀀스: (is_white, key_index, start_time_offset)
_RAW_SEQUENCE = (
    [(True,  k) for k in TEST_WHITE] +
    [(False, k) for k in TEST_BLACK]
)
SEQUENCE = [
    (is_white, key_idx, i * INTERVAL)
    for i, (is_white, key_idx) in enumerate(_RAW_SEQUENCE)
]

_start_time = None   # render() 호출


def _get_alpha_and_color(elapsed: float) -> tuple[float, tuple[int, int, int]]:
    """
    elapsed: 해당 음의 애니메이션 시작 후 경과 시간 (0 ~ DURATION)
    반환: (alpha 0~1, BGR color)

    0.0s  → 투명
    0.5s → 진한 초록  (peak)
    1.0s  → 투명
    """
    t = min(elapsed / DURATION, 1.0)   # 0.0 ~ 1.0

    # 0→0.5 : fade-in,  0.5→1.0 : fade-out
    phase = t / 0.5 if t < 0.5 else (1.0 - t) / 0.5  

    # 연두 (50, 255, 154) ─→ 진한 초록 (0, 200, 0)  
    b = int(50  * (1 - phase))
    g = int(255 * (1 - phase) + 200 * phase)
    r = int(154 * (1 - phase))
    return phase, (b, g, r)


def render(frame, whites, blacks):
    global _start_time
    if _start_time is None:
        _start_time = time.time()

    now = time.time()
    elapsed_total = now - _start_time

    output = frame.copy()

    for is_white, key_idx, offset in SEQUENCE:
        anim_elapsed = elapsed_total - offset
        if anim_elapsed < 0 or anim_elapsed > DURATION:
            continue   # 아직 시작 전 or 이미 끝남

        keys = whites if is_white else blacks
        if key_idx >= len(keys):
            continue

        x1, y1, x2, y2 = keys[key_idx]
        cx = int((x1 + x2) / 2)
        cy = int(y1 + (y2 - y1) * 0.75) if is_white else int((y1 + y2) / 2)

        key_w = x2 - x1
        key_h = y2 - y1
        r = int(min(key_w, key_h) * (0.22 if is_white else 0.25))

        alpha, color = _get_alpha_and_color(anim_elapsed)

        layer = output.copy()
        cv2.circle(layer, (cx, cy), r, color, -1)
        cv2.addWeighted(layer, alpha, output, 1.0 - alpha, 0, output)

    return output