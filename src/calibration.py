# src/calibration.py
import cv2
import numpy as np
import json
import os


class KeyboardCalibrator:
    """
    웹캠 영상에서 건반 영역을 캘리브레이션하는 도구.
    
    사용자가 건반 4꼭짓점(좌상→우상→우하→좌하)을 클릭하면,
    각 건반의 화면 좌표를 자동 계산하여 저장.
    """
    
    # 건반 구조 (한 옥타브 = 흰건 7개)
    # 흑건이 있는 흰건 위치 (옥타브 내 인덱스): C(0), D(1), F(3), G(4), A(5)
    WHITE_PITCH_CLASSES = {0, 2, 4, 5, 7, 9, 11}
    BLACK_PITCH_CLASSES = {1, 3, 6, 8, 10}
    
    def __init__(self, num_white_keys=14, start_note=60):
        """
        Args:
            num_white_keys: 캘리브레이션할 흰건 개수 (14 = 2옥타브)
            start_note: 가장 왼쪽 흰건의 MIDI 노트 번호 (기본 60 = C4)
        """
        self.num_white_keys = num_white_keys
        self.start_note = start_note
        self.corners = []  # 사용자가 클릭한 4점
        self.key_regions = {}  # {midi_note: [4점 좌표]}
        self.preview_frame = None
    
    def _on_mouse(self, event, x, y, flags, param):
        """마우스 콜백 — 좌클릭 시 4점 수집"""
        if event == cv2.EVENT_LBUTTONDOWN and len(self.corners) < 4:
            self.corners.append((x, y))
            print(f"  점 {len(self.corners)}/4: ({x}, {y})")

    @staticmethod
    def _is_window_closed(window_name):
        try:
            return cv2.getWindowProperty(window_name, cv2.WND_PROP_VISIBLE) < 1
        except cv2.error:
            return True

    @staticmethod
    def _destroy_window(window_name):
        try:
            cv2.destroyWindow(window_name)
        except cv2.error:
            pass
    
    def calibrate(self, cap):
        """
        웹캠에서 4점을 클릭 받아 건반 좌표를 계산.
        
        Args:
            cap: cv2.VideoCapture 객체
        
        Returns:
            성공 시 True, 사용자 취소 시 False
        """
        if hasattr(cap, "isOpened") and not cap.isOpened():
            print("웹캠을 열 수 없습니다")
            return False

        self.corners = []
        window_name = "Calibration - Click 4 corners (TL -> TR -> BR -> BL)"
        cv2.namedWindow(window_name)
        cv2.setMouseCallback(window_name, self._on_mouse)
        
        print("\n[캘리브레이션 시작]")
        print("순서대로 4점을 클릭하세요:")
        print("  1. 건반 영역 좌상단")
        print("  2. 건반 영역 우상단")
        print("  3. 건반 영역 우하단")
        print("  4. 건반 영역 좌하단")
        print("  R: 다시 시작 | ENTER: 확정 | ESC: 취소\n")
        
        failed_reads = 0
        max_failed_reads = 30

        while True:
            ret, frame = cap.read()
            if not ret:
                failed_reads += 1
                if failed_reads >= max_failed_reads:
                    print("웹캠 프레임을 읽을 수 없습니다")
                    self._destroy_window(window_name)
                    return False
                key = cv2.waitKey(30) & 0xFF
                if key == 27 or self._is_window_closed(window_name):
                    self._destroy_window(window_name)
                    return False
                continue

            failed_reads = 0
            
            display = frame.copy()
            
            # 클릭한 점들 표시
            for i, pt in enumerate(self.corners):
                cv2.circle(display, pt, 8, (0, 255, 0), -1)
                cv2.putText(display, str(i + 1), 
                           (pt[0] + 12, pt[1] - 8),
                           cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2)
            
            # 점들을 선으로 연결
            if len(self.corners) >= 2:
                for i in range(len(self.corners) - 1):
                    cv2.line(display, self.corners[i], self.corners[i + 1],
                            (0, 255, 0), 2)
                if len(self.corners) == 4:
                    cv2.line(display, self.corners[3], self.corners[0],
                            (0, 255, 0), 2)
            
            # 안내 텍스트
            status = f"Points: {len(self.corners)}/4"
            cv2.putText(display, status, (20, 40),
                       cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 255), 2)
            
            if len(self.corners) == 4:
                cv2.putText(display, "ENTER to confirm, R to redo",
                           (20, 80), cv2.FONT_HERSHEY_SIMPLEX,
                           0.7, (0, 255, 255), 2)
            
            cv2.imshow(window_name, display)
            
            key = cv2.waitKey(1) & 0xFF
            if self._is_window_closed(window_name):
                self._destroy_window(window_name)
                return False
            if key == 27:  # ESC
                self._destroy_window(window_name)
                return False
            elif key == ord('r') or key == ord('R'):
                self.corners = []
                print("  점 초기화됨")
            elif key == 13 and len(self.corners) == 4:  # ENTER
                self.preview_frame = frame.copy()
                self._compute_key_regions()
                self._destroy_window(window_name)
                return True
    
    def _compute_key_regions(self):
        """4 꼭짓점으로부터 각 건반의 좌표를 계산"""
        if len(self.corners) != 4:
            raise ValueError("건반 영역을 계산하려면 꼭짓점 4개가 필요합니다")
        if self.num_white_keys <= 0:
            raise ValueError("num_white_keys는 1 이상이어야 합니다")
        if self.start_note % 12 in self.BLACK_PITCH_CLASSES:
            raise ValueError("start_note는 가장 왼쪽 흰건의 MIDI 노트여야 합니다")

        self.key_regions.clear()
        tl, tr, br, bl = [np.array(p, dtype=np.float32) for p in self.corners]
        
        # 흰건 영역 분할
        white_top_step = (tr - tl) / self.num_white_keys
        white_bot_step = (br - bl) / self.num_white_keys
        
        white_notes = []
        note = self.start_note
        while len(white_notes) < self.num_white_keys:
            if note % 12 in self.WHITE_PITCH_CLASSES:
                white_notes.append(note)
            note += 1
        
        # 흰건 좌표 계산
        for i in range(self.num_white_keys):
            note = white_notes[i]
            
            p1 = tl + white_top_step * i
            p2 = tl + white_top_step * (i + 1)
            p3 = bl + white_bot_step * (i + 1)
            p4 = bl + white_bot_step * i
            
            self.key_regions[note] = [
                tuple(p1.astype(int)),
                tuple(p2.astype(int)),
                tuple(p3.astype(int)),
                tuple(p4.astype(int))
            ]
        
        # 흑건 좌표 계산 (흰건 사이에 위치, 길이는 60%)
        black_height_ratio = 0.6
        black_width_ratio = 0.6
        
        for i in range(self.num_white_keys - 1):
            current_white_note = white_notes[i]
            next_white_note = white_notes[i + 1]
            if next_white_note - current_white_note != 2:
                continue
            
            black_note = current_white_note + 1
            
            # 흰건 i와 i+1 사이의 경계선
            top_boundary = tl + white_top_step * (i + 1)
            bot_boundary = bl + white_bot_step * (i + 1)
            
            # 흑건의 너비 = 흰건 너비의 60%, 경계선을 중심으로 좌우 분배
            half_w_top = white_top_step * black_width_ratio / 2
            half_w_bot = white_bot_step * black_width_ratio / 2
            
            top_left = top_boundary - half_w_top
            top_right = top_boundary + half_w_top
            
            # 흑건은 위에서 60%만 차지 (아래쪽은 흰건 영역)
            bot_left = top_left + (bot_boundary - half_w_bot - top_left) * black_height_ratio
            bot_right = top_right + (bot_boundary + half_w_bot - top_right) * black_height_ratio
            
            self.key_regions[black_note] = [
                tuple(top_left.astype(int)),
                tuple(top_right.astype(int)),
                tuple(bot_right.astype(int)),
                tuple(bot_left.astype(int))
            ]
    
    def draw_keys(self, frame, highlight_notes=None, alpha=0.4):
        """
        프레임 위에 건반 영역을 그려서 캘리브레이션 결과 확인.
        
        Args:
            frame: 그릴 프레임
            highlight_notes: 강조할 MIDI 노트 set (None이면 모두 외곽선만)
            alpha: 채우기 투명도 (0이면 불투명, 1이면 투명)
        """
        if highlight_notes is None:
            highlight_notes = set()
        else:
            highlight_notes = set(highlight_notes)
        
        overlay = frame.copy()
        result = frame.copy()
        fill_opacity = max(0.0, min(1.0, 1 - alpha))
        frame_opacity = 1.0 - fill_opacity
        
        for note, pts in self.key_regions.items():
            pts_np = np.array(pts, dtype=np.int32)
            if note in highlight_notes:
                cv2.fillPoly(overlay, [pts_np], (0, 200, 0))
        
        result = cv2.addWeighted(overlay, fill_opacity, result, frame_opacity, 0)

        for note, pts in self.key_regions.items():
            pts_np = np.array(pts, dtype=np.int32)
            is_black = (note % 12) in self.BLACK_PITCH_CLASSES
            color = (0, 200, 0) if note in highlight_notes else (
                (180, 180, 180) if not is_black else (60, 60, 60)
            )
            cv2.polylines(result, [pts_np], True, color, 1)

        return result
    
    def save(self, path="data/calibration.json"):
        """캘리브레이션 결과를 파일로 저장"""
        dir_name = os.path.dirname(path)
        if dir_name:
            os.makedirs(dir_name, exist_ok=True)
        data = {
            'corners': self.corners,
            'num_white_keys': self.num_white_keys,
            'start_note': self.start_note,
            'key_regions': {str(k): v for k, v in self.key_regions.items()}
        }
        with open(path, 'w') as f:
            json.dump(data, f, indent=2)
        print(f"  저장됨: {path}")
    
    def load(self, path="data/calibration.json"):
        """저장된 캘리브레이션 불러오기"""
        if not os.path.exists(path):
            return False
        with open(path) as f:
            data = json.load(f)
        self.corners = [tuple(p) for p in data['corners']]
        self.num_white_keys = data['num_white_keys']
        self.start_note = data['start_note']
        self.key_regions = {
            int(k): [tuple(p) for p in v] 
            for k, v in data['key_regions'].items()
        }
        print(f"  로드됨: {path}")
        return True


