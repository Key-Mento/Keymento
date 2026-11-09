import cv2
import sys
import time


class Camera:
    """Keep the active device and its index together when switching cameras."""

    def __init__(self, cap, index):
        self._cap = cap
        self.index = index
        # 연주자 맞은편(노트북 화면 등)에 단 카메라는 건반이 거꾸로 보인다.
        # 여기서 돌려 두면 보정·검출·화면이 모두 같은 방향을 쓴다.
        self.rotate_180 = False

    def read(self):
        ok, frame = self._cap.read()
        if ok and self.rotate_180:
            frame = cv2.rotate(frame, cv2.ROTATE_180)
        return ok, frame

    def release(self):
        self._cap.release()

    def switch(self):
        target = 0 if self.index != 0 else 1
        try:
            replacement = open_camera(target)
        except (RuntimeError, cv2.error) as error:
            print(f"카메라 전환 실패: {error} 현재 {self.index}번을 유지합니다.")
            return False

        self._cap.release()
        self._cap = replacement._cap
        self.index = replacement.index
        # rotate_180 은 그대로 둔다 — 방향은 카메라가 아니라 설치 위치의 문제다.
        print(f"카메라 전환 완료: {self.index}번. 새 카메라를 보정하세요.")
        return True


def open_camera(index=None):
    """Try camera 1 before 0; an explicit index overrides this convention.

    Device indices do not identify hardware, but external webcams commonly
    use index 1 on laptops. Use --camera if the device order differs.
    """
    candidates = (1, 0) if index is None else (index,)

    for candidate in candidates:
        # macOS USB UVC cameras sometimes need a short warmup and may not
        # support the requested 1280x720 mode. Try native defaults first.
        backend = cv2.CAP_AVFOUNDATION if sys.platform == "darwin" else cv2.CAP_ANY
        for resolution in (None, (1280, 720), (640, 480)):
            cap = cv2.VideoCapture(candidate, backend)
            selected = False
            try:
                if not cap.isOpened():
                    break  # Device unavailable, not a resolution issue.

                if resolution is not None:
                    cap.set(cv2.CAP_PROP_FRAME_WIDTH, resolution[0])
                    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, resolution[1])

                deadline = time.monotonic() + 3.0
                while time.monotonic() < deadline:
                    ok, frame = cap.read()
                    if ok and frame is not None and frame.size > 0:
                        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
                        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
                        print(f"카메라 선택: {candidate}번 ({width}x{height})")
                        selected = True
                        return Camera(cap, candidate)
                    time.sleep(0.1)

                mode = "기본" if resolution is None else f"{resolution[0]}x{resolution[1]}"
                print(f"카메라 {candidate}번: {mode} 해상도 영상 수신 실패, 재시도")
            except cv2.error as exc:
                print(f"카메라 {candidate}번: OpenCV 오류: {exc}")
            finally:
                if not selected:
                    cap.release()

    tried = ", ".join(str(candidate) for candidate in candidates)
    raise RuntimeError(f"카메라 연결 실패 (시도한 번호: {tried}). "
                       "연결 상태를 확인하거나 --camera 번호를 지정하세요.")
