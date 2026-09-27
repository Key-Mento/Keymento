import cv2
import numpy as np
import json
import os
import time


# 현재 파이썬 파일이 있는 폴더
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# 수동 캘리브레이션과 ArUco 캘리브레이션이 함께 사용하는 파일
CALIB_FILE = os.path.join(
    BASE_DIR,
    "calibration_points.json"
)

# ArUco 자동 갱신 설정
MOVEMENT_THRESHOLD = 8.0
STABLE_FRAME_COUNT = 5
STABILITY_THRESHOLD = 3.0
SMOOTHING_ALPHA = 0.3
MAX_MISSING_FRAMES = 30

# 한 프레임에 네 개가 동시에 잡혀야만 하면, 마커마다 60% 확률로 잡혀도
# 넷이 함께 잡히는 건 13%뿐이다. 그래서 마커별 마지막 위치를 잠깐 기억한다.
MARKER_MEMORY_SECONDS = 0.5
ARUCO_IDS = (0, 1, 2, 3)

# 마커 중심은 건반 모서리가 아니다 — 마커는 건반 위·아래 패널에 붙으므로
# 그대로 쓰면 격자가 옆으로 늘어나고 기울어진다. 수동 보정(M) 때 찍은
# 건반 모서리를 "마커 사각형 = 단위 정사각형" 좌표로 기억해 두고, 그 뒤로는
# 마커가 움직인 만큼 건반 모서리를 따라 옮긴다.
KEY_AREA_FILE = os.path.join(
    BASE_DIR,
    "key_area.json"
)
UNIT_SQUARE = np.float32([[0, 0], [1, 0], [1, 1], [0, 1]])

# OpenCV orders each marker's corners as top-left, top-right, bottom-right,
# bottom-left. Keep this as None only when each marker center physically marks
# the corresponding keyboard corner. Otherwise set the corner index used for
# marker IDs 0, 1, 2, and 3 after checking the actual marker orientation.
ARUCO_KEYBOARD_CORNER_INDICES = None

# 수동 클릭 좌표
manual_points = []


def save_points(points):
    """현재 캘리브레이션 좌표를 JSON 파일에 저장한다."""
    serializable_points = [
        [int(round(x)), int(round(y))]
        for x, y in points
    ]

    try:
        with open(
            CALIB_FILE,
            "w",
            encoding="utf-8"
        ) as file:
            json.dump(
                serializable_points,
                file,
                indent=4
            )

        print("캘리브레이션 좌표 저장 완료")
        print(f"저장 위치: {CALIB_FILE}")
        print(f"저장 좌표: {serializable_points}")

    except OSError as error:
        print("캘리브레이션 좌표 저장 실패")
        print(error)


def load_points():
    """저장된 캘리브레이션 좌표를 불러온다."""
    if not os.path.exists(CALIB_FILE):
        return None

    try:
        with open(
            CALIB_FILE,
            "r",
            encoding="utf-8"
        ) as file:
            points = json.load(file)

        points = np.array(
            points,
            dtype=np.float32
        )

        if points.shape != (4, 2):
            print("저장된 좌표 형식이 올바르지 않습니다.")
            return None

        if not validate_points(points):
            print("저장된 캘리브레이션 좌표가 유효하지 않습니다.")
            return None

        print("캘리브레이션 좌표 불러오기 완료")
        print(f"불러온 위치: {CALIB_FILE}")

        return points

    except (
        json.JSONDecodeError,
        OSError,
        ValueError
    ) as error:
        print("저장된 캘리브레이션 파일을 불러올 수 없습니다.")
        print(error)

        return None


def _transform(points, matrix):
    return cv2.perspectiveTransform(
        np.asarray(points, dtype=np.float32).reshape(-1, 1, 2),
        matrix
    ).reshape(-1, 2)


