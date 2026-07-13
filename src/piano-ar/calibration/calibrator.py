import cv2
import numpy as np
import json
import os


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


def draw_manual_points(frame):
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

    while True:
        ret, frame = cap.read()

        if not ret:
            print("카메라 프레임 읽기 실패")
            break

        display = draw_manual_points(frame)

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

                break

            print("선택한 네 점이 올바르지 않습니다.")
            print("좌표를 다시 선택하세요.")

            manual_points = []

    cv2.destroyWindow(window_name)

    return result


def calibrate(cap):
    """Return calibration points for the AR loop.

    Saved points are reused immediately. If there are no saved points, this
    waits for either ArUco IDs 0, 1, 2, 3 or a manual calibration request.
    """
    calibration_points = load_points()

    if calibration_points is not None:
        return calibration_points

    detector = create_aruco_detector()
    window_name = "Initial Calibration"
    cv2.namedWindow(window_name)

    print("No saved calibration points found.")
    print("Show ArUco markers ID 0, 1, 2, 3 or press M for manual setup.")
    print("ESC: cancel")

    while True:
        ret, frame = cap.read()

        if not ret:
            print("Failed to read camera frame")
            break

        detected_points = detect_aruco_points(
            frame,
            detector
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
            "Show ArUco markers or press M",
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

    return cv2.aruco.ArucoDetector(
        aruco_dict,
        parameters
    )


def detect_aruco_points(
    frame,
    detector
):
    """현재 프레임에서 ID 0, 1, 2, 3의 중심점을 검출한다."""
    corners, ids, _ = detector.detectMarkers(
        frame
    )

    if ids is None:
        return None

    marker_reference_points = {}

    for marker_corner, marker_id in zip(
        corners,
        ids.flatten()
    ):
        marker_points = marker_corner[0]
        marker_id = int(marker_id)

        if marker_id not in (0, 1, 2, 3):
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

    required_ids = [0, 1, 2, 3]

    if not all(
        marker_id in marker_reference_points
        for marker_id in required_ids
    ):
        return None

    # ID 0: 왼쪽 위
    # ID 1: 오른쪽 위
    # ID 2: 오른쪽 아래
    # ID 3: 왼쪽 아래
    points = np.array(
        [
            marker_reference_points[0],
            marker_reference_points[1],
            marker_reference_points[2],
            marker_reference_points[3]
        ],
        dtype=np.float32
    )

    return points


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
