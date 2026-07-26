import cv2
import sys
import threading
import time
from pathlib import Path

# Project paths must be registered before importing local modules.
SRC_DIR = Path(__file__).resolve().parents[1]
PROJECT_ROOT = Path(__file__).resolve().parents[2]

if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

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
from midi.inputs import create_input_source
from perform.session import load_judgement_notes, run_judgement
from settings import Settings
from utils.transform import get_matrix, warp


DEFAULT_SCORE_PATH = PROJECT_ROOT / "songs" / "twinkle-twinkle-little-star.mid"
SONGS_DIR = PROJECT_ROOT / "songs"
MIDI_EXTENSIONS = {".mid", ".midi"}
SESSION_COUNTDOWN = 3
# Keep judgement and AR guidance on the same slower timeline.
# 1.0 is the original tempo; lower values make the performance slower.
PLAYBACK_SPEED_SCALE = 0.35


class PerformanceSession:
    """Run MIDI judgement without blocking the camera/AR render loop."""

    def __init__(self, score_path, speed, input_source):
        self.score_path = score_path
        self.speed = speed
        self.input_source = input_source
        self.stop_event = threading.Event()
        self.thread = None
        self.started_at = None
        self.event = {"type": "ready"}
        self.result = None
        self.error = None
        self._lock = threading.Lock()

    def _on_event(self, event):
        with self._lock:
            self.event = event
            if event["type"] == "start":
                self.started_at = time.monotonic()
                self.result = None
            elif event["type"] == "done":
                self.result = event["result"]

    def _run(self):
        try:
            with create_input_source(self.input_source) as source:
                run_judgement(
                    str(self.score_path),
                    speed=self.speed,
                    countdown=SESSION_COUNTDOWN,
                    sound=True,
                    input_source=source,
                    on_event=self._on_event,
                    stop_event=self.stop_event,
                )
        except Exception as error:  # Keep the AR window alive on MIDI errors.
            with self._lock:
                self.error = str(error)
                self.event = {"type": "error"}
            print(f"Performance session failed: {error}")

    def start(self):
        if self.thread is not None and self.thread.is_alive():
            return
        self.stop_event.clear()
        with self._lock:
            self.started_at = None
            self.result = None
            self.error = None
            self.event = {"type": "ready"}
        self.thread = threading.Thread(
            target=self._run,
            name="performance-session",
            daemon=True,
        )
        self.thread.start()

    def restart(self):
        self.stop()
        if self.thread is not None:
            self.thread.join(timeout=1.0)
        self.stop_event = threading.Event()
        self.start()

    def stop(self):
        self.stop_event.set()

    def snapshot(self):
        with self._lock:
            return self.started_at, dict(self.event), self.result, self.error


def _session_status(event, result, error):
    if error:
        return f"MIDI error: {error}", (0, 0, 255)
    if result:
        return (
            f"Finished  Overall {result['overall']:.1f}%  "
            f"Pitch {result['pitch_accuracy']:.1f}%  "
            f"Timing {result['timing_accuracy']:.1f}%",
            (0, 255, 0),
        )

    event_type = event.get("type")
    if event_type == "countdown":
        return f"Get ready: {event['seconds']}", (0, 255, 255)
    if event_type == "start":
        return f"Play: {event.get('next', '')}", (0, 255, 0)
    if event_type == "note":
        pitch = "OK" if event["pitch_ok"] else "WRONG"
        return (
            f"{event['index']}/{event['total']}  {pitch}  "
            f"{event['grade']} ({event['diff_ms']:+d}ms)  "
            f"Next: {event.get('next') or '-'}",
            (0, 255, 0) if event["pitch_ok"] else (0, 0, 255),
        )
    if event_type == "aborted":
        return "Performance stopped", (0, 165, 255)
    return "Preparing MIDI input...", (255, 255, 255)


def _display_song_name(path):
    return path.stem.replace("-", " ").replace("_", " ").title()


def _get_score_path():
    if len(sys.argv) > 1:
        return Path(sys.argv[1]).expanduser().resolve()

    if not SONGS_DIR.is_dir():
        print(f"Songs directory not found: {SONGS_DIR}")
        return DEFAULT_SCORE_PATH

    song_paths = sorted(
        path
        for path in SONGS_DIR.iterdir()
        if path.is_file() and path.suffix.lower() in MIDI_EXTENSIONS
    )

    if not song_paths:
        print(f"No MIDI files found in {SONGS_DIR}")
        return DEFAULT_SCORE_PATH

    default_index = 0

    if DEFAULT_SCORE_PATH in song_paths:
        default_index = song_paths.index(DEFAULT_SCORE_PATH)

    print()
    print("Select a song:")

    for index, path in enumerate(song_paths, start=1):
        default_marker = " (default)" if index - 1 == default_index else ""
        print(f"{index}. {_display_song_name(path)}{default_marker}")

    while True:
        choice = input(
            f"Song number [default {default_index + 1}]: "
        ).strip()

        if not choice:
            return song_paths[default_index]

        if choice.isdigit():
            selected_index = int(choice) - 1

            if 0 <= selected_index < len(song_paths):
                return song_paths[selected_index]

        print(f"Enter a number from 1 to {len(song_paths)}.")


def _load_score_notes():
    score_path = _get_score_path()

    if not score_path.is_file():
        print(f"Score file not found: {score_path}")
        return [], None, score_path

    if score_path.suffix.lower() not in MIDI_EXTENSIONS:
        print(f"Unsupported score file: {score_path}")
        print("Use a .mid or .midi file.")
        return [], None, score_path

    try:
        notes = load_judgement_notes(str(score_path))
        bpm = None
    except ModuleNotFoundError as error:
        if error.name == "mido":
            print("Failed to load score: Python package 'mido' is missing.")
            print("Install it with:")
            print(f"{sys.executable} -m pip install mido")
            return [], None, score_path

        raise
    except OSError as error:
        print(f"Failed to load score: {score_path}")
        print(error)
        return [], None, score_path

    print(f"Loaded score: {score_path}")
    print(f"Judgement/AR notes: {len(notes)}")
    if notes:
        print(f"First target MIDI note: {notes[0]['note']}")

    return notes, bpm, score_path


def main():
    score_notes, _, score_path = _load_score_notes()

    if not score_notes:
        print("No playable score notes were loaded.")
        return

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
    settings = Settings()
    playback_speed = settings.speed * PLAYBACK_SPEED_SCALE
    performance = PerformanceSession(
        score_path,
        speed=playback_speed,
        input_source=settings.input_source,
    )
    print(
        f"Playback speed: {playback_speed:g}x "
        f"(settings {settings.speed:g}x * AR scale {PLAYBACK_SPEED_SCALE:g})"
    )
    performance.start()

    candidate_points = None
    stable_count = 0
    update_count = 0
    auto_update_enabled = True
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

        started_at, session_event, result, session_error = (
            performance.snapshot()
        )
        playback_time = (
            (time.monotonic() - started_at) * performance.speed
            if started_at is not None else -1.0
        )
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
        session_message, session_color = _session_status(
            session_event,
            result,
            session_error,
        )
        cv2.putText(
            output,
            session_message,
            (15, 50),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.50,
            session_color,
            2,
        )
        cv2.putText(
            output,
            "M: manual / A: auto / R: reset ArUco / P: restart session / ESC",
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
            performance.restart()
            print("Performance session restarted")
    performance.stop()
    if performance.thread is not None:
        performance.thread.join(timeout=1.0)
    cap.release()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
