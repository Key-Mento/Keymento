import os

import cv2

# Windows 기본 백엔드(MSMF)는 일부 USB 웹캠에서 프레임을 못 받고
# 한참 멈춘다. DSHOW 는 같은 장치를 바로 연다.
BACKEND = cv2.CAP_DSHOW if os.name == "nt" else cv2.CAP_ANY

PREFERRED_SIZE = (1280, 720)


def _open(index, size):
    cap = cv2.VideoCapture(index, BACKEND)
    if not cap.isOpened():
        return None

    if size is not None:
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, size[0])
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, size[1])

    # 지원하지 않는 해상도를 요청하면 열리기만 하고 프레임이 안 온다.
    ok, _ = cap.read()
    if not ok:
        cap.release()
        return None

    return cap


def open_camera(index=0):
    cap = _open(index, PREFERRED_SIZE) or _open(index, None)

    if cap is None:
        raise Exception(f"카메라 연결 실패 (장치 번호 {index})")

    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    print(f"📷 카메라 {index}번: {w}x{h}")

    return cap
