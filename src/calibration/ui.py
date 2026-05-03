# ──────────────────────────────────────────────────────────────────────────────
# ui.py — 캘리브레이션 UI (웹캠 창 + 마우스 입력)
#
# 사용자가 화면에서 건반 영역의 꼭짓점 4개를 클릭하면
# 그 좌표를 수집해서 반환한다.
# OpenCV 창 관련 코드만 담당하며, 수학 계산은 compute.py에서 처리한다.
# ──────────────────────────────────────────────────────────────────────────────

import cv2

_WINDOW = "Calibration — TL → TR → BR → BL 순서로 클릭"


class CornerPicker:
    """웹캠 창에서 마우스 클릭으로 4점을 수집한다."""

    def pick(self, cap) -> list:
        """
        웹캠 영상을 보여주면서 사용자가 클릭한 4점을 수집한다.

        조작법:
          - 마우스 좌클릭: 점 추가 (최대 4개)
          - R: 초기화 (다시 찍기)
          - ENTER: 4점이 모두 찍혔을 때 확정
          - ESC / 창 닫기: 취소

        반환값:
          - 성공: [(x,y), (x,y), (x,y), (x,y)] — TL·TR·BR·BL 순서
          - 취소: None
        """
        corners = []

        # 마우스 콜백: 좌클릭할 때마다 좌표를 corners에 추가
        def on_click(event, x, y, flags, param):
            if event == cv2.EVENT_LBUTTONDOWN and len(corners) < 4:
                corners.append((x, y))
                print(f"  점 {len(corners)}/4: ({x}, {y})")

        cv2.namedWindow(_WINDOW)
        cv2.setMouseCallback(_WINDOW, on_click)
        print("\n[캘리브레이션]  TL → TR → BR → BL 순서로 4점 클릭")
        print("  R: 초기화  |  ENTER: 확정  |  ESC: 취소\n")

        # 웹캠 읽기 실패 횟수를 추적 — 30번 연속 실패하면 웹캠 오류로 판단하고 종료
        failed = 0
        while True:
            ret, frame = cap.read()
            if not ret:
                failed += 1
                if failed >= 30:
                    break
                # 프레임을 못 읽어도 키 입력은 처리 (ESC로 취소 가능)
                if cv2.waitKey(30) & 0xFF == 27 or _is_closed(_WINDOW):
                    break
                continue
            failed = 0

            # 현재까지 찍힌 점과 안내선을 프레임 위에 그려서 표시
            cv2.imshow(_WINDOW, _draw_guide(frame, corners))
            key = cv2.waitKey(1) & 0xFF

            if _is_closed(_WINDOW) or key == 27:  # 창 닫기 또는 ESC
                break
            if key in (ord('r'), ord('R')):        # R: 처음부터 다시
                corners.clear()
                print("  초기화됨")
            elif key == 13 and len(corners) == 4:  # ENTER: 4점 확정
                _close(_WINDOW)
                return list(corners)

        _close(_WINDOW)
        return None


# ── 내부 헬퍼 함수 ───────────────────────────────���─────────────────────────────

def _draw_guide(frame, corners: list):
    """
    웹캠 프레임 위에 클릭 현황을 그려 사용자에게 안내한다.
    - 클릭한 점: 초록 원 + 순서 번호
    - 점들을 잇는 선 (4점이면 사각형 완성)
    - 상태 텍스트 (몇 점 찍었는지, 다음 행동 안내)
    """
    out = frame.copy()

    # 클릭한 점마다 원과 번호 표시
    for i, pt in enumerate(corners):
        cv2.circle(out, pt, 8, (0, 255, 0), -1)
        cv2.putText(out, str(i + 1), (pt[0] + 12, pt[1] - 8),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2)

    # 점들을 순서대로 선으로 연결 (4점이면 마지막→첫번째 닫힘)
    for i in range(len(corners) - 1):
        cv2.line(out, corners[i], corners[i + 1], (0, 255, 0), 2)
    if len(corners) == 4:
        cv2.line(out, corners[3], corners[0], (0, 255, 0), 2)

    # 상단에 현재 상태 텍스트
    cv2.putText(out, f"Points: {len(corners)}/4", (20, 40),
                cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 255), 2)
    if len(corners) == 4:
        cv2.putText(out, "ENTER: confirm  R: redo", (20, 80),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)
    return out


def _is_closed(name: str) -> bool:
    """사용자가 창의 X 버튼을 눌러 닫았는지 확인한다."""
    try:
        return cv2.getWindowProperty(name, cv2.WND_PROP_VISIBLE) < 1
    except cv2.error:
        return True


def _close(name: str):
    """창을 닫는다. 이미 닫혀 있어도 오류 없이 통과한다."""
    try:
        cv2.destroyWindow(name)
    except cv2.error:
        pass
