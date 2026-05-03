import cv2

_WINDOW = "Calibration — TL → TR → BR → BL 순서로 클릭"


class CornerPicker:
    """웹캠 창에서 마우스 클릭으로 4점을 수집한다."""

    def pick(self, cap) -> list:
        """
        4점을 클릭 받아 반환한다.
        ENTER로 확정하면 [(x,y), ...] 4개 반환, ESC/창 닫기 시 None 반환.
        """
        corners = []

        def on_click(event, x, y, flags, param):
            if event == cv2.EVENT_LBUTTONDOWN and len(corners) < 4:
                corners.append((x, y))
                print(f"  점 {len(corners)}/4: ({x}, {y})")

        cv2.namedWindow(_WINDOW)
        cv2.setMouseCallback(_WINDOW, on_click)
        print("\n[캘리브레이션]  TL → TR → BR → BL 순서로 4점 클릭")
        print("  R: 초기화  |  ENTER: 확정  |  ESC: 취소\n")

        failed = 0
        while True:
            ret, frame = cap.read()
            if not ret:
                failed += 1
                if failed >= 30:
                    break
                if cv2.waitKey(30) & 0xFF == 27 or _is_closed(_WINDOW):
                    break
                continue
            failed = 0

            cv2.imshow(_WINDOW, _draw_guide(frame, corners))
            key = cv2.waitKey(1) & 0xFF

            if _is_closed(_WINDOW) or key == 27:
                break
            if key in (ord('r'), ord('R')):
                corners.clear()
                print("  초기화됨")
            elif key == 13 and len(corners) == 4:  # ENTER
                _close(_WINDOW)
                return list(corners)

        _close(_WINDOW)
        return None


# ── private helpers ────────────────────────────────────────────────────────────

def _draw_guide(frame, corners: list):
    out = frame.copy()

    for i, pt in enumerate(corners):
        cv2.circle(out, pt, 8, (0, 255, 0), -1)
        cv2.putText(out, str(i + 1), (pt[0] + 12, pt[1] - 8),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2)

    for i in range(len(corners) - 1):
        cv2.line(out, corners[i], corners[i + 1], (0, 255, 0), 2)
    if len(corners) == 4:
        cv2.line(out, corners[3], corners[0], (0, 255, 0), 2)

    cv2.putText(out, f"Points: {len(corners)}/4", (20, 40),
                cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 255), 2)
    if len(corners) == 4:
        cv2.putText(out, "ENTER: confirm  R: redo", (20, 80),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)
    return out


def _is_closed(name: str) -> bool:
    try:
        return cv2.getWindowProperty(name, cv2.WND_PROP_VISIBLE) < 1
    except cv2.error:
        return True


def _close(name: str):
    try:
        cv2.destroyWindow(name)
    except cv2.error:
        pass
