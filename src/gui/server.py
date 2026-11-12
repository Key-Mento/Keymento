"""곡 선택 · 속도 · 입력 소스를 제어하는 웹 UI 서버.

표준 라이브러리(http.server)만 사용한다 — 추가 의존성 없음.
PC 에서 실행하고, 같은 네트워크의 브라우저(PC/태블릿/폰)에서
http://<PC-IP>:8321/ 으로 접속한다.

실행:
    python src\\gui\\server.py
    python src\\gui\\server.py --countdown 10 --midi-port 1

API:
    GET  /            UI 페이지
    GET  /api/state   전체 상태 (+ ?since=seq 이후의 세션 이벤트)
    POST /api/select  {"song_id": "..."}
    POST /api/speed   {"speed": 1.25}
    POST /api/input   {"source": "local" | "udp"}
    POST /api/mode    {"practice": true | false}
    POST /api/start   판정 세션 시작 (백그라운드 스레드)
    POST /api/stop    실행 중인 세션 중단
    POST /api/view    {"view": "crop"|"full"|"flat"|"sheet"|"basic"} 무대 보기
                      (AR 영상 자르기 / [실험용] 악보 / 카메라 없을 때 기본 화면)
    GET  /api/sheet   [실험용] 선택한 곡의 오선 악보 (perform/sheet.py)
"""

import argparse
import collections
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

_SRC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

from settings import Settings, SPEED_PRESETS, INPUT_SOURCES  # noqa: E402
from midi.inputs import create_input_source, UDP_DEFAULT_PORT  # noqa: E402
from perform.session import run_judgement                    # noqa: E402
from perform.sheet import build_sheet                        # noqa: E402

_INDEX_PATH = Path(__file__).resolve().parent / "index.html"

# 웹 API 판. 서버와 웹 페이지(index.html 의 API_VERSION)에서 같이 올린다.
# 페이지는 파일에서 매번 새로 읽히지만 서버는 켤 때의 코드로 돌기 때문에,
# 실행 중에 코드가 바뀌면 둘이 어긋난다 — 페이지가 이 값을 보고 '다시 실행'
# 안내를 띄우거나(서버가 예전 것), 스스로 새로 고친다(페이지가 예전 것).
API_VERSION = 3

# 세션 진행 상태의 초기값. AR 창(piano-ar/main.py)이 매 프레임 읽으므로
# 키가 빠지는 일이 없도록 한 곳에서 모양을 정해 둔다.
_EMPTY_PROGRESS = {
    "index": 0, "total": 0, "next": None, "next_notes": [],
    "last_note": None, "last_ok": None, "retries": 0, "last_missing": [],
}