class KeyArea:
    """마커 네 점(ID 0~3 → 단위 정사각형 모서리) 기준으로 본 건반 네 모서리.

    기본값은 단위 정사각형 그대로 — 마커 중심이 곧 건반 모서리라는 예전
    가정과 같다. 마커와 건반의 물리적 관계만 담으므로 카메라를 바꾸거나
    화면을 돌려도(F) 다시 배울 필요가 없다.
    """

    def __init__(self, points=UNIT_SQUARE):
        self.points = np.asarray(points, dtype=np.float32)

    @classmethod
    def load(cls):
        if not os.path.exists(KEY_AREA_FILE):
            return cls()

        try:
            with open(KEY_AREA_FILE, "r", encoding="utf-8") as file:
                points = np.array(json.load(file), dtype=np.float32)
        except (json.JSONDecodeError, OSError, ValueError) as error:
            print("건반 영역 파일을 불러올 수 없습니다.")
            print(error)
            return cls()

        if points.shape != (4, 2):
            print("건반 영역 파일 형식이 올바르지 않습니다.")
            return cls()

        return cls(points)

    @classmethod
    def learn(cls, marker_points, key_points):
        """지금 보이는 마커 기준으로 건반 모서리가 어디 있는지 배운다."""
        matrix = cv2.getPerspectiveTransform(
            np.asarray(marker_points, dtype=np.float32),
            UNIT_SQUARE
        )
        return cls(_transform(key_points, matrix))

    def save(self):
        try:
            with open(KEY_AREA_FILE, "w", encoding="utf-8") as file:
                json.dump(
                    np.round(self.points.astype(float), 5).tolist(),
                    file,
                    indent=4
                )
            print(f"건반 영역 저장 완료: {KEY_AREA_FILE}")
        except OSError as error:
            print("건반 영역 저장 실패")
            print(error)

    def from_markers(self, marker_points):
        """마커 네 점 → 건반 네 모서리."""
        matrix = cv2.getPerspectiveTransform(
            UNIT_SQUARE,
            np.asarray(marker_points, dtype=np.float32)
        )
        return _transform(self.points, matrix)

    def to_markers(self, key_points):
        """건반 네 모서리 → 그때 마커가 있었을 네 점 (from_markers 의 역)."""
        matrix = cv2.getPerspectiveTransform(
            self.points,
            np.asarray(key_points, dtype=np.float32)
        )
        return _transform(UNIT_SQUARE, matrix)


def order_points(points):
    """
    순서 없이 선택한 네 점을 다음 순서로 정렬한다.

    0: 왼쪽 위
    1: 오른쪽 위
    2: 오른쪽 아래
    3: 왼쪽 아래
    """
    points = np.array(
        points,
        dtype=np.float32
    )

    # y 좌표를 기준으로 위쪽 두 점과 아래쪽 두 점을 구분
    y_sorted = points[
        np.argsort(points[:, 1])
    ]

    top_points = y_sorted[:2]
    bottom_points = y_sorted[2:]

    # 각 그룹에서 x 좌표를 기준으로 왼쪽과 오른쪽을 구분
    top_points = top_points[
        np.argsort(top_points[:, 0])
    ]

    bottom_points = bottom_points[
        np.argsort(bottom_points[:, 0])
    ]

    top_left = top_points[0]
    top_right = top_points[1]
    bottom_left = bottom_points[0]
    bottom_right = bottom_points[1]

    return np.array(
        [
            top_left,
            top_right,
            bottom_right,
            bottom_left
        ],
        dtype=np.float32
    )


def validate_points(points):
    """네 점이 정상적인 캘리브레이션 사각형인지 검사한다."""
    if points is None:
        return False

    points = np.array(
        points,
        dtype=np.float32
    )

    if points.shape != (4, 2):
        return False

    polygon = points.astype(np.int32)

    if not cv2.isContourConvex(polygon):
        return False

    area = cv2.contourArea(points)

    if area < 5000:
        return False

    top_length = np.linalg.norm(
        points[1] - points[0]
    )

    right_length = np.linalg.norm(
        points[2] - points[1]
    )

    bottom_length = np.linalg.norm(
        points[2] - points[3]
    )

    left_length = np.linalg.norm(
        points[3] - points[0]
    )

    if min(
        top_length,
        right_length,
        bottom_length,
        left_length
    ) < 30:
        return False

    horizontal_ratio = (
        top_length / bottom_length
    )

    vertical_ratio = (
        left_length / right_length
    )

    if not 0.5 <= horizontal_ratio <= 2.0:
        return False

    if not 0.5 <= vertical_ratio <= 2.0:
        return False

    return True


