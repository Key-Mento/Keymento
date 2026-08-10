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
"""

import argparse
import collections
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

_SRC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

from settings import Settings, SPEED_PRESETS, INPUT_SOURCES  # noqa: E402
from midi.inputs import create_input_source, UDP_DEFAULT_PORT  # noqa: E402
from perform.session import run_judgement                    # noqa: E402

_INDEX_PATH = Path(__file__).resolve().parent / "index.html"

# 세션 진행 상태의 초기값. AR 창(piano-ar/main.py)이 매 프레임 읽으므로
# 키가 빠지는 일이 없도록 한 곳에서 모양을 정해 둔다.
_EMPTY_PROGRESS = {
    "index": 0, "total": 0, "next": None, "next_notes": [],
    "last_note": None, "last_ok": None, "retries": 0,
}


class SessionManager:
    """판정 세션을 백그라운드 스레드로 돌리고 상태/이벤트를 보관한다."""

    def __init__(self, settings, midi_port=0, udp_port=UDP_DEFAULT_PORT,
                 countdown=5, sound=True, speed_scale=1.0,
                 source_factory=None):
        self.settings = settings
        self.midi_port = midi_port
        self.udp_port = udp_port
        self.countdown = countdown
        self.sound = sound
        # 입력 소스를 직접 만들어 주는 함수 (song, speed, practice)
        # -> MidiInputSource. None 이면 설정(local/udp)대로 만든다.
        # 건반 없이 돌리는 데모 모드가 이 자리에 자기 소스를 끼운다.
        self.source_factory = source_factory
        # 설정 속도에 한 번 더 곱하는 배율. AR 창은 악보가 눈으로 따라갈
        # 수 있을 만큼 느려야 해서 1 보다 작은 값을 쓴다(piano-ar/main.py).
        self.speed_scale = speed_scale

        self._lock = threading.Lock()
        self._thread = None
        self._stop = threading.Event()
        self._events = collections.deque(maxlen=300)  # (seq, event_dict)
        self._seq = 0

        # UI 표시용 상태 (폴링 응답에 그대로 나감)
        self.state = "idle"   # idle|countdown|playing|done|aborted|error
        self.countdown_left = 0
        self.progress = dict(_EMPTY_PROGRESS)
        self.result = None
        self.error = None
        # 세션이 시작된 시점의 모드. 진행 중에 설정이 바뀌어도 화면 표시가
        # 세션 동작과 어긋나지 않도록 start() 에서 한 번 고정한다.
        self.active_practice = None

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
            self.progress = dict(_EMPTY_PROGRESS)
            self.result = None
            self.error = None
            self.active_practice = self.settings.practice_mode
            self._thread = threading.Thread(
                target=self._run, args=(song,), daemon=True)
            self._thread.start()
            return True, None

    def stop(self):
        self._stop.set()

    @property
    def effective_speed(self):
        """세션에 실제로 넘어가는 속도 (설정값 × 배율)."""
        return self.settings.speed * self.speed_scale

    # ── 내부 동작 ─────────────────────────────────────────────────
    def _push(self, event):
        """세션 이벤트를 저장하고 UI 상태 필드에 반영한다."""
        with self._lock:
            self._seq += 1
            self._events.append((self._seq, event))

            kind = event.get("type")
            if kind == "countdown":
                self.state = "countdown"
                self.countdown_left = event["seconds"]
            elif kind == "start":
                self.state = "playing"
                self.countdown_left = 0
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
                }
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
            }
            payload.update(manager.snapshot(since))
            return payload

        # ── 라우팅 ────────────────────────────────────────────────
        def do_GET(self):
            path, _, query = self.path.partition("?")
            if path == "/":
                self._serve_index()
            elif path == "/api/state":
                since = 0
                for part in query.split("&"):
                    if part.startswith("since="):
                        try:
                            since = int(part[len("since="):])
                        except ValueError:
                            pass
                self._send_json(self._state_payload(since))
            else:
                self.send_error(404)

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


def create_server(manager, host="0.0.0.0", port=8321):
    """세션 매니저에 묶인 HTTP 서버를 만든다(아직 돌리지는 않는다).

    AR 창(piano-ar/main.py)이 같은 매니저를 공유한 채 서버를 별도
    스레드로 띄우기 위한 진입점이다. 바인딩 실패는 OSError 로 나간다.
    """
    return ThreadingHTTPServer((host, port), _make_handler(manager))


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
