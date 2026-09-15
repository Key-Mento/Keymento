import cv2

def open_camera(index=0):
    cap = cv2.VideoCapture(index)

    if not cap.isOpened():
        raise Exception("카메라 연결 실패")

    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)

    return cap