def manual_mouse_callback(
    event,
    x,
    y,
    flags,
    param
):
    """수동 캘리브레이션 창에서 클릭한 좌표를 저장한다."""
    global manual_points

    if event == cv2.EVENT_LBUTTONDOWN:
        if len(manual_points) < 4:
            manual_points.append((x, y))

            print(
                f"{len(manual_points)}번째 점 선택: "
                f"({x}, {y})"
            )


def draw_manual_points(frame, marker_count=None):
    """수동으로 선택한 좌표를 화면에 표시한다."""
    display = frame.copy()

    for index, point in enumerate(manual_points):
        cv2.circle(
            display,
            point,
            6,
            (0, 255, 0),
            -1
        )

        cv2.putText(
            display,
            str(index + 1),
            (point[0] + 8, point[1] - 8),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.6,
            (0, 255, 0),
            2
        )

    if len(manual_points) == 4:
        ordered_points = order_points(
            manual_points
        )

        cv2.polylines(
            display,
            [ordered_points.astype(np.int32)],
            True,
            (0, 255, 255),
            2
        )

    cv2.putText(
        display,
        "Click 4 corners in any order",
        (20, 35),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.65,
        (255, 255, 255),
        2
    )

    cv2.putText(
        display,
        "R: reset / ESC: cancel",
        (20, 65),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.6,
        (255, 255, 255),
        2
    )

    if marker_count is not None:
        cv2.putText(
            display,
            f"ArUco {marker_count}/4 (all 4 = ArUco follows these corners)",
            (20, 95),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.6,
            (0, 255, 0) if marker_count == 4 else (0, 165, 255),
            2
        )

    return display


def manual_calibrate(cap):
    """
    현재 카메라 화면에서 네 점을 직접 클릭하여
    수동 캘리브레이션을 수행한다.
    """
    global manual_points

    manual_points = []

    window_name = "Manual Calibration"

    cv2.namedWindow(window_name)
    cv2.setMouseCallback(
        window_name,
        manual_mouse_callback
    )

    print()
    print("수동 캘리브레이션을 시작합니다.")
    print("건반 영역의 네 모서리를 순서 없이 클릭하세요.")
    print("R: 다시 선택, ESC: 취소")

    result = None
    tracker = MarkerTracker()

    while True:
        ret, frame = cap.read()

        if not ret:
            print("카메라 프레임 읽기 실패")
            break

        markers = tracker.update(frame)
        display = draw_manual_points(frame, len(tracker.recent_ids))

        cv2.imshow(
            window_name,
            display
        )

        key = cv2.waitKey(1) & 0xFF

        if key == 27:
            print("수동 캘리브레이션을 취소했습니다.")
            break

        if key in (ord("r"), ord("R")):
            manual_points = []
            print("선택한 좌표를 초기화했습니다.")

        if len(manual_points) == 4:
            ordered_points = order_points(
                manual_points
            )

            if validate_points(ordered_points):
                result = ordered_points

                save_points(result)

                print("수동 캘리브레이션 완료")
                print("자동 정렬된 좌표:")
                print(result)

                if markers is not None and validate_points(markers):
                    KeyArea.learn(markers, result).save()
                    print("이제 ArUco 가 마커 대신 이 건반 모서리를 따라갑니다.")
                else:
                    print("ArUco 마커 4개가 다 보이지 않아 건반 영역은 "
                          "그대로 둡니다. 자동 보정(A)이 이 좌표를 "
                          "덮어쓸 수 있습니다.")

                break

            print("선택한 네 점이 올바르지 않습니다.")
            print("좌표를 다시 선택하세요.")

            manual_points = []

    cv2.destroyWindow(window_name)

    return result


