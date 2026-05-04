# ──────────────────────────────────────────────────────────────────────────────
# calibration.py — 캘리브레이션 메인 클래스
#
# 이 파일은 ui.py와 compute.py를 조립하는 얇은 조율자(orchestrator) 역할이다.
#
# 전체 흐름:
#   1. calibrate(cap)  → UI로 4점 수집 → compute로 좌표 계산 → key_regions 저장
#   2. draw_keys(frame) → key_regions를 바탕으로 AR 오버레이 렌더링
#   3. save() / load() → 캘리브레이션 결과를 JSON으로 영속화
#
# 외부에서 사용할 때는 KeyboardCalibrator만 import하면 된다.
#   from src.calibration import KeyboardCalibrator
# ──────────────────────────────────────────────────────────────────────────────

import json
import os

import cv2
import numpy as np

from .compute import compute_key_regions, is_black, note_name, BLACK_PITCH_CLASSES
from .ui import CornerPicker


class KeyboardCalibrator:
    """
    웹캠으로 실제 피아노 건반을 캘리브레이션하고 결과를 관리한다.

    Attributes:
        num_white_keys: 캘리브레이션할 흰건 수 (기본 14 = 2옥타브)
        start_note:     가장 왼쪽 흰건의 MIDI 번호 (기본 60 = C4)
        corners:        사용자가 클릭한 4점 좌표 [(x,y) * 4]
        key_regions:    건반별 화면 좌표 {midi_note: [(x,y) * 4]}
    """

    def __init__(self, num_white_keys: int = 14, start_note: int = 60):
        self.num_white_keys = num_white_keys
        self.start_note = start_note
        self.corners: list = []
        self.key_regions: dict = {}  # {midi_note: [(x,y)*4]}

    # ── 캘리브레이션 ────────────────────────────────────────────────────────────

    def calibrate(self, cap) -> bool:
        """
        웹캠 창을 열어 4점을 클릭 받고 건반 좌표를 계산한다.

        Args:
            cap: cv2.VideoCapture 객체

        Returns:
            True: 성공 (key_regions 갱신됨)
            False: 사용자가 취소하거나 웹캠 오류
        """
        corners = CornerPicker().pick(cap)
        if corners is None:
            return False
        self.corners = corners
        self.key_regions = compute_key_regions(corners, self.num_white_keys, self.start_note)
        return True

    # ── AR 오버레이 렌더링 ──────────────────────────────────────────────────────

    def draw_keys(self, frame, highlight_notes=None, alpha: float = 0.4):
        """
        웹캠 프레임 위에 건반 영역을 그린다. AR 오버레이 역할.

        Args:
            frame:           웹캠에서 읽은 원본 프레임
            highlight_notes: 초록색으로 강조할 MIDI 노트 집합 (연주해야 할 건반 표시에 사용)
            alpha:           배경 투명도 (0.0 = 완전 불투명, 1.0 = 완전 투명)

        Returns:
            오버레이가 적용된 새 프레임 (원본은 변경되지 않음)
        """
        highlighted = set(highlight_notes or [])

        # alpha → fill 불투명도로 변환 (alpha=0.4면 fill이 60% 불투명)
        fill_w = 1.0 - max(0.0, min(1.0, alpha))

        # 1단계: 강조할 건반만 초록색으로 채운 overlay를 원본과 블렌딩
        overlay = frame.copy()
        for note, pts in self.key_regions.items():
            if note in highlighted:
                cv2.fillPoly(overlay, [np.array(pts, dtype=np.int32)], (0, 200, 0))
        result = cv2.addWeighted(overlay, fill_w, frame, 1.0 - fill_w, 0)

        # 2단계: 모든 건반의 외곽선을 그림
        # 강조 건반 → 초록, 흑건 → 어두운 회색, 흰건 → 밝은 회색
        for note, pts in self.key_regions.items():
            if note in highlighted:
                color = (0, 200, 0)
            elif is_black(note):
                color = (60, 60, 60)
            else:
                color = (180, 180, 180)
            cv2.polylines(result, [np.array(pts, dtype=np.int32)], True, color, 1)

        return result

    # ── 저장 / 불러오기 ─────────────────────────────────────────────────────────

    def save(self, path: str = "data/calibration.json"):
        """
        캘리브레이션 결과를 JSON 파일로 저장한다.
        저장 내용: 4점 좌표, 흰건 수, 시작 노트, 건반별 좌표
        """
        dir_name = os.path.dirname(path)
        if dir_name:
            os.makedirs(dir_name, exist_ok=True)
        data = {
            "corners": self.corners,
            "num_white_keys": self.num_white_keys,
            "start_note": self.start_note,
            # JSON key는 문자열만 허용되므로 MIDI 번호를 str로 변환
            "key_regions": {str(k): v for k, v in self.key_regions.items()},
        }
        with open(path, "w") as f:
            json.dump(data, f, indent=2)
        print(f"저장됨: {path}")

    def load(self, path: str = "data/calibration.json") -> bool:
        """
        저장된 캘리브레이션 파일을 불러온다.

        Returns:
            True: 성공  /  False: 파일 없음
        """
        if not os.path.exists(path):
            return False
        with open(path) as f:
            data = json.load(f)
        self.corners = [tuple(p) for p in data["corners"]]
        self.num_white_keys = data["num_white_keys"]
        self.start_note = data["start_note"]
        # JSON에서 읽으면 key가 str이므로 다시 int로 변환
        self.key_regions = {
            int(k): [tuple(p) for p in v]
            for k, v in data["key_regions"].items()
        }
        print(f"로드됨: {path}")
        return True


# ── 직접 실행 시: 캘리브레이션 + 결과 미리보기 ─────────────────────────────────

def main(camera_index: int = 1) -> int:
    # 웹캠 열기 (CAP_DSHOW: Windows에서 딜레이 줄이는 백엔드)
    cap = cv2.VideoCapture(camera_index, cv2.CAP_DSHOW)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)

    if not cap.isOpened():
        print("웹캠을 열 수 없습니다")
        return 1

    # 2옥타브(C4~B5), 흰건 14개
    calib = KeyboardCalibrator(num_white_keys=14, start_note=60)

    if calib.calibrate(cap):
        calib.save()

        # 미리보기: 숫자키로 흰건 강조 토글
        white_notes = sorted(n for n in calib.key_regions if not is_black(n))
        highlight = set()

        print("\n[미리보기]  숫자키 1~7: 흰건 토글  |  Q: 종료")
        print("  " + "  ".join(f"{i+1}={note_name(n)}" for i, n in enumerate(white_notes[:7])))

        while True:
            ret, frame = cap.read()
            if not ret:
                break

            cv2.imshow("Preview", calib.draw_keys(frame, highlight))
            key = cv2.waitKey(1) & 0xFF
            if key == ord('q'):
                break
            if ord('1') <= key <= ord('7'):
                # 해당 흰건을 highlight에 추가/제거 (토글)
                note = white_notes[key - ord('1')] if key - ord('1') < len(white_notes) else None
                if note:
                    highlight ^= {note}

    cap.release()
    cv2.destroyAllWindows()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