class SessionManager:
    """판정 세션을 백그라운드 스레드로 돌리고 상태/이벤트를 보관한다."""

    def __init__(self, settings, midi_port=0, udp_port=UDP_DEFAULT_PORT,
                 countdown=5, sound=True, speed_scale=1.0,
                 source_factory=None, metronome=False, sheet=False):
        self.settings = settings
        self.midi_port = midi_port
        self.udp_port = udp_port
        self.countdown = countdown
        self.sound = sound
        # [실험용] 곡 템포 카운트인 + 박 클릭 (일반 모드에서만 동작)
        self.metronome = metronome
        # [실험용] 웹 UI 의 영상 자리에 계이름 악보를 띄우고 진행을 칠한다
        self.sheet = sheet
        self._sheet_cache = None         # (경로, 수정 시각, 악보 dict)
        # 입력 소스를 직접 만들어 주는 함수 (song, speed, practice)
        # -> MidiInputSource. None 이면 설정(local/udp)대로 만든다.
        # 건반 없이 돌리는 데모 모드가 이 자리에 자기 소스를 끼운다.
        self.source_factory = source_factory
        # 설정 속도에 한 번 더 곱하는 배율. AR 창은 악보가 눈으로 따라갈
        # 수 있을 만큼 느려야 해서 1 보다 작은 값을 쓴다(piano-ar/main.py).
        self.speed_scale = speed_scale

        # Latest rendered AR frame for the browser's MJPEG viewer.
        # This is presentation-only; the judgement/MIDI pipeline is unchanged.
        self._video_cond = threading.Condition()
        self._video_frame = None
        self._video_serial = 0
        self.video_enabled = False
        # 브라우저 영상 보기: crop(건반+여백) / full(카메라 전체) / flat(펴서 보기)
        self.video_view = "crop"

        self._lock = threading.Lock()
        self._thread = None
        self._stop = threading.Event()
        self._events = collections.deque(maxlen=300)  # (seq, event_dict)
        self._seq = 0

        # UI 표시용 상태 (폴링 응답에 그대로 나감)
        self.state = "idle"   # idle|countdown|playing|done|aborted|error
        self.countdown_left = 0
        # 판정 시계의 0초(PC 시계)와 카운트다운 숫자 하나의 길이(초).
        # 세션이 카운트다운을 시작할 때 알려 준다 — 브라우저가 폴링 간격과
        # 상관없이 카운트다운 숫자와 악보 진행을 제때 그리는 데 쓴다.
        self.start_at = None
        self.count_step = None
        self.progress = dict(_EMPTY_PROGRESS)
        # 일반 모드에서 이미 친 음 {(덩어리 번호, MIDI 음): (등급, 반영 시각)}.
        # AR 이 친 음의 마커를 지우고 등급 색으로 잠깐 번쩍인다. progress
        # 처럼 통째로 새 dict 로 바꿔 끼워 AR 쪽이 락 없이 읽는다.
        self.hits = {}
        self.result = None
        self.error = None
        # 세션이 시작된 시점의 모드. 진행 중에 설정이 바뀌어도 화면 표시가
        # 세션 동작과 어긋나지 않도록 start() 에서 한 번 고정한다.
        self.active_practice = None

    def publish_video_frame(self, jpeg_bytes):
        """Publish an already-rendered JPEG without changing any session state."""
        with self._video_cond:
            self._video_frame = jpeg_bytes
            self._video_serial += 1
            self._video_cond.notify_all()

    def wait_for_video_frame(self, last_serial, timeout=2.0):
        with self._video_cond:
            if self._video_serial == last_serial:
                self._video_cond.wait(timeout=timeout)
            return self._video_serial, self._video_frame

    # ── 상태 조회 ─────────────────────────────────────────────────
    def running(self):
        return self._thread is not None and self._thread.is_alive()

    def events_since(self, since):
        with self._lock:
            return [[seq, ev] for seq, ev in self._events if seq > since]

    def snapshot(self, since=0):
        return {
            "running": self.running(),
            "state": self.state,
            "countdown": self.countdown_left,
            "start_at": self.start_at,
            "count_step": self.count_step,
            "progress": dict(self.progress),
            "active_practice": self.active_practice,
            "result": self.result,
            "error": self.error,
            "seq": self._seq,
            "events": self.events_since(since),
        }

    # ── 세션 제어 ─────────────────────────────────────────────────
    def start(self):
        """세션 시작. (성공 여부, 오류 메시지) 를 반환한다."""
        with self._lock:
            if self.running():
                return False, "이미 연주가 진행 중입니다."

            song = self.settings.get_selected_song()
            if song is None:
                songs = self.settings.list_songs()
                if not songs:
                    return False, "songs/ 폴더에 곡이 없습니다."
                song = songs[0]

            self._stop.clear()
            self.state = "countdown"
            self.countdown_left = self.countdown
            self.start_at = None
            self.count_step = None
            self.progress = dict(_EMPTY_PROGRESS)
            self.hits = {}
            self.result = None
            self.error = None
            self.active_practice = self.settings.practice_mode
            self._thread = threading.Thread(
                target=self._run, args=(song,), daemon=True)
            self._thread.start()
            return True, None

    def stop(self):
        self._stop.set()

    def display_view(self):
        """브라우저 무대에 지금 무엇이 보이는가: sheet / ar-crop·ar-full·ar-flat / basic."""
        if self.sheet:
            return "sheet"
        if self.video_enabled:
            return "ar-" + self.video_view
        return "basic"

    def sheet_for_selected(self):
        """[실험용] 선택한 곡의 계이름 악보. 곡 파일이 그대로면 다시 계산하지 않는다."""
        song = self.settings.get_selected_song()
        if song is None:
            songs = self.settings.list_songs()
            if not songs:
                raise ValueError("songs/ 폴더에 곡이 없습니다.")
            song = songs[0]
        mtime = os.path.getmtime(song.path)
        cached = self._sheet_cache
        if cached is None or cached[0] != song.path or cached[1] != mtime:
            cached = (song.path, mtime, build_sheet(song.path))
            self._sheet_cache = cached
        return {"song": song.id, "name": song.name, **cached[2]}

    @property
    def effective_speed(self):
        """세션에 실제로 넘어가는 속도 (설정값 × 배율)."""
        return self.settings.speed * self.speed_scale

    # ── 내부 동작 ─────────────────────────────────────────────────
    def _push(self, event):
        """세션 이벤트를 저장하고 UI 상태 필드에 반영한다."""
        if event.get("type") == "start":
            # 시선 녹화 로그에서 어느 화면(AR 영상 / 악보)으로 친 시도인지
            # 알 수 있게 시작 순간의 보기를 남긴다.
            event = {**event, "view": self.display_view()}
        with self._lock:
            self._seq += 1
            self._events.append((self._seq, event))

            kind = event.get("type")
            if kind == "countdown":
                self.state = "countdown"
                # 메트로놈 카운트인은 초 대신 남은 박 수를 띄운다 — 숫자가
                # 클릭과 함께 바뀌어야 박을 셀 수 있다.
                self.countdown_left = event.get("beats", event["seconds"])
                self.start_at = event.get("start_at", self.start_at)
                self.count_step = event.get("step", self.count_step)
            elif kind == "start":
                self.state = "playing"
                self.countdown_left = 0
                self.start_at = event.get("t0", self.start_at)
                self.progress = dict(
                    _EMPTY_PROGRESS,
                    total=event["total"],
                    next=event["next"],
                    next_notes=list(event.get("next_notes") or []),
                )
            elif kind == "note":
                # next_notes/last_* 는 AR 창이 쓴다 — 짚어 줄 건반의 raw
                # MIDI 번호와, 방금 틀렸는지(빨간 플래시 조건)가 필요하다.
                if event.get("retry"):
                    retries = self.progress.get("retries", 0) + 1
                else:
                    retries = self.progress.get("retries", 0)
                self.progress = {
                    "index": event["index"],
                    "total": event["total"],
                    "next": event["next"],
                    "next_notes": list(event.get("next_notes") or []),
                    "last_note": event.get("played_note"),
                    "last_ok": event.get("pitch_ok"),
                    "retries": retries,
                    # 연습 모드에서 화음을 덜 쳐 실패했을 때 빠진 음
                    "last_missing": list(event.get("missing_notes") or []),
                }
                if event.get("pitch_ok") and event.get("group") is not None:
                    key = (event["group"], event["played_note"])
                    self.hits = {**self.hits,
                                 key: (event.get("grade"), time.time())}
            elif kind == "done":
                self.state = "done"
                self.result = event["result"]
            elif kind == "aborted":
                self.state = "aborted"
            elif kind == "error":
                self.state = "error"
                self.error = event["message"]

    def _run(self, song):
        source = None
        try:
            if self.source_factory is not None:
                source = self.source_factory(song, self.effective_speed,
                                             self.active_practice)
            else:
                source = create_input_source(
                    self.settings.input_source,
                    midi_port=self.midi_port, udp_port=self.udp_port)
        except Exception as exc:  # noqa: BLE001 — 장치 부재 등은 UI 로 전달
            self._push({"type": "error", "message": str(exc)})
            return

        try:
            run_judgement(
                song.path,
                speed=self.effective_speed,
                countdown=self.countdown,
                sound=self.sound,
                input_source=source,
                on_event=self._push,
                stop_event=self._stop,
                practice=self.settings.practice_mode,
                metronome=self.metronome,
            )
        except Exception as exc:  # noqa: BLE001
            self._push({"type": "error", "message": str(exc)})
        finally:
            source.close()


