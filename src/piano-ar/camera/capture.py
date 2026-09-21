import cv2


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
        cap = cv2.VideoCapture(candidate)
        selected = False
        try:
            if not cap.isOpened():
                continue

            cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)

            # Verify video delivery before choosing the device.
            for _ in range(5):
                ok, frame = cap.read()
                if ok and frame is not None and frame.size > 0:
                    print(f"카메라 선택: {candidate}번")
                    selected = True
                    return Camera(cap, candidate)
        except cv2.error:
            continue
        finally:
            if not selected:
                cap.release()

    tried = ", ".join(str(candidate) for candidate in candidates)
    raise RuntimeError(f"카메라 연결 실패 (시도한 번호: {tried}). "
                       "연결 상태를 확인하거나 --camera 번호를 지정하세요.")
