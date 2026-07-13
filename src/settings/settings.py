"""사용자 설정 모듈: 곡 선택 + 속도(배율) 관리.

GUI 등 다른 모듈이 import 해서 사용한다.
- 곡 선택: songs/ 폴더를 스캔해 사용 가능한 MIDI 곡 목록을 제공.
- 속도 조절: 프리셋(0.5/0.75/1.0/1.25/1.5x) + 임의 배율 모두 지원.
- 영속화: 선택한 곡 id 와 속도를 JSON(data/settings.json)에 저장/복원.

속도(speed)의 의미: 판정 목표 타이밍을 스케일하는 배율.
판정 쪽에서 `목표 간격 / speed` 로 적용하면 0.5x 는 간격이 2배로
늘어나 느리게 연주해도 정확 판정이 나온다. (judgement.run_judgement 참고)
"""

import json
import os
from dataclasses import dataclass

# ── 속도 설정 상수 ────────────────────────────────────────────────
SPEED_PRESETS = [0.5, 0.75, 1.0, 1.25, 1.5]
MIN_SPEED = 0.25
MAX_SPEED = 2.0
DEFAULT_SPEED = 1.0

# ── 입력 소스 상수 ────────────────────────────────────────────────
# local: PC 에 직접 연결된 MIDI 키보드 / udp: 라즈베리파이(hw/pi_midi) 수신
INPUT_SOURCES = ("local", "udp")
DEFAULT_INPUT_SOURCE = "local"

# 프로젝트 루트: src/settings/settings.py → parents[2]
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEFAULT_SONGS_DIR = os.path.join(_PROJECT_ROOT, "songs")
DEFAULT_CONFIG_PATH = os.path.join(_PROJECT_ROOT, "data", "settings.json")


@dataclass(frozen=True)
class Song:
    """선택 가능한 곡 하나."""
    id: str          # 파일명(확장자 제외), 안정적인 식별자
    name: str        # 사람이 읽기 좋은 표시 이름
    path: str        # 절대 경로(.mid)


def _to_display_name(stem: str) -> str:
    """'happy-birthday-to-you-c-major' → 'Happy Birthday To You C Major'."""
    return " ".join(word.capitalize() for word in stem.replace("_", "-").split("-") if word)


class Settings:
    """곡 선택 + 속도 설정을 보관하고 JSON 으로 영속화한다."""

    def __init__(self, songs_dir: str = DEFAULT_SONGS_DIR,
                 config_path: str = DEFAULT_CONFIG_PATH):
        self.songs_dir = songs_dir
        self.config_path = config_path
        self._songs: list[Song] = []
        self._selected_id: str | None = None
        self._speed: float = DEFAULT_SPEED
        self._input_source: str = DEFAULT_INPUT_SOURCE

        self.refresh_songs()
        self.load()

    # ── 곡 선택 ───────────────────────────────────────────────────
    def refresh_songs(self) -> list[Song]:
        """songs_dir 를 다시 스캔해 곡 목록을 갱신한다."""
        songs: list[Song] = []
        if os.path.isdir(self.songs_dir):
            for fname in sorted(os.listdir(self.songs_dir)):
                if fname.lower().endswith((".mid", ".midi")):
                    stem = os.path.splitext(fname)[0]
                    songs.append(Song(
                        id=stem,
                        name=_to_display_name(stem),
                        path=os.path.join(self.songs_dir, fname),
                    ))
        self._songs = songs

        # 선택 곡이 더 이상 존재하지 않으면 선택 해제
        if self._selected_id is not None and not self._find(self._selected_id):
            self._selected_id = None
        return songs

    def list_songs(self) -> list[Song]:
        """현재 사용 가능한 곡 목록."""
        return list(self._songs)

    def _find(self, song_id: str) -> Song | None:
        return next((s for s in self._songs if s.id == song_id), None)

    def select_song(self, song_id: str) -> Song:
        """곡을 선택한다. 존재하지 않으면 ValueError."""
        song = self._find(song_id)
        if song is None:
            raise ValueError(f"존재하지 않는 곡 id: {song_id!r}")
        self._selected_id = song_id
        return song

    def get_selected_song(self) -> Song | None:
        """현재 선택된 곡. 없으면 None."""
        if self._selected_id is None:
            return None
        return self._find(self._selected_id)

    # ── 속도 ─────────────────────────────────────────────────────
    @property
    def speed(self) -> float:
        return self._speed

    def set_speed(self, value: float) -> float:
        """임의 배율 설정. MIN_SPEED~MAX_SPEED 범위로 clamp 한 값을 반환."""
        try:
            value = float(value)
        except (TypeError, ValueError):
            raise ValueError(f"속도는 숫자여야 합니다: {value!r}")
        if value <= 0:
            raise ValueError(f"속도는 0보다 커야 합니다: {value}")
        self._speed = max(MIN_SPEED, min(MAX_SPEED, value))
        return self._speed

    def set_speed_preset(self, index: int) -> float:
        """SPEED_PRESETS 인덱스로 속도를 설정한다."""
        if not 0 <= index < len(SPEED_PRESETS):
            raise IndexError(f"프리셋 인덱스 범위 초과: {index}")
        self._speed = SPEED_PRESETS[index]
        return self._speed

    # ── 입력 소스 ─────────────────────────────────────────────────
    @property
    def input_source(self) -> str:
        return self._input_source

    def set_input_source(self, name: str) -> str:
        """입력 소스를 설정한다. INPUT_SOURCES 외의 값이면 ValueError."""
        if name not in INPUT_SOURCES:
            raise ValueError(
                f"알 수 없는 입력 소스: {name!r} (가능: {INPUT_SOURCES})")
        self._input_source = name
        return self._input_source

    # ── 영속화 ───────────────────────────────────────────────────
    def to_dict(self) -> dict:
        return {"selected_song": self._selected_id, "speed": self._speed,
                "input_source": self._input_source}

    def save(self) -> None:
        """현재 설정을 config_path(JSON)에 저장한다."""
        os.makedirs(os.path.dirname(self.config_path), exist_ok=True)
        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, ensure_ascii=False, indent=2)

    def load(self) -> None:
        """config_path 가 있으면 설정을 복원한다. 손상/부재 시 기본값 유지."""
        if not os.path.isfile(self.config_path):
            return
        try:
            with open(self.config_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            return

        song_id = data.get("selected_song")
        if song_id is not None and self._find(song_id):
            self._selected_id = song_id

        speed = data.get("speed")
        if speed is not None:
            try:
                self.set_speed(speed)
            except ValueError:
                pass

        input_source = data.get("input_source")
        if input_source in INPUT_SOURCES:
            self._input_source = input_source
