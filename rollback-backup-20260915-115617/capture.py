import os

import cv2


# Windows 기본 백엔드(MSMF)는 일부 USB 웹캠에서 프레임을 못 받고
# 한참 멈출 수 있으므로 DSHOW를 사용한다.
BACKEND = cv2.CAP_DSHOW if os.name == "nt" else cv2.CAP_ANY

PREFERRED_SIZE = (1280, 720)


def _open(index, size):
    cap = cv2.VideoCapture(index, BACKEND)

    if not cap.isOpened():
        cap.release()
        return None

    if size is not None:
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, size[0])
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, size[1])

    # 카메라가 열려도 실제 프레임이 들어오지 않을 수 있으므로 확인한다.
    for _ in range(5):
        try:
            ok, frame = cap.read()

            if ok and frame is not None and frame.size > 0:
                return cap
        except cv2.error:
            break

    cap.release()
    return None


def open_camera(index=None):
    """카메라 번호가 지정되지 않으면 1번을 먼저 시도하고 0번을 시도한다."""

    candidates = (1, 0) if index is None else (index,)

    for candidate in candidates:
        # 우선 1280x720으로 시도한다.
        cap = _open(candidate, PREFERRED_SIZE)

        # 지원하지 않는 경우 기본 해상도로 다시 시도한다.
        if cap is None:
            cap = _open(candidate, None)

        if cap is not None:
            w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

            print(f"📷 카메라 {candidate}번: {w}x{h}")
            return cap

    tried = ", ".join(str(candidate) for candidate in candidates)

    raise RuntimeError(
        f"카메라 연결 실패 (시도한 번호: {tried}). "
        "연결 상태를 확인하거나 --camera 번호를 지정하세요."
    )