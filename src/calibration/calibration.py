import json
import os

import cv2
import numpy as np

from .compute import compute_key_regions, is_black, note_name, BLACK_PITCH_CLASSES
from .ui import CornerPicker


class KeyboardCalibrator:
    def __init__(self, num_white_keys: int = 14, start_note: int = 60):
        self.num_white_keys = num_white_keys
        self.start_note = start_note
        self.corners: list = []
        self.key_regions: dict = {}  # {midi_note: [(x,y)*4]}

    def calibrate(self, cap) -> bool:
        corners = CornerPicker().pick(cap)
        if corners is None:
            return False
        self.corners = corners
        self.key_regions = compute_key_regions(corners, self.num_white_keys, self.start_note)
        return True

    def draw_keys(self, frame, highlight_notes=None, alpha: float = 0.4):
        highlighted = set(highlight_notes or [])
        fill_w = 1.0 - max(0.0, min(1.0, alpha))

        overlay = frame.copy()
        for note, pts in self.key_regions.items():
            if note in highlighted:
                cv2.fillPoly(overlay, [np.array(pts, dtype=np.int32)], (0, 200, 0))
        result = cv2.addWeighted(overlay, fill_w, frame, 1.0 - fill_w, 0)

        for note, pts in self.key_regions.items():
            if note in highlighted:
                color = (0, 200, 0)
            elif is_black(note):
                color = (60, 60, 60)
            else:
                color = (180, 180, 180)
            cv2.polylines(result, [np.array(pts, dtype=np.int32)], True, color, 1)

        return result

    def save(self, path: str = "data/calibration.json"):
        dir_name = os.path.dirname(path)
        if dir_name:
            os.makedirs(dir_name, exist_ok=True)
        data = {
            "corners": self.corners,
            "num_white_keys": self.num_white_keys,
            "start_note": self.start_note,
            "key_regions": {str(k): v for k, v in self.key_regions.items()},
        }
        with open(path, "w") as f:
            json.dump(data, f, indent=2)
        print(f"저장됨: {path}")

    def load(self, path: str = "data/calibration.json") -> bool:
        if not os.path.exists(path):
            return False
        with open(path) as f:
            data = json.load(f)
        self.corners = [tuple(p) for p in data["corners"]]
        self.num_white_keys = data["num_white_keys"]
        self.start_note = data["start_note"]
        self.key_regions = {
            int(k): [tuple(p) for p in v]
            for k, v in data["key_regions"].items()
        }
        print(f"로드됨: {path}")
        return True


# ── 직접 실행 시 캘리브레이션 + 미리보기 ────────────────────────────────────────

def main() -> int:
    cap = cv2.VideoCapture(0, cv2.CAP_DSHOW)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)

    if not cap.isOpened():
        print("웹캠을 열 수 없습니다")
        return 1

    calib = KeyboardCalibrator(num_white_keys=14, start_note=60)

    if calib.calibrate(cap):
        calib.save()

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
                note = white_notes[key - ord('1')] if key - ord('1') < len(white_notes) else None
                if note:
                    highlight ^= {note}

    cap.release()
    cv2.destroyAllWindows()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
