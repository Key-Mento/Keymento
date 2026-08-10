"""Keymento 통합 진입점 — AR 건반 + 웹 UI + 소리 + 연습/일반 모드.

한 프로세스에서 두 가지가 동시에 돈다.

  메인 스레드       카메라 → ArUco 보정 → 원근 보정 → 건반 AR 오버레이
  백그라운드 스레드  웹 UI 서버(:8321) + 판정 세션(src/perform/session.py)

브라우저(PC·태블릿)에서 곡·모드·속도를 고르고 시작을 누르면 AR 창이
세션 상태에 맞춰 표시를 바꾼다. 소리는 세션 쪽 NotePlayer 가 낸다
(컨트롤러에 자체 음원이 없어도 소프트웨어 신디사이저로 들린다).

  연습 모드  다음에 눌러야 할 건반을 AR 이 맥동으로 짚어 준다. 맞출
             때까지 기다리므로 박자 판정이 없다. 화음은 아직 안 친
             음만 계속 빛난다.
  일반 모드  악보를 시간축대로 흘려보내고, 음정·박자를 함께 판정한다.
             목표를 미리 짚어 주지 않는다 — 언제 치는지가 점수이므로.

세션이 cv2 창을 건드리지 않는 이유: OpenCV 의 창·키 입력은 메인
스레드에서만 안정적으로 동작한다. 그래서 세션은 상태만 갱신하고,
그리기는 전적으로 이 파일의 루프가 담당한다.

실행:
    python src/piano-ar/main.py --midi-port 2
    python src/piano-ar/main.py --list-midi     # 포트 번호 확인
    python src/piano-ar/main.py --no-serve      # 서버 없이 악보 자유 재생
"""

import argparse
import sys
import threading
import time
from pathlib import Path

import cv2
import numpy as np

# Project paths must be registered before importing local modules.
SRC_DIR = Path(__file__).resolve().parents[1]
PROJECT_ROOT = Path(__file__).resolve().parents[2]

if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

# The hyphen keeps piano-score out of normal imports, so add it explicitly.
PIANO_SCORE_DIR = SRC_DIR / "piano-score"

if str(PIANO_SCORE_DIR) not in sys.path:
    sys.path.insert(0, str(PIANO_SCORE_DIR))