def _make_handler(manager: SessionManager):
    settings = manager.settings

    class _Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass  # 요청 로그 생략 (폴링이 계속 찍히는 것 방지)

        # ── 응답 헬퍼 ─────────────────────────────────────────────
        def _send_json(self, obj, status=200):
            body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _read_json(self):
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0:
                return {}
            try:
                return json.loads(self.rfile.read(length).decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                return None

        def _state_payload(self, since=0):
            settings.refresh_songs()
            selected = settings.get_selected_song()
            payload = {
                "songs": [{"id": s.id, "name": s.name}
                          for s in settings.list_songs()],
                "selected": selected.id if selected else None,
                "speed": settings.speed,
                "presets": SPEED_PRESETS,
                "input": settings.input_source,
                "input_sources": list(INPUT_SOURCES),
                "practice": settings.practice_mode,
                "speed_scale": manager.speed_scale,
                "effective_speed": manager.effective_speed,
                # [실험용] 웹 UI 의 다음 음 안내 / 실시간 피드백 표시 여부
                "show_hint": getattr(manager, "show_hint", True),
                "show_feedback": getattr(manager, "show_feedback", True),
                "metronome": manager.metronome,
                "sheet": manager.sheet,
                "video_enabled": manager.video_enabled,
                "video_view": manager.video_view,
                # 브라우저가 서버 시계와의 차이를 재는 데 쓴다(start_at 이 서버 시계).
                "server_time": time.time(),
                "api": API_VERSION,
            }
            payload.update(manager.snapshot(since))
            return payload

        # ── 라우팅 ────────────────────────────────────────────────
        def do_GET(self):
            path, _, query = self.path.partition("?")
            if path == "/":
                self._serve_index()
            elif path == "/video.jpg":
                self._serve_video_frame()
            elif path == "/video.mjpg":
                self._stream_video()
            elif path == "/api/state":
                since = 0
                for part in query.split("&"):
                    if part.startswith("since="):
                        try:
                            since = int(part[len("since="):])
                        except ValueError:
                            pass
                self._send_json(self._state_payload(since))
            elif path == "/api/sheet":
                try:
                    settings.refresh_songs()
                    self._send_json(manager.sheet_for_selected())
                except Exception as exc:  # noqa: BLE001 — 악보 오류는 화면에 띄운다
                    self._send_json({"error": str(exc)}, status=500)
            else:
                self.send_error(404)

        def _serve_video_frame(self):
            """Serve one complete JPEG; browsers fetch frames independently."""
            with manager._video_cond:
                frame = manager._video_frame
            if not manager.video_enabled or frame is None:
                self.send_error(503, "AR frame not ready")
                return
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", str(len(frame)))
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
            self.end_headers()
            try:
                self.wfile.write(frame)
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass

        def _stream_video(self):
            if not manager.video_enabled:
                self.send_error(404, "AR video is not active")
                return
            self.send_response(200)
            self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
            self.send_header("Cache-Control", "no-store, no-cache")
            self.end_headers()
            serial = -1
            try:
                while manager.video_enabled:
                    new_serial, frame = manager.wait_for_video_frame(serial)
                    if frame is None or new_serial == serial:
                        continue
                    serial = new_serial
                    self.wfile.write(b"--frame\r\n")
                    self.wfile.write(b"Content-Type: image/jpeg\r\n")
                    self.wfile.write(f"Content-Length: {len(frame)}\r\n\r\n".encode("ascii"))
                    self.wfile.write(frame)
                    self.wfile.write(b"\r\n")
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass  # browser tab closed/reloaded

        def do_POST(self):
            if manager.running() and self.path in (
                    "/api/select", "/api/speed", "/api/input", "/api/mode"):
                self._send_json(
                    {"ok": False, "error": "연주 중에는 변경할 수 없습니다."},
                    status=409)
                return

            data = self._read_json()
            if data is None:
                self._send_json({"ok": False, "error": "잘못된 JSON"}, 400)
                return

            try:
                if self.path == "/api/select":
                    settings.refresh_songs()
                    settings.select_song(str(data.get("song_id")))
                elif self.path == "/api/speed":
                    settings.set_speed(data.get("speed"))
                elif self.path == "/api/input":
                    settings.set_input_source(str(data.get("source")))
                elif self.path == "/api/mode":
                    settings.set_practice_mode(bool(data.get("practice")))
                elif self.path == "/api/view":
                    # 영상 보기(crop/full/flat), [실험용] 악보(sheet), 카메라 없을 때의
                    # 기본 화면(basic). 화면 표시만 바꾸고 판정에는 영향이 없어서
                    # 연주 중에도 바꿀 수 있다.
                    view = str(data.get("view"))
                    if view == "sheet":
                        manager.sheet = True
                    elif view in ("crop", "full", "flat"):
                        manager.sheet = False
                        manager.video_view = view
                    elif view == "basic":
                        manager.sheet = False
                    else:
                        raise ValueError("알 수 없는 보기: " + view)
                elif self.path == "/api/start":
                    ok, error = manager.start()
                    if not ok:
                        self._send_json({"ok": False, "error": error}, 409)
                        return
                elif self.path == "/api/stop":
                    manager.stop()
                else:
                    self.send_error(404)
                    return
            except (ValueError, IndexError) as exc:
                self._send_json({"ok": False, "error": str(exc)}, 400)
                return

            if self.path in ("/api/select", "/api/speed", "/api/input",
                             "/api/mode"):
                settings.save()
            self._send_json({"ok": True, **self._state_payload()})

        def _serve_index(self):
            try:
                body = _INDEX_PATH.read_bytes()
            except OSError:
                self.send_error(500, "index.html 을 찾을 수 없습니다")
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

    return _Handler


class _WebServer(ThreadingHTTPServer):
    """같은 포트로 두 번 뜨지 않는 웹 서버.

    파이썬 HTTP 서버는 SO_REUSEADDR 를 켜는데, 윈도우에서는 이 옵션이 이미 다른
    서버가 듣고 있는 포트도 또 열게 해 준다. 그러면 Keymento 를 끄지 않은 채 한 번
    더 실행했을 때 두 서버가 한 포트를 나눠 받고, 브라우저가 예전 프로그램에 붙어
    새 기능이 안 되는 것처럼 보인다. 윈도우에서는 이 옵션을 꺼서 두 번째 실행이
    OSError 로 바로 실패하게 한다(main.py 가 '이미 실행 중'이라고 알린다). 윈도우는
    이 옵션 없이도 끄자마자 다시 열 수 있지만, 다른 OS 는 아니라서 그대로 켠다.
    """

    allow_reuse_address = os.name != "nt"


def create_server(manager, host="0.0.0.0", port=8321):
    """세션 매니저에 묶인 HTTP 서버를 만든다(아직 돌리지는 않는다).

    AR 창(piano-ar/main.py)이 같은 매니저를 공유한 채 서버를 별도
    스레드로 띄우기 위한 진입점이다. 바인딩 실패(이미 실행 중 포함)는
    OSError 로 나간다.
    """
    return _WebServer((host, port), _make_handler(manager))


def parse_args():
    parser = argparse.ArgumentParser(description="Keymento 웹 UI 서버")
    parser.add_argument("--host", default="0.0.0.0",
                        help="바인딩 주소 (기본: 0.0.0.0 = 모든 인터페이스)")
    parser.add_argument("--port", type=int, default=8321,
                        help="HTTP 포트 (기본: 8321)")
    parser.add_argument("--midi-port", type=int, default=0,
                        help="로컬 입력 시 rtmidi 포트 번호 (기본: 0)")
    parser.add_argument("--udp-port", type=int, default=UDP_DEFAULT_PORT,
                        help=f"UDP 입력 수신 포트 (기본: {UDP_DEFAULT_PORT})")
    parser.add_argument("--countdown", type=int, default=5,
                        help="연주 시작 전 카운트다운 초 (기본: 5)")
    parser.add_argument("--no-sound", action="store_true",
                        help="건반 소리 에코 끄기")
    parser.add_argument("--metronome", action="store_true",
                        help="[실험용] 곡 템포로 카운트인 + 연주 내내 박 클릭 "
                             "(일반 모드만, 채점은 그대로)")
    parser.add_argument("--sheet", action="store_true",
                        help="[실험용] 영상 자리에 계이름 악보를 띄우고 "
                             "판정 시계에 맞춰 진행을 빨갛게 칠하기")
    return parser.parse_args()


def main():
    args = parse_args()
    settings = Settings()
    manager = SessionManager(
        settings,
        midi_port=args.midi_port,
        udp_port=args.udp_port,
        countdown=args.countdown,
        sound=not args.no_sound,
        metronome=args.metronome,
        sheet=args.sheet,
    )

    try:
        server = create_server(manager, args.host, args.port)
    except OSError as exc:
        print(f"포트 {args.port} 바인딩 실패: {exc}")
        print("다른 프로그램이 포트를 쓰고 있을 수 있습니다. "
              "--port 로 다른 포트를 지정해 주세요.")
        return
    print(f"🎹 Keymento 웹 UI: http://localhost:{args.port}/")
    print("   (태블릿/폰에서는 http://<PC-IP>:{0}/ 로 접속)".format(args.port))
    print("   Ctrl+C 로 종료\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n서버 종료.")
    finally:
        manager.stop()
        server.server_close()


if __name__ == "__main__":
    main()
