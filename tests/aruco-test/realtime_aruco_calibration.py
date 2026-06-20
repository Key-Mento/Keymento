import cv2
import numpy as np
import json
import os


# 현재 파이썬 파일이 있는 폴더
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# realtime_aruco_calibration.py와 같은 폴더에 저장
CALIB_FILE = os.path.join(
    BASE_DIR,
    "aruco_calibration_points.json"
)

# 평균적으로 이 값 이상 이동하면 유의미한 이동으로 판단
MOVEMENT_THRESHOLD = 8.0

# 새 마커 위치가 이 프레임 수만큼 안정적으로 유지되어야 갱신
STABLE_FRAME_COUNT = 5

# 연속 프레임 사이의 좌표 차이가 이 값 이하여야 안정적인 상태로 판단
STABILITY_THRESHOLD = 3.0

# 새로운 좌표를 얼마나 반영할지 결정
# 1.0이면 즉시 변경, 값이 작을수록 부드럽게 변경
SMOOTHING_ALPHA = 0.3

# 마커를 잃어버렸다고 판단할 프레임 수
MAX_MISSING_FRAMES = 30


print("캘리브레이션 파일 경로:")
print(CALIB_FILE)


# 노트북 카메라 열기
cap = cv2.VideoCapture(0)

if not cap.isOpened():
    print("카메라를 열 수 없습니다.")
    raise SystemExit


# ArUco 마커 검출기 설정
aruco_dict = cv2.aruco.getPredefinedDictionary(
    cv2.aruco.DICT_4X4_50
)

parameters = cv2.aruco.DetectorParameters()

detector = cv2.aruco.ArucoDetector(
    aruco_dict,
    parameters
)


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


def detect_points(frame):
    """현재 프레임에서 ID 0, 1, 2, 3의 중심점을 검출한다."""
    corners, ids, _ = detector.detectMarkers(frame)

    if ids is None:
        return None

    marker_centers = {}

    for marker_corner, marker_id in zip(
        corners,
        ids.flatten()
    ):
        points = marker_corner[0]
        center = points.mean(axis=0)

        marker_centers[int(marker_id)] = center

    required_ids = [0, 1, 2, 3]

    if not all(
        marker_id in marker_centers
        for marker_id in required_ids
    ):
        return None

    # ID 순서
    # 0: 왼쪽 위
    # 1: 오른쪽 위
    # 2: 오른쪽 아래
    # 3: 왼쪽 아래
    points = np.array(
        [
            marker_centers[0],
            marker_centers[1],
            marker_centers[2],
            marker_centers[3]
        ],
        dtype=np.float32
    )

    return points


def validate_points(points):
    """검출된 네 점이 정상적인 사각형인지 검사한다."""
    if points is None or points.shape != (4, 2):
        return False

    polygon = points.astype(np.float32)

    # 사각형이 볼록한지 검사
    if not cv2.isContourConvex(
        polygon.astype(np.int32)
    ):
        return False

    # 사각형 넓이가 너무 작은 경우 제외
    area = cv2.contourArea(polygon)

    if area < 5000:
        return False

    # 각 변의 길이를 계산
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

    # 위쪽과 아래쪽 변의 길이 비율
    horizontal_ratio = (
        top_length / bottom_length
    )

    # 왼쪽과 오른쪽 변의 길이 비율
    vertical_ratio = (
        left_length / right_length
    )

    if not 0.5 <= horizontal_ratio <= 2.0:
        return False

    if not 0.5 <= vertical_ratio <= 2.0:
        return False

    return True


def calculate_average_movement(
    old_points,
    new_points
):
    """기존 좌표와 새로운 좌표 사이의 평균 이동 거리를 계산한다."""
    distances = np.linalg.norm(
        new_points - old_points,
        axis=1
    )

    return float(np.mean(distances))


def calculate_max_movement(
    old_points,
    new_points
):
    """네 점 중 가장 크게 이동한 거리를 계산한다."""
    distances = np.linalg.norm(
        new_points - old_points,
        axis=1
    )

    return float(np.max(distances))


