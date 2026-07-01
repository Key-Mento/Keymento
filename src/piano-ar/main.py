import cv2
import sys
import time
from pathlib import Path

from ar.overlay import render
from calibration.calibrator import (
    MOVEMENT_THRESHOLD,
    STABILITY_THRESHOLD,
    STABLE_FRAME_COUNT,
    calculate_average_movement,
    calculate_max_movement,
    calibrate,
    create_aruco_detector,
    detect_aruco_points,
    manual_calibrate,
    save_points,
    smooth_points,
    validate_points,
)
from camera.capture import open_camera
from keyboard.mapping import build_keys
from utils.transform import get_matrix, warp

SRC_DIR = Path(__file__).resolve().parents[1]
PROJECT_ROOT = Path(__file__).resolve().parents[2]

if str(SRC_DIR) not in sys.path:
    sys.path.append(str(SRC_DIR))

from score.score_loader import load_score


DEFAULT_SCORE_PATH = PROJECT_ROOT / "songs" / "twinkle-twinkle-little-star.mid"


def _get_score_path():
    if len(sys.argv) > 1:
        return Path(sys.argv[1]).expanduser().resolve()

    return DEFAULT_SCORE_PATH


def _load_score_notes():
    score_path = _get_score_path()

    try:
        notes, bpm = load_score(str(score_path))
    except OSError as error:
        print(f"Failed to load score: {score_path}")
        print(error)
        return [], None, score_path

    print(f"Loaded score: {score_path}")
    print(f"Notes: {len(notes)} / BPM: {bpm}")

    return notes, bpm, score_path


def main():
    score_notes, _, _ = _load_score_notes()

    cap = open_camera()

    points = calibrate(cap)

    if points is None or len(points) != 4:
        print("Calibration failed or canceled")
        cap.release()
        cv2.destroyAllWindows()
        return

    calibration_points = points.copy()
    matrix = get_matrix(calibration_points)
    detector = create_aruco_detector()

    candidate_points = None
    stable_count = 0
    update_count = 0
    auto_update_enabled = True
    playback_start_time = time.time()

    while True:
        ret, frame = cap.read()
        if not ret:
            break

        detected_points = detect_aruco_points(frame, detector)
        valid_aruco = (
            detected_points is not None
            and validate_points(detected_points)
        )

        status_message = "ArUco not detected"
        status_color = (0, 0, 255)

        if valid_aruco:
            if auto_update_enabled:
                average_movement = calculate_average_movement(
                    calibration_points,
                    detected_points
                )
                max_movement = calculate_max_movement(
                    calibration_points,
                    detected_points
                )

                if average_movement >= MOVEMENT_THRESHOLD:
                    if candidate_points is None:
                        candidate_points = detected_points.copy()
                        stable_count = 1
                    else:
                        candidate_movement = calculate_average_movement(
                            candidate_points,
                            detected_points
                        )

                        if candidate_movement <= STABILITY_THRESHOLD:
                            candidate_points = (
                                candidate_points * stable_count
                                + detected_points
                            ) / (stable_count + 1)
                            stable_count += 1
                        else:
                            candidate_points = detected_points.copy()
                            stable_count = 1

                    status_message = (
                        f"ArUco movement {average_movement:.1f}px "
                        f"({stable_count}/{STABLE_FRAME_COUNT})"
                    )
                    status_color = (0, 165, 255)

                    if stable_count >= STABLE_FRAME_COUNT:
                        calibration_points = smooth_points(
                            calibration_points,
                            candidate_points
                        )
                        matrix = get_matrix(calibration_points)
                        save_points(calibration_points)

                        update_count += 1
                        candidate_points = None
                        stable_count = 0

                        print(
                            f"ArUco calibration updated #{update_count} "
                            f"(avg={average_movement:.2f}px, "
                            f"max={max_movement:.2f}px)"
                        )

                        status_message = "ArUco calibration updated"
                        status_color = (0, 255, 0)
                else:
                    candidate_points = None
                    stable_count = 0
                    status_message = (
                        f"Calibration stable: {average_movement:.1f}px"
                    )
                    status_color = (0, 255, 0)
            else:
                candidate_points = None
                stable_count = 0
                status_message = "ArUco detected - auto update off"
                status_color = (255, 255, 0)
        else:
            candidate_points = None
            stable_count = 0

        warped = warp(frame, matrix)

        h, w = warped.shape[:2]
        whites, blacks = build_keys(w, h)

        for key in whites:
            x1, y1, x2, y2 = key
            cv2.rectangle(warped, (x1, y1), (x2, y2), (0, 255, 0), 1)

        for key in blacks:
            x1, y1, x2, y2 = key
            cv2.rectangle(warped, (x1, y1), (x2, y2), (50, 50, 50), -1)
            cv2.rectangle(warped, (x1, y1), (x2, y2), (0, 200, 255), 1)

        playback_time = time.time() - playback_start_time
        output = render(
            warped,
            whites,
            blacks,
            notes=score_notes,
            playback_time=playback_time
        )

        cv2.putText(
            output,
            status_message,
            (15, 25),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.55,
            status_color,
            2
        )
        cv2.putText(
            output,
            "M: manual / A: auto / R: reset ArUco / P: restart score / ESC",
            (15, h - 12),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.45,
            (255, 255, 255),
            1
        )

        cv2.imshow("Piano AR", output)

        key = cv2.waitKey(1) & 0xFF

        if key == 27:
            break

        if key in (ord("m"), ord("M")):
            manual_result = manual_calibrate(cap)

            if manual_result is not None:
                calibration_points = manual_result.copy()
                matrix = get_matrix(calibration_points)
                candidate_points = None
                stable_count = 0
                update_count += 1

        if key in (ord("a"), ord("A")):
            auto_update_enabled = not auto_update_enabled
            candidate_points = None
            stable_count = 0
            print(
                "ArUco auto update:",
                "ON" if auto_update_enabled else "OFF"
            )

        if key in (ord("r"), ord("R")):
            if valid_aruco:
                calibration_points = detected_points.copy()
                matrix = get_matrix(calibration_points)
                save_points(calibration_points)
                candidate_points = None
                stable_count = 0
                update_count += 1
                print("Calibration reset to current ArUco points")
            else:
                print("Cannot reset: ArUco IDs 0, 1, 2, 3 are not all visible")

        if key in (ord("p"), ord("P")):
            playback_start_time = time.time()
            print("Score playback restarted")

    cap.release()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