def calibrate(cap, reuse_saved=True, on_switch=None):
    """Return calibration points for the AR loop.

    Saved points are reused immediately. If there are no saved points, this
    waits for either ArUco IDs 0, 1, 2, 3 or a manual calibration request.
    """
    calibration_points = load_points() if reuse_saved else None

    if calibration_points is not None:
        return calibration_points

    tracker = MarkerTracker()
    key_area = KeyArea.load()
    window_name = "Initial Calibration"
    cv2.namedWindow(window_name)

    print("Camera calibration required.")
    print("Show ArUco markers ID 0, 1, 2, 3 or press M for manual setup.")
    print("ESC: cancel")
    if on_switch is not None:
        print("C: switch camera 0/1")

    while True:
        ret, frame = cap.read()

        if not ret:
            print("Failed to read camera frame")
            break

        markers = tracker.update(frame)
        detected_points = (
            None if markers is None
            else key_area.from_markers(markers)
        )

        valid_aruco = (
            detected_points is not None
            and validate_points(detected_points)
        )

        if valid_aruco:
            calibration_points = detected_points.copy()
            save_points(calibration_points)
            print("Initial ArUco calibration completed")
            print(calibration_points)
            cv2.destroyWindow(window_name)
            return calibration_points

        display = draw_main_screen(
            frame,
            detected_points,
            None,
            f"Markers {len(tracker.recent_ids)}/4 / M: manual"
            + (" / C: camera" if on_switch else ""),
            (0, 0, 255),
            0,
            True
        )

        cv2.imshow(
            window_name,
            display
        )

        key = cv2.waitKey(1) & 0xFF

        if key == 27:
            break

        if key in (ord("c"), ord("C")) and on_switch is not None:
            on_switch()
            continue

        if key in (ord("m"), ord("M")):
            manual_result = manual_calibrate(cap)

            if manual_result is not None:
                cv2.destroyWindow(window_name)
                return manual_result

    cv2.destroyWindow(window_name)

    return None


def create_aruco_detector():
    """ArUco 마커 검출기를 생성한다."""
    aruco_dict = cv2.aruco.getPredefinedDictionary(
        cv2.aruco.DICT_4X4_50
    )

    parameters = cv2.aruco.DetectorParameters()

    # 건반 위 마커는 작고 비스듬히 찍혀 한 칸이 3~5px 밖에 안 된다.
    # 임계값 창 크기(3·10·17)를 작은 마커에 맞추고, 흐린 윤곽도 사각형으로
    # 받아 준다. 창을 4 간격으로 더 촘촘히 하면 조금 더 잡히지만 검출
    # 시간이 1.5배(720p 에서 23→34ms)라 30fps 를 못 지킨다.
    parameters.adaptiveThreshWinSizeStep = 7
    parameters.polygonalApproxAccuracyRate = 0.05
    parameters.minMarkerPerimeterRate = 0.02
    parameters.perspectiveRemovePixelPerCell = 8
    # 모서리를 서브픽셀로 다듬어 기준점 떨림(→ 자동 보정 흔들림)을 줄인다.
    parameters.cornerRefinementMethod = cv2.aruco.CORNER_REFINE_SUBPIX

    return cv2.aruco.ArucoDetector(
        aruco_dict,
        parameters
    )


def detect_aruco_points(
    frame,
    detector
):
    """현재 프레임에서 ID 0, 1, 2, 3의 중심점을 검출한다."""
    marker_reference_points = detect_marker_points(
        frame,
        detector
    )

    if not all(
        marker_id in marker_reference_points
        for marker_id in ARUCO_IDS
    ):
        return None

    # ID 0: 왼쪽 위
    # ID 1: 오른쪽 위
    # ID 2: 오른쪽 아래
    # ID 3: 왼쪽 아래
    return np.array(
        [marker_reference_points[marker_id] for marker_id in ARUCO_IDS],
        dtype=np.float32
    )