# 직접 실행하면 캘리브레이션 + 미리보기
if __name__ == "__main__":
    cap = cv2.VideoCapture(0, cv2.CAP_DSHOW)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
    
    if not cap.isOpened():
        print("웹캠을 열 수 없습니다")
        exit()
    
    # 14건반(2옥타브) C4부터 시작
    calib = KeyboardCalibrator(num_white_keys=14, start_note=60)
    
    if calib.calibrate(cap):
        calib.save()
        print("\n[캘리브레이션 미리보기]")
        print("  숫자키 1~7: 흰건 강조 (C D E F G A B)")
        print("  Q: 종료\n")
        
        highlight = set()
        white_notes = sorted([n for n in calib.key_regions.keys() 
                              if n % 12 not in calib.BLACK_PITCH_CLASSES])
        
        while True:
            ret, frame = cap.read()
            if not ret:
                print("웹캠 프레임을 읽을 수 없습니다")
                break
            
            display = calib.draw_keys(frame, highlight)
            cv2.putText(display, "Press 1-7 to highlight white keys, Q to quit",
                       (20, 40), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)
            cv2.imshow("Calibration Preview", display)
            
            key = cv2.waitKey(1) & 0xFF
            if key == ord('q') or calib._is_window_closed("Calibration Preview"):
                break
            elif ord('1') <= key <= ord('7'):
                idx = key - ord('1')
                if idx < len(white_notes):
                    note = white_notes[idx]
                    if note in highlight:
                        highlight.remove(note)
                    else:
                        highlight.add(note)
    
    cap.release()
    cv2.destroyAllWindows()