def smooth_points(
    old_points,
    new_points
):
    """기존 좌표와 새로운 좌표를 보간하여 부드럽게 갱신한다."""
    return (
        (1.0 - SMOOTHING_ALPHA) * old_points
        + SMOOTHING_ALPHA * new_points
    )


def draw_points(
    frame,
    detected_points,
    calibration_points
):
    """검출 좌표와 현재 적용 중인 캘리브레이션 좌표를 표시한다."""
    display = frame.copy()

    # 현재 프레임에서 검출된 좌표: 초록색
    if detected_points is not None:
        detected_int = np.round(
            detected_points
        ).astype(np.int32)

        for marker_id, point in enumerate(
            detected_int
        ):
            point_tuple = tuple(point)

            cv2.circle(
                display,
                point_tuple,
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

    # 실제 캘리브레이션에 사용 중인 좌표: 노란색
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

        for point in calibration_int:
            cv2.circle(
                display,
                tuple(point),
                4,
                (0, 255, 255),
                -1
            )

    return display


# 저장된 좌표가 있다면 초기 캘리브레이션으로 사용
calibration_points = load_points()

# 새로운 위치가 안정적인지 검사하기 위한 변수
candidate_points = None
stable_count = 0
missing_frame_count = 0
update_count = 0


if calibration_points is not None:
    print("저장된 캘리브레이션 좌표를 불러왔습니다.")
    print(calibration_points)

else:
    print("저장된 캘리브레이션 좌표가 없습니다.")
    print("마커 ID 0, 1, 2, 3을 카메라에 보여주세요.")


while True:
    ret, frame = cap.read()

    if not ret:
        print("프레임 읽기 실패")
        break

    detected_points = detect_points(frame)

    status_message = ""
    status_color = (0, 255, 255)

    if (
        detected_points is not None
        and validate_points(detected_points)
    ):
        missing_frame_count = 0

        # 저장된 캘리브레이션 좌표가 없는 경우
        if calibration_points is None:
            calibration_points = (
                detected_points.copy()
            )

            save_points(calibration_points)

            status_message = (
                "Initial calibration completed"
            )

            update_count += 1

            print("초기 캘리브레이션 완료")
            print(calibration_points)

        else:
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

            # 기존 위치와 비교해 유의미하게 이동한 경우
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

                    # 새로운 위치가 연속 프레임에서
                    # 비슷하게 유지되는지 확인
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

                # 일정 프레임 동안 안정적인 이동이 확인된 경우
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
                        f"캘리브레이션 자동 갱신 "
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
                        "Calibration updated"
                    )

                    status_color = (
                        0,
                        255,
                        0
                    )

            else:
                # 임계값보다 작은 움직임은 검출 오차로 판단
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
        missing_frame_count += 1
        candidate_points = None
        stable_count = 0

        # 마커를 놓쳐도 기존 캘리브레이션 유지
        if (
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
                "Show markers ID 0, 1, 2, 3"
            )

            status_color = (
                0,
                0,
                255
            )

    display = draw_points(
        frame,
        detected_points,
        calibration_points
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
        f"Update count: {update_count}",
        (20, 65),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.6,
        (255, 255, 255),
        2
    )

    cv2.putText(
        display,
        "Green: detected / Yellow: applied",
        (20, display.shape[0] - 20),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.55,
        (255, 255, 255),
        1
    )

    cv2.imshow(
        "Real-time ArUco Calibration",
        display
    )

    key = cv2.waitKey(1) & 0xFF

    # ESC 입력 시 종료
    if key == 27:
        break

    # R 또는 r 입력 시 현재 검출 좌표로 강제 재설정
    if (
        key in (ord("r"), ord("R"))
        and detected_points is not None
    ):
        if validate_points(detected_points):
            calibration_points = (
                detected_points.copy()
            )

            save_points(calibration_points)

            candidate_points = None
            stable_count = 0
            update_count += 1

            print(
                "캘리브레이션을 "
                "수동으로 재설정했습니다."
            )

            print(calibration_points)


cap.release()
cv2.destroyAllWindows()