class MarkerTracker:
    """프레임마다 일부 마커만 잡혀도 네 기준점을 이어 붙인다.

    - 최근 MARKER_MEMORY_SECONDS 안에 본 마커는 마지막 위치를 쓴다.
    - 그래도 하나가 비면(손이 가린 경우 등) 나머지 셋의 이동으로
      기준 좌표의 네 번째 점을 옮겨 채운다.
    """

    def __init__(self, detector=None):
        self.detector = detector or create_aruco_detector()
        # 이번 프레임에서 실제로 찾은 ID → 기준점 (화면 표시용)
        self.seen = {}
        # 최근에 본 ID 목록 (상태 표시용)
        self.recent_ids = []
        # 세 점으로 추정해 채운 ID, 없으면 None
        self.estimated_id = None
        self._last = {}

    def reset(self):
        """좌표계가 바뀌면(회전·카메라 전환) 기억한 위치를 버린다."""
        self._last.clear()
        self.seen = {}
        self.recent_ids = []
        self.estimated_id = None

    def update(self, frame, reference_points=None):
        """ID 0~3 순서의 네 점, 채울 수 없으면 None."""
        now = time.monotonic()
        self.seen = detect_marker_points(frame, self.detector)

        for marker_id, point in self.seen.items():
            self._last[marker_id] = (point, now)

        recent = {
            marker_id: point
            for marker_id, (point, seen_at) in self._last.items()
            if now - seen_at <= MARKER_MEMORY_SECONDS
        }
        self.recent_ids = sorted(recent)
        self.estimated_id = None

        missing = [
            marker_id for marker_id in ARUCO_IDS
            if marker_id not in recent
        ]

        if len(missing) == 1 and reference_points is not None:
            known = [
                marker_id for marker_id in ARUCO_IDS
                if marker_id in recent
            ]
            reference = np.asarray(reference_points, dtype=np.float32)
            affine = cv2.getAffineTransform(
                reference[known],
                np.array([recent[marker_id] for marker_id in known],
                         dtype=np.float32)
            )
            recent[missing[0]] = cv2.transform(
                reference[missing].reshape(1, 1, 2),
                affine
            ).reshape(2)
            self.estimated_id = missing[0]
            missing = []

        if missing:
            return None

        return np.array(
            [recent[marker_id] for marker_id in ARUCO_IDS],
            dtype=np.float32
        )


def detect_marker_points(
    frame,
    detector
):
    """현재 프레임에서 찾은 ID 0~3 마커의 기준점을 {ID: 점} 으로 준다."""
    corners, ids, _ = detector.detectMarkers(
        frame
    )

    if ids is None:
        return {}

    marker_reference_points = {}

    for marker_corner, marker_id in zip(
        corners,
        ids.flatten()
    ):
        marker_points = marker_corner[0]
        marker_id = int(marker_id)

        if marker_id not in ARUCO_IDS:
            continue

        if ARUCO_KEYBOARD_CORNER_INDICES is None:
            reference_point = marker_points.mean(axis=0)
        else:
            if len(ARUCO_KEYBOARD_CORNER_INDICES) != 4:
                raise ValueError(
                    "ARUCO_KEYBOARD_CORNER_INDICES must contain 4 indices"
                )

            corner_index = ARUCO_KEYBOARD_CORNER_INDICES[marker_id]

            if corner_index not in (0, 1, 2, 3):
                raise ValueError("ArUco corner indices must be from 0 to 3")

            reference_point = marker_points[corner_index]

        marker_reference_points[marker_id] = reference_point

    return marker_reference_points


def calculate_average_movement(
    old_points,
    new_points
):
    """기존 좌표와 새 좌표 사이의 평균 이동 거리를 계산한다."""
    distances = np.linalg.norm(
        new_points - old_points,
        axis=1
    )

    return float(np.mean(distances))


def calculate_max_movement(
    old_points,
    new_points
):
    """네 점 중 가장 큰 이동 거리를 계산한다."""
    distances = np.linalg.norm(
        new_points - old_points,
        axis=1
    )

    return float(np.max(distances))


def smooth_points(
    old_points,
    new_points
):
    """기존 좌표와 새로운 좌표를 보간한다."""
    return (
        (1.0 - SMOOTHING_ALPHA) * old_points
        + SMOOTHING_ALPHA * new_points
    )