from judgement import note_to_ascii                             # noqa: E402
from ar.overlay import render, visible_range                    # noqa: E402
from calibration.calibrator import (                            # noqa: E402
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
from camera.capture import open_camera                          # noqa: E402
from keyboard.mapping import build_keys                         # noqa: E402
from utils.transform import get_matrix, warp                    # noqa: E402

from gui.server import SessionManager, create_server            # noqa: E402
from midi.inputs import (                                       # noqa: E402
    UDP_DEFAULT_PORT,
    DemoInput,
    LocalMidiInput,
    MidiSystemStuck,
    is_virtual_port,
    list_midi_ports,
    midi_stuck_help,
    resolve_midi_port,
)
from perform.session import load_judgement_notes                # noqa: E402
from settings import Settings                                   # noqa: E402


SONGS_DIR = PROJECT_ROOT / "songs"
MIDI_EXTENSIONS = {".mid", ".midi"}

FONT = cv2.FONT_HERSHEY_SIMPLEX
WINDOW_NAME = "Keymento AR"

# 워프 결과는 800x200 로 납작하다(utils/transform.py). 그 위에 글자를
# 얹으면 건반이 가려지므로, 아래에 별도 HUD 띠를 붙이고 거기에 그린다.
HUD_HEIGHT = 132
HUD_BG = (24, 24, 24)

# 악보가 눈으로 따라갈 수 있을 만큼 느려야 해서 설정 속도에 한 번 더
# 곱하는 배율. 판정 목표 간격도 같은 값으로 늘어나 화면과 채점이
# 어긋나지 않는다. --speed-scale 로 바꿀 수 있다.
DEFAULT_SPEED_SCALE = 0.35

COLOR_OK = (0, 255, 0)
COLOR_WARN = (0, 165, 255)
COLOR_BAD = (0, 0, 255)
COLOR_INFO = (255, 255, 255)
COLOR_ACCENT = (255, 200, 0)

HELP_LINE = "M: manual  A: auto  R: reset ArUco  P: replay  ESC: quit"


# ════════════════════════════════════════════════════════════════════
# 그리기 헬퍼
# ════════════════════════════════════════════════════════════════════

def ascii_names(notes):
    """MIDI 번호 목록 → 화면 표기용 이름 ("C4+E4+G4"). 비면 "-".

    세션은 한글 음이름('도4')을 주지만 cv2.putText 는 Hershey 벡터
    폰트만 그릴 수 있어 ASCII 밖은 '?' 로 깨진다. 그래서 AR 창은 raw
    MIDI 번호에서 이름을 다시 만든다.
    """
    if not notes:
        return "-"

    return "+".join(note_to_ascii(int(note)) for note in notes)


def draw_text(image, text, origin, color=COLOR_INFO, scale=0.55, thickness=2):
    """읽히도록 검은 외곽선을 깔고 글자를 그린다."""
    cv2.putText(image, text, origin, FONT, scale, (0, 0, 0), thickness + 3,
                cv2.LINE_AA)
    cv2.putText(image, text, origin, FONT, scale, color, thickness,
                cv2.LINE_AA)


def draw_text_right(image, text, right_x, y, color=COLOR_INFO, scale=0.5,
                    thickness=1):
    """오른쪽 끝을 right_x 에 맞춰 그린다."""
    (tw, _), _ = cv2.getTextSize(text, FONT, scale, thickness)
    draw_text(image, text, (right_x - tw, y), color, scale, thickness)


def draw_center_text(image, text, color=COLOR_INFO, scale=3.0, dy=0):
    h, w = image.shape[:2]
    (tw, th), _ = cv2.getTextSize(text, FONT, scale, 5)
    draw_text(image, text, ((w - tw) // 2, (h + th) // 2 + dy), color,
              scale=scale, thickness=5)


def draw_progress_bar(image, ratio, y, color=COLOR_ACCENT):
    h, w = image.shape[:2]
    x1, x2 = 15, w - 15

    cv2.rectangle(image, (x1, y), (x2, y + 10), (70, 70, 70), -1)

    fill = int(x1 + (x2 - x1) * max(0.0, min(ratio, 1.0)))

    if fill > x1:
        cv2.rectangle(image, (x1, y), (fill, y + 10), color, -1)


def attach_hud_panel(warped):
    """건반 화면 아래에 HUD 띠를 붙인 캔버스를 만든다.

    Returns (canvas, hud_top) — hud_top 아래가 글자를 그려도 되는 영역.
    """
    h, w = warped.shape[:2]
    canvas = np.empty((h + HUD_HEIGHT, w, 3), dtype=warped.dtype)
    canvas[:h] = warped
    canvas[h:] = HUD_BG

    return canvas, h


# ════════════════════════════════════════════════════════════════════
# 악보 로딩 (일반 모드의 시간축 오버레이용)
# ════════════════════════════════════════════════════════════════════

def load_score_notes(score_path):
    """MIDI 파일 → 오버레이용 노트 목록. 실패하면 빈 목록.

    판정과 같은 정답지(load_judgement_notes)를 쓴다 — 화면에 뜨는 음과
    채점하는 음이 다르면 안 되기 때문이다.
    """
    score_path = Path(score_path)

    if not score_path.is_file():
        print(f"Score file not found: {score_path}")
        return []

    if score_path.suffix.lower() not in MIDI_EXTENSIONS:
        print(f"Unsupported score file: {score_path}")
        return []

    try:
        notes = load_judgement_notes(str(score_path))
    except ModuleNotFoundError as error:
        if error.name == "mido":
            print("Failed to load score: Python package 'mido' is missing.")
            print(f"Install it with: {sys.executable} -m pip install mido")
            return []
        raise
    except OSError as error:
        print(f"Failed to load score: {score_path}\n{error}")
        return []

    print(f"Loaded score: {score_path.name} ({len(notes)} notes)")

    return notes


def resolve_song_path(settings, override=None):
    """--song 인자 또는 설정이 고른 곡의 경로를 돌려준다."""
    if override:
        return Path(override).expanduser().resolve()

    song = settings.get_selected_song()

    if song is not None:
        return Path(song.path)

    songs = settings.list_songs()

    return Path(songs[0].path) if songs else None


# ════════════════════════════════════════════════════════════════════
# 세션 상태 → AR 표시
# ════════════════════════════════════════════════════════════════════

class SessionView:
    """SessionManager 의 상태를 읽어 AR 화면에 옮기는 어댑터.

    manager.snapshot() 을 매 프레임 부르지 않는 이유: 스냅샷은 락을
    잡고 이벤트 큐 전체를 훑는다. 화면에 필요한 것은 필드 몇 개뿐이고,
    그 필드들은 _push 에서 통째로 새 dict 로 교체되므로 그냥 읽어도
    찢어진 값이 나오지 않는다.
    """

    def __init__(self, manager, settings):
        self._manager = manager
        self._settings = settings
        self._prev_state = None
        self._prev_progress = None
        self._progress_at = 0.0
        self._play_started_at = None
        self._score_notes = []
        self._loaded_song = None

    # ── 매 프레임 갱신 ────────────────────────────────────────────
    def update(self):
        state = self._manager.state
        progress = self._manager.progress

        # 진행이 바뀐 시각 = 오답 플래시가 사라지기 시작하는 기준점.
        # _push 가 매번 새 dict 를 만들므로 identity 비교로 충분하다.
        if progress is not self._prev_progress:
            self._prev_progress = progress
            self._progress_at = time.time()

        if state != self._prev_state:
            self._on_state_change(state)
            self._prev_state = state

        return state, progress

    def _on_state_change(self, state):
        # 카운트다운 동안 악보를 미리 읽어 둔다(연주 시작 후 끊기지 않게).
        if state == "countdown":
            self._preload_score()
        elif state == "playing":
            if not self._score_notes:
                self._preload_score()
            # 세션의 [START] 와 최대 한 프레임(≈33ms) 어긋난다. 판정
            # 기준이 아니라 표시용 시계라 문제되지 않는다.
            self._play_started_at = time.time()

    def _preload_score(self):
        song = self._settings.get_selected_song()

        if song is None:
            return

        if self._loaded_song == song.path and self._score_notes:
            return

        self._score_notes = load_score_notes(song.path)
        self._loaded_song = song.path

    # ── 조회 ─────────────────────────────────────────────────────
    @property
    def practice(self):
        """이번 세션의 모드. 시작 전에는 설정값을 미리 보여 준다."""
        active = self._manager.active_practice

        return self._settings.practice_mode if active is None else active

    @property
    def score_notes(self):
        return self._score_notes

    @property
    def playback_time(self):
        """악보상 위치(초). 벽시계 경과 × 속도 — render 가 note['time'] 과 비교."""
        if self._play_started_at is None:
            return -1.0

        elapsed = time.time() - self._play_started_at

        return elapsed * self._manager.effective_speed

    @property
    def since_progress(self):
        return time.time() - self._progress_at

    def song_name(self):
        song = self._settings.get_selected_song()

        return song.name if song else "-"


def draw_session_layer(warped, whites, blacks, view, state, progress,
                       base_note=None):
    """세션 상태에 맞는 오버레이를 그려 새 이미지를 돌려준다."""
    if state != "playing":
        return warped.copy()

    if view.practice:
        # 연습 모드는 악보 시계가 멈춰 있다 — 아직 안 친 목표 건반만
        # 계속 맥동시킨다. 화음은 남은 음만 빛난다.
        return render(warped, whites, blacks,
                      active_notes=list(progress.get("next_notes") or []),
                      base_note=base_note)

    # 일반 모드: 악보를 시간축대로 흘려보낸다. 목표를 미리 짚어 주면
    # 언제 치는지가 점수인 의미가 없어지므로 표시하지 않는다.
    return render(warped, whites, blacks,
                  notes=view.score_notes,
                  playback_time=view.playback_time,
                  base_note=base_note)


def draw_session_hud(canvas, hud_top, view, state, progress, countdown,
                     result, error, url):
    """건반 아래 HUD 띠에 모드 · 목표 음 · 진행 · 결과를 그린다.

    카운트다운과 최종 점수만은 건반 영역 한가운데에 크게 띄운다 —
    그 순간에는 건반을 볼 필요가 없고, 멀리서도 보여야 하기 때문이다.
    """
    practice = view.practice
    mode_label = "PRACTICE" if practice else "JUDGE"
    index = progress.get("index", 0)
    total = progress.get("total", 0)
    targets = progress.get("next_notes") or []

    # ── 1줄: 모드 + 곡 ──────────────────────────────────────────
    draw_text(canvas, f"[{mode_label}] {view.song_name()}",
              (15, hud_top + 26), COLOR_ACCENT, scale=0.6)

    # ── 2·3줄: 상태별 본문 ──────────────────────────────────────
    if state == "idle":
        draw_text(canvas, "Waiting - start from the web UI",
                  (15, hud_top + 56), COLOR_INFO, scale=0.55)
        if url:
            draw_text(canvas, url, (15, hud_top + 82), COLOR_ACCENT,
                      scale=0.55)

    elif state == "countdown":
        draw_text(canvas, "Get ready...", (15, hud_top + 56), COLOR_WARN,
                  scale=0.6)
        draw_center_text(canvas[:hud_top], str(countdown), COLOR_ACCENT,
                         scale=3.0)

    elif state == "playing":
        draw_text(canvas, f"Next: {ascii_names(targets)}",
                  (15, hud_top + 60), COLOR_OK, scale=0.95, thickness=3)
        draw_text(canvas, f"{index}/{total}", (330, hud_top + 60),
                  COLOR_INFO, scale=0.7)

        if practice and progress.get("retries"):
            draw_text(canvas, f"retries {progress['retries']}",
                      (470, hud_top + 60), COLOR_WARN, scale=0.6)

        # 방금 틀린 음을 잠깐 알려 준다.
        if progress.get("last_ok") is False and view.since_progress < 1.2:
            missed = progress.get("last_note")
            played = "MISSED" if missed is None else f"X {note_to_ascii(missed)}"
            draw_text(canvas, f"{played}  ->  press {ascii_names(targets)}",
                      (15, hud_top + 88), COLOR_BAD, scale=0.6)

        draw_progress_bar(canvas, index / total if total else 0.0,
                          hud_top + 94,
                          COLOR_OK if practice else COLOR_ACCENT)

    elif state == "done" and result:
        headline = f"{result['overall']:.0f} / 100"

        if result.get("mode") == "practice":
            detail = (f"clean {result['pitch_correct']}/{result['total']}"
                      f"   retries {result['wrong_attempts']}")
        else:
            timing = result.get("timing_accuracy")
            timing_text = "-" if timing is None else f"{timing:.0f}%"
            detail = (f"pitch {result['pitch_accuracy']:.0f}%"
                      f"   timing {timing_text}")

        draw_center_text(canvas[:hud_top], headline, COLOR_OK, scale=1.7)
        draw_text(canvas, detail, (15, hud_top + 60), COLOR_INFO, scale=0.65)
        draw_progress_bar(canvas, 1.0, hud_top + 94, COLOR_OK)

    elif state == "aborted":
        draw_text(canvas, "Session stopped", (15, hud_top + 60), COLOR_WARN,
                  scale=0.7)

    elif state == "error":
        draw_text(canvas, "Session error", (15, hud_top + 56), COLOR_BAD,
                  scale=0.65)
        if error:
            draw_text(canvas, str(error)[:58], (15, hud_top + 82), COLOR_BAD,
                      scale=0.5)

    draw_text(canvas, HELP_LINE, (15, hud_top + HUD_HEIGHT - 12), COLOR_INFO,
              scale=0.45, thickness=1)


# ════════════════════════════════════════════════════════════════════
# AR 메인 루프
# ════════════════════════════════════════════════════════════════════

def run_ar_loop(cap, calibration_points, view=None, manager=None,
                free_notes=None, url=None, base_note=None):
    """카메라 루프. view/manager 가 없으면 악보를 자유 재생한다."""
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
        status_color = COLOR_BAD

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
                    status_color = COLOR_WARN

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
                        status_color = COLOR_OK
                else:
                    candidate_points = None
                    stable_count = 0
                    status_message = (
                        f"Calibration stable: {average_movement:.1f}px"
                    )
                    status_color = COLOR_OK
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

        # ── 세션 연동 표시 / 자유 재생 ────────────────────────────
        if view is not None:
            state, progress = view.update()
            layered = draw_session_layer(warped, whites, blacks, view,
                                         state, progress, base_note)
            output, hud_top = attach_hud_panel(layered)
            draw_session_hud(output, hud_top, view, state, progress,
                             manager.countdown_left, manager.result,
                             manager.error, url)
        else:
            layered = render(warped, whites, blacks, notes=free_notes,
                             playback_time=time.time() - playback_start_time,
                             base_note=base_note)
            output, hud_top = attach_hud_panel(layered)
            draw_text(output, "Free score playback (no session)",
                      (15, hud_top + 26), COLOR_ACCENT, scale=0.6)
            draw_text(output, HELP_LINE, (15, hud_top + HUD_HEIGHT - 12),
                      COLOR_INFO, scale=0.45, thickness=1)

        # ArUco 상태는 HUD 우측에 — 건반 영역은 오버레이 전용으로 비운다.
        draw_text_right(output, status_message, w - 15, hud_top + 26,
                        status_color, scale=0.5)

        cv2.imshow(WINDOW_NAME, output)

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
            if manager is not None:
                # 세션 모드에서는 브라우저의 '시작'과 같은 동작을 준다.
                ok, error = manager.start()
                print("Session restarted" if ok else f"Cannot start: {error}")
            else:
                playback_start_time = time.time()
                print("Score playback restarted")


# ════════════════════════════════════════════════════════════════════
# 진입점
# ════════════════════════════════════════════════════════════════════

def parse_args():
    parser = argparse.ArgumentParser(
        description="Keymento: AR 건반 + 웹 UI + 연습/일반 세션")
    parser.add_argument("--midi-port", default="auto",
                        help="MIDI 입력 포트. 기본 auto 는 가상 포트를 빼고 "
                             "진짜 건반을 찾는다. 번호(2)나 이름 조각"
                             "(keystation)으로 직접 고를 수도 있다.")
    parser.add_argument("--udp-port", type=int, default=UDP_DEFAULT_PORT,
                        help=f"라즈베리파이 UDP 수신 포트 "
                             f"(기본: {UDP_DEFAULT_PORT})")
    parser.add_argument("--host", default="0.0.0.0",
                        help="웹 UI 바인딩 주소 (기본: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=8321,
                        help="웹 UI HTTP 포트 (기본: 8321)")
    parser.add_argument("--camera", type=int, default=0,
                        help="카메라 장치 번호 (기본: 0)")
    parser.add_argument("--countdown", type=int, default=5,
                        help="시작 전 카운트다운 초 (기본: 5)")
    parser.add_argument("--speed-scale", type=float,
                        default=DEFAULT_SPEED_SCALE,
                        help=f"설정 속도에 곱하는 배율 "
                             f"(기본: {DEFAULT_SPEED_SCALE:g} — 눈으로 "
                             f"따라갈 수 있게 느리게). 1 이면 설정값 그대로.")
    parser.add_argument("--song", default=None,
                        help="--no-serve 자유 재생에 쓸 MIDI 파일 경로")
    parser.add_argument("--demo", action="store_true",
                        help="건반 없이 확인 — 정답지를 자동으로 연주한다")
    parser.add_argument("--demo-wrong", type=int, default=7, metavar="N",
                        help="데모에서 N 번째마다 일부러 틀리기 "
                             "(기본: 7, 0 이면 전부 정확히)")
    parser.add_argument("--no-camera", action="store_true",
                        help="카메라·AR 창 없이 웹 UI 와 판정만 돌리기")
    parser.add_argument("--no-browser", action="store_true",
                        help="브라우저 자동 열기 끄기")
    parser.add_argument("--no-sound", action="store_true",
                        help="건반 소리 에코 끄기")
    parser.add_argument("--no-serve", action="store_true",
                        help="웹 UI·세션 없이 악보만 자유 재생")
    parser.add_argument("--list-midi", action="store_true",
                        help="MIDI 입력 포트 목록만 출력하고 종료")
    parser.add_argument("--guide", action="store_true",
                        help="한글 실행 안내를 출력하고 종료 (run help)")
    parser.add_argument("--base-note", type=int, default=None, metavar="N",
                        help="AR 맨 왼쪽 흰건반의 MIDI 번호 (도여야 함). "
                             "생략하면 설정값을 쓴다.")
    parser.add_argument("--detect-base", action="store_true",
                        help="건반의 맨 왼쪽 키를 눌러 기준음을 잡고 저장 "
                             "(run base)")
    return parser.parse_args()


GUIDE = """
Keymento 실행 안내
==================

  ┌──────────────┬──────────────┬──────────────┐
  │              │  내가 연주    │  자동 연주    │
  ├──────────────┼──────────────┼──────────────┤
  │  카메라 O     │  run         │  run demo    │
  │  카메라 X     │  run nocam   │  run selftest│
  └──────────────┴──────────────┴──────────────┘

  run              ★ 메인 — 웹에서 곡 고르고, AR 이 짚어 주면, 내가 친다
  run nocam        카메라 세팅 전에도 됨. 쳐야 할 음과 판정이 브라우저에 나옴
  run demo         내가 안 쳐도 알아서 연주 — 카메라/AR 이 잘 도는지 확인용
  run selftest     자동 + 카메라도 없음 — 코드 고치고 빠르게 점검할 때

  run base         AR 기준음 잡기 (건반 맨 왼쪽 키를 눌러 맞춤) ★처음 한 번
  run ports        연결된 MIDI 입력 포트 목록
  run help         이 안내

  demo / selftest 는 '자동'입니다. 건반을 쳐도 반영되지 않습니다.

처음 한 번은 기준음을 맞추세요
------------------------------
  AR 이 가정하는 '맨 왼쪽 흰건반'이 실제 건반과 다르면 마커가 통째로
  옥타브 단위로 밀립니다. 짚어 준 건반을 눌러도 판정이 안 맞고, 연습
  모드는 그 자리에서 계속 기다립니다.

    run base        → 맨 왼쪽 키를 한 번 누르면 저장됩니다

  숫자로 직접 줄 수도 있습니다 (도여야 함): run --base-note 60

무엇을 켜고 꺼야 하나
---------------------
  아무것도 안 켜도 됩니다.
    - venv       run.bat 이 알아서 씁니다
    - MIDI 포트  기본 auto 가 진짜 건반을 찾습니다 (loopMIDI 는 건너뜀)
    - 브라우저   시작하면 자동으로 열립니다
    - loopMIDI   run test / run demo 를 쓰면 아예 필요 없습니다

  건반이 자동으로 안 잡힐 때만 직접 고르세요:
    run --midi-port 2
    run --midi-port keystation      (이름 조각으로도 됩니다)

메인 흐름 (run)
---------------
  1. 브라우저가 자동으로 열립니다
  2. 곡 · 모드(연습/일반) · 속도를 고르고 [연주 시작]
  3. 카운트다운 뒤, 쳐야 할 건반이 AR 화면에 표시됩니다
     - 연습 모드: 맞출 때까지 그 건반이 계속 맥동합니다
     - 일반 모드: 악보 시간대로 마커가 흘러갑니다
  4. 치면 브라우저에 ✅/❌ 와 등급이 뜨고 다음 음으로 넘어갑니다
  5. 끝나면 최종 점수

  카메라가 아직 건반을 못 비추면 run nocam 으로 3~5 번을 그대로 쓸 수
  있습니다. AR 표시만 빠지고 나머지는 같습니다.

자주 쓰는 조합
--------------
  run selftest                코드 고치고 바로 확인 (10초, 자동)
  run --speed-scale 1         AR 감속 배율 끄기 (설정 속도 그대로)
  run --camera 1              두 번째 카메라 쓰기
  run --no-browser            브라우저 자동 열기 끄기

곡 고르기
---------
  브라우저에서 고릅니다. twinkle 은 4473음 11분이라 시연에 맞지 않습니다.
  head-shoulder-knee-and-toe (39음, 15초) 또는
  happy-birthday (28음, 화음 3곳) 를 쓰세요.

전체 옵션: run --help
"""


def print_midi_ports():
    try:
        ports = list_midi_ports()
    except MidiSystemStuck as exc:
        print(f"\n{exc}\n")
        print(midi_stuck_help())
        return

    if not ports:
        print("MIDI 입력 포트가 없습니다. 키보드 연결을 확인하세요.")
        print("건반 없이 확인만 하려면 --demo 로 실행하세요.")
        return

    print("MIDI 입력 포트:")
    for index, name in enumerate(ports):
        print(f"  [{index}] {name}")
    print("\n보통은 그냥 두면 됩니다 — 기본값 auto 가 진짜 건반을 찾습니다.")
    print("직접 고르려면: --midi-port 2  또는  --midi-port keystation")


def report_range(settings, base_note):
    """표시 음역과, 고른 곡이 그 안에 들어오는지 알려 준다.

    범위를 벗어난 음은 마커가 아예 안 뜬다 — 시작하고 나서야 눈치채면
    늦으므로 실행 직후에 미리 말해 준다.
    """
    whites, blacks = build_keys(800, 200)
    low, high = visible_range(whites, blacks, base_note)

    from judgement import note_to_ascii

    print(f"🎹 AR 기준음: {base_note} ({note_to_ascii(base_note)})  |  "
          f"표시 음역: {note_to_ascii(low)}~{note_to_ascii(high)} "
          f"({low}~{high})")

    song = settings.get_selected_song()
    if song is None:
        return

    try:
        notes = [n["note"] for n in load_judgement_notes(song.path)]
    except Exception:  # noqa: BLE001 — 안내용이라 실패해도 진행
        return

    if not notes:
        return

    outside = [n for n in notes if n < low or n > high]

    if outside:
        print(f"   ⚠️  '{song.name}' 은 {len(outside)}/{len(notes)}음이 "
              f"표시 범위 밖입니다 "
              f"({note_to_ascii(min(notes))}~{note_to_ascii(max(notes))}). "
              f"그 음은 마커가 뜨지 않습니다.")
    else:
        print(f"   ✅ '{song.name}' ({note_to_ascii(min(notes))}~"
              f"{note_to_ascii(max(notes))}) 전 음이 표시됩니다.")


def detect_base_note(settings, midi_port_spec, timeout=30):
    """건반의 맨 왼쪽 키를 눌러 받게 하고 AR 기준음을 정해 저장한다.

    받은 음을 '그 이하의 가장 가까운 도'로 내림한다 — 맨 왼쪽 키가 도가
    아닌 건반도 있고, 사용자가 옆 키를 눌러도 같은 옥타브면 맞게 잡힌다.

    기다리는 동안 남은 초를 계속 찍는다. 잠자코 30초를 기다리면 멈춘
    것과 구분이 안 되기 때문이다.
    """
    from judgement import note_to_ascii

    try:
        ports = list_midi_ports()
    except MidiSystemStuck as exc:
        print(f"\n{exc}\n")
        print(midi_stuck_help())
        return None

    if not ports:
        print("\n[!] MIDI 입력 포트가 하나도 없습니다.")
        print("    건반의 USB 를 꽂고 다시 실행하세요.")
        return None

    port = resolve_midi_port(midi_port_spec, verbose=False)

    if not 0 <= port < len(ports):
        print(f"\n[!] {port} 번 포트가 없습니다. 사용 가능: 0~{len(ports) - 1}")
        for index, name in enumerate(ports):
            print(f"      [{index}] {name}")
        return None

    port_name = ports[port]

    # 가상 포트(loopMIDI 등)에서는 기준음을 잴 수 없다 — 아무리 기다려도
    # 사람이 누른 건반이 오지 않는다. 기다리게 두면 멈춘 것처럼 보인다.
    if is_virtual_port(port_name):
        print(f"\n[!] 잡힌 포트가 가상 포트입니다: [{port}] {port_name}")
        print("    진짜 건반이 연결돼 있지 않은 것 같습니다.")
        print("    현재 MIDI 입력 포트:")
        for index, name in enumerate(ports):
            mark = "  (가상)" if is_virtual_port(name) else "  ← 건반?"
            print(f"      [{index}] {name}{mark}")
        print("\n    건반을 꽂고 다시 실행하거나, 번호를 직접 주세요:")
        print("      run base --midi-port 2")
        print("    기준음을 숫자로 바로 넣을 수도 있습니다:")
        print("      run --base-note 60")
        return None

    try:
        source = LocalMidiInput(port=port)
    except RuntimeError as exc:
        print(f"\n[!] {exc}")
        return None

    print(f"\n🎹 듣는 중: [{port}] {port_name}")
    print(f"현재 기준음: {settings.base_note} "
          f"({note_to_ascii(settings.base_note)})")
    print("\n건반의 '맨 왼쪽 키'를 한 번 눌러 주세요.  (취소: Ctrl+C)")

    note = None
    deadline = time.time() + timeout
    last_shown = None

    try:
        while True:
            left = deadline - time.time()
            if left <= 0:
                break

            # 살아 있다는 표시 — 같은 줄을 덮어쓴다.
            seconds = int(left) + 1
            if seconds != last_shown:
                print(f"\r  대기 중... {seconds:2d}초 ", end="", flush=True)
                last_shown = seconds

            event = source.poll()
            if event is not None and event.is_on:
                note = event.note
                break

            time.sleep(0.005)
    except KeyboardInterrupt:
        print("\n취소했습니다. 기준음은 그대로입니다.")
        source.close()
        return None
    finally:
        source.close()

    print("\r" + " " * 30 + "\r", end="")

    if note is None:
        print("[!] 입력이 없었습니다. 기준음은 그대로입니다 "
              f"({settings.base_note}).")
        print("    건반이 이 포트로 보내고 있는지 확인하세요:")
        for index, name in enumerate(ports):
            print(f"      [{index}] {name}")
        print("    다른 포트면: run base --midi-port <번호>")
        return None

    base = (note // 12) * 12

    try:
        settings.set_base_note(base)
    except ValueError as exc:
        print(f"[!] {exc}")
        return None

    settings.save()

    whites, blacks = build_keys(800, 200)
    low, high = visible_range(whites, blacks, base)

    print(f"받은 음: {note} ({note_to_ascii(note)})")
    print(f"AR 기준음을 {base} ({note_to_ascii(base)}) 로 저장했습니다.")
    print(f"이제 표시 가능한 음역: {low}({note_to_ascii(low)}) ~ "
          f"{high}({note_to_ascii(high)})")
    return base


def make_demo_factory(wrong_every):
    """건반 대신 정답지를 스스로 연주하는 입력 소스를 만드는 함수."""
    def factory(song, speed, practice):
        notes = load_judgement_notes(song.path)
        print(f"🤖 데모 입력: {song.name} — {len(notes)}음을 자동 연주"
              + (f" (매 {wrong_every}번째는 일부러 틀림)"
                 if wrong_every else ""))
        return DemoInput(notes, speed=speed, wrong_every=wrong_every,
                         retry_after_wrong=practice)

    return factory


def start_web_ui(manager, args):
    """웹 UI 서버를 백그라운드 스레드로 띄운다. (server, url) 반환."""
    try:
        server = create_server(manager, args.host, args.port)
    except OSError as exc:
        print(f"포트 {args.port} 바인딩 실패: {exc}")
        print("이미 서버가 떠 있다면 --no-serve 로 실행하거나 "
              "--port 로 다른 포트를 지정하세요.")
        return None, None

    threading.Thread(target=server.serve_forever, daemon=True).start()

    url = f"http://localhost:{args.port}/"
    print(f"🎹 웹 UI: {url}")
    print(f"   태블릿/폰에서는 http://<PC-IP>:{args.port}/ 로 접속")

    if not args.no_browser:
        # 창이 이미 떠 있으면 새 탭으로 붙는다. 실패해도 무시 — 주소는
        # 위에 찍혀 있으니 직접 열면 된다.
        try:
            import webbrowser
            webbrowser.open(url)
        except Exception:  # noqa: BLE001
            pass

    return server, url


def print_banner(args):
    """누가 치는지 / 카메라를 쓰는지 시작하자마자 못 박아 둔다.

    옵션 조합만으로는 '자동 연주인데 내가 치는 줄 알고 기다리는' 상황이
    생긴다. 그래서 매 실행마다 두 줄로 분명히 찍는다.
    """
    who = "🤖 자동 연주 (데모)" if args.demo else "🙋 내가 연주"
    cam = "❌ 안 씀" if args.no_camera else "📷 사용"

    print()
    print("═" * 46)
    print(f"  연주   {who}")
    print(f"  카메라  {cam}")
    print("═" * 46)

    if args.demo:
        print("  ※ 데모입니다 — 건반을 쳐도 반영되지 않습니다.")
        print("     직접 치려면:  run        (카메라 O)")
        print("                  run nocam  (카메라 X)")


def run_headless(url, demo):
    """카메라 없이 웹 UI 만 띄워 두고 Ctrl+C 까지 기다린다."""
    print("\nAR 창 없이 실행 중입니다. 쳐야 할 음과 판정은 브라우저에 나옵니다.")
    print(f"  → {url}")
    if not demo:
        print("  브라우저에서 곡·모드를 고르고 시작을 누른 뒤, 건반을 치세요.")
    print("종료: Ctrl+C")

    while True:
        time.sleep(0.5)


def main():
    args = parse_args()

    if args.guide:
        print(GUIDE)
        return

    if args.list_midi:
        print_midi_ports()
        return

    settings = Settings()

    if args.detect_base:
        detect_base_note(settings, args.midi_port)
        return

    if args.base_note is not None:
        try:
            settings.set_base_note(args.base_note)
        except ValueError as exc:
            print(f"[!] {exc}")
            return

    print_banner(args)

    base_note = settings.base_note
    report_range(settings, base_note)

    # ── 서버 없이 악보만 흘려보는 모드 ────────────────────────────
    if args.no_serve:
        song_path = resolve_song_path(settings, args.song)

        if song_path is None:
            print("곡을 찾을 수 없습니다. songs/ 폴더를 확인하세요.")
            return

        free_notes = load_score_notes(song_path)

        if not free_notes:
            print("재생할 노트가 없습니다.")
            return

        manager = view = None
        server = url = None
    else:
        free_notes = None

        if args.demo:
            midi_port = 0
            source_factory = make_demo_factory(args.demo_wrong)
        else:
            # MIDI 가 막혀 있으면 여기서 끊는다. 그냥 진행하면 세션을
            # 시작하는 순간 입력 소스를 여느라 그 스레드가 얼어붙어,
            # AR 창은 떠 있는데 아무 반응이 없는 상태가 된다.
            try:
                list_midi_ports()
            except MidiSystemStuck as exc:
                print(f"\n{exc}\n")
                print(midi_stuck_help())
                return

            # 매번 번호를 찾아 넘기지 않아도 되도록 여기서 해결한다.
            midi_port = resolve_midi_port(args.midi_port)
            source_factory = None

        manager = SessionManager(
            settings,
            midi_port=midi_port,
            udp_port=args.udp_port,
            countdown=args.countdown,
            sound=not args.no_sound,
            speed_scale=args.speed_scale,
            source_factory=source_factory,
        )
        view = SessionView(manager, settings)
        server, url = start_web_ui(manager, args)

        if server is None:
            return

        print(f"   속도: 설정 {settings.speed:g}x × 배율 "
              f"{args.speed_scale:g} = {manager.effective_speed:g}x")

    cap = None

    try:
        # ── 카메라 없이 웹 UI·판정만 ──────────────────────────────
        if args.no_camera:
            run_headless(url, args.demo)
            return

        cap = open_camera(args.camera)
        points = calibrate(cap)

        if points is None or len(points) != 4:
            print("Calibration failed or canceled")
            return

        print("\nAR 창이 열렸습니다. " + (
            "브라우저에서 곡·모드를 고르고 시작하세요."
            if url else "P 로 악보를 다시 재생합니다."))

        run_ar_loop(cap, points.copy(), view=view, manager=manager,
                    free_notes=free_notes, url=url, base_note=base_note)
    except KeyboardInterrupt:
        print("\n종료합니다.")
    finally:
        if manager is not None:
            manager.stop()
        if server is not None:
            server.shutdown()
            server.server_close()
        if cap is not None:
            cap.release()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