def draw_main_screen(
    frame,
    detected_points,
    calibration_points,
    status_message,
    status_color,
    update_count,
    auto_update_enabled
):
    """메인 화면에 검출 좌표와 현재 적용 좌표를 표시한다."""
    display = frame.copy()

    # ArUco로 현재 검출한 좌표: 초록색
    if detected_points is not None:
        detected_int = np.round(
            detected_points
        ).astype(np.int32)

        for marker_id, point in enumerate(
            detected_int
        ):
            cv2.circle(
                display,
                tuple(point),
                6,
                (0, 255, 0),
                -1
            )

            cv2.putText(
                display,
                f"ID {marker_id}",
                (point[0] + 10, point[1] - 10),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.6,
                (0, 255, 0),
                2
            )

        cv2.polylines(
            display,
            [detected_int],
            True,
            (0, 255, 0),
            2
        )

    # 현재 실제로 적용 중인 좌표: 노란색
    if calibration_points is not None:
        calibration_int = np.round(
            calibration_points
        ).astype(np.int32)

        cv2.polylines(
            display,
            [calibration_int],
            True,
            (0, 255, 255),
            3
        )

        for index, point in enumerate(
            calibration_int
        ):
            cv2.circle(
                display,
                tuple(point),
                5,
                (0, 255, 255),
                -1
            )

            cv2.putText(
                display,
                str(index),
                (point[0] + 8, point[1] + 20),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.55,
                (0, 255, 255),
                2
            )

    auto_text = (
        "ArUco auto update: ON"
        if auto_update_enabled
        else "ArUco auto update: OFF"
    )

    cv2.putText(
        display,
        status_message,
        (20, 35),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.65,
        status_color,
        2
    )

    cv2.putText(
        display,
        auto_text,
        (20, 65),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.6,
        (255, 255, 255),
        2
    )

    cv2.putText(
        display,
        f"Update count: {update_count}",
        (20, 95),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.6,
        (255, 255, 255),
        2
    )

    cv2.putText(
        display,
        "M: manual / A: auto toggle / R: ArUco reset / ESC: exit",
        (20, display.shape[0] - 45),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.5,
        (255, 255, 255),
        1
    )

    cv2.putText(
        display,
        "Green: detected ArUco / Yellow: applied calibration",
        (20, display.shape[0] - 20),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.5,
        (255, 255, 255),
        1
    )

    return display


def main():
    """수동 및 ArUco 캘리브레이션을 함께 실행한다."""
    print("캘리브레이션 파일 경로:")
    print(CALIB_FILE)

    # Windows에서 노트북 카메라 실행이 느린 경우 CAP_DSHOW가 유리할 수 있음
    if os.name == "nt":
        cap = cv2.VideoCapture(
            0,
            cv2.CAP_DSHOW
        )
    else:
        cap = cv2.VideoCapture(0)

    if not cap.isOpened():
        print("카메라를 열 수 없습니다.")
        return

    cap.set(
        cv2.CAP_PROP_FRAME_WIDTH,
        1280
    )

    cap.set(
        cv2.CAP_PROP_FRAME_HEIGHT,
        720
    )

    detector = create_aruco_detector()

    calibration_points = load_points()

    candidate_points = None
    stable_count = 0
    missing_frame_count = 0
    update_count = 0

    # 실행 중 A 키로 켜고 끌 수 있음
    auto_update_enabled = True

    if calibration_points is None:
        print()
        print("저장된 캘리브레이션 좌표가 없습니다.")
        print("두 가지 방법 중 하나를 사용하세요.")
        print("1. 마커 ID 0, 1, 2, 3을 카메라에 보여주기")
        print("2. M 키를 눌러 수동 캘리브레이션 시작")

    else:
        print()
        print("저장된 캘리브레이션 좌표를 적용했습니다.")
        print(calibration_points)

    print()
    print("조작 방법")
    print("M: 수동 캘리브레이션")
    print("A: ArUco 자동 갱신 켜기/끄기")
    print("R: 현재 검출된 ArUco 좌표로 즉시 재설정")
    print("ESC: 종료")

    while True:
        ret, frame = cap.read()

        if not ret:
            print("프레임 읽기 실패")
            break

        detected_points = detect_aruco_points(
            frame,
            detector
        )

        valid_aruco = (
            detected_points is not None
            and validate_points(detected_points)
        )

        status_message = ""
        status_color = (0, 255, 255)

        if valid_aruco:
            missing_frame_count = 0

            # 아직 적용된 좌표가 없으면 ArUco로 초기 캘리브레이션
            if calibration_points is None:
                calibration_points = (
                    detected_points.copy()
                )

                save_points(calibration_points)

                update_count += 1
                candidate_points = None
                stable_count = 0

                status_message = (
                    "Initial ArUco calibration completed"
                )

                status_color = (0, 255, 0)

                print("ArUco 초기 캘리브레이션 완료")
                print(calibration_points)

            elif auto_update_enabled:
                average_movement = (
                    calculate_average_movement(
                        calibration_points,
                        detected_points
                    )
                )

                max_movement = (
                    calculate_max_movement(
                        calibration_points,
                        detected_points
                    )
                )

                if (
                    average_movement
                    >= MOVEMENT_THRESHOLD
                ):
                    if candidate_points is None:
                        candidate_points = (
                            detected_points.copy()
                        )

                        stable_count = 1

                    else:
                        candidate_movement = (
                            calculate_average_movement(
                                candidate_points,
                                detected_points
                            )
                        )

                        if (
                            candidate_movement
                            <= STABILITY_THRESHOLD
                        ):
                            candidate_points = (
                                candidate_points
                                * stable_count
                                + detected_points
                            ) / (stable_count + 1)

                            stable_count += 1

                        else:
                            candidate_points = (
                                detected_points.copy()
                            )

                            stable_count = 1

                    status_message = (
                        f"Movement detected: "
                        f"{average_movement:.1f}px "
                        f"({stable_count}/"
                        f"{STABLE_FRAME_COUNT})"
                    )

                    if (
                        stable_count
                        >= STABLE_FRAME_COUNT
                    ):
                        calibration_points = (
                            smooth_points(
                                calibration_points,
                                candidate_points
                            )
                        )

                        save_points(
                            calibration_points
                        )

                        update_count += 1

                        print(
                            f"ArUco 자동 갱신 "
                            f"#{update_count}"
                        )

                        print(
                            f"평균 이동 거리: "
                            f"{average_movement:.2f}px"
                        )

                        print(
                            f"최대 이동 거리: "
                            f"{max_movement:.2f}px"
                        )

                        print(calibration_points)

                        candidate_points = None
                        stable_count = 0

                        status_message = (
                            "ArUco calibration updated"
                        )

                        status_color = (
                            0,
                            255,
                            0
                        )

                else:
                    candidate_points = None
                    stable_count = 0

                    status_message = (
                        f"Calibration stable: "
                        f"{average_movement:.1f}px"
                    )

                    status_color = (
                        0,
                        255,
                        0
                    )

            else:
                candidate_points = None
                stable_count = 0

                status_message = (
                    "ArUco detected - auto update disabled"
                )

                status_color = (
                    255,
                    255,
                    0
                )

        else:
            missing_frame_count += 1
            candidate_points = None
            stable_count = 0

            if calibration_points is None:
                status_message = (
                    "Show ArUco markers or press M"
                )

                status_color = (
                    0,
                    0,
                    255
                )

            elif (
                missing_frame_count
                <= MAX_MISSING_FRAMES
            ):
                status_message = (
                    "Marker temporarily missing "
                    "- using previous calibration"
                )

                status_color = (
                    0,
                    165,
                    255
                )

            else:
                status_message = (
                    "Markers missing "
                    "- previous calibration maintained"
                )

                status_color = (
                    0,
                    0,
                    255
                )

        display = draw_main_screen(
            frame,
            detected_points,
            calibration_points,
            status_message,
            status_color,
            update_count,
            auto_update_enabled
        )

        cv2.imshow(
            "Combined Calibration",
            display
        )

        key = cv2.waitKey(1) & 0xFF

        if key == 27:
            break

        # 수동 캘리브레이션 실행
        if key in (ord("m"), ord("M")):
            manual_result = manual_calibrate(
                cap
            )

            if manual_result is not None:
                calibration_points = (
                    manual_result.copy()
                )

                candidate_points = None
                stable_count = 0
                missing_frame_count = 0
                update_count += 1

                print(
                    "수동 좌표를 현재 "
                    "캘리브레이션으로 적용했습니다."
                )

        # ArUco 자동 갱신 켜기/끄기
        if key in (ord("a"), ord("A")):
            auto_update_enabled = (
                not auto_update_enabled
            )

            candidate_points = None
            stable_count = 0

            print(
                "ArUco 자동 갱신:",
                (
                    "ON"
                    if auto_update_enabled
                    else "OFF"
                )
            )

        # 현재 검출된 ArUco 좌표로 즉시 재설정
        if key in (ord("r"), ord("R")):
            if valid_aruco:
                calibration_points = (
                    detected_points.copy()
                )

                save_points(
                    calibration_points
                )

                candidate_points = None
                stable_count = 0
                missing_frame_count = 0
                update_count += 1

                print(
                    "현재 ArUco 좌표로 "
                    "캘리브레이션을 재설정했습니다."
                )

                print(calibration_points)

            else:
                print(
                    "ID 0, 1, 2, 3 마커가 "
                    "모두 검출되지 않았습니다."
                )

    cap.release()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
