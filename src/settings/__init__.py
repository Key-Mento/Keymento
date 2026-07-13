"""사용자 설정 패키지: 곡 선택 + 속도 + 입력 소스.

역할 분담:
  - 곡 선택 로직   → songs.py (SongLibrary)
  - 속도 검증 로직 → speed.py (SpeedControl)
  - 이 파일        → 둘을 조립한 Settings 파사드. 입력 소스 선택과
                     JSON(data/settings.json) 저장/복원을 담당.

GUI 등 호출자는 Settings 하나만 알면 된다. 곡/속도 로직만 필요하면
SongLibrary, SpeedControl 을 단독으로 써도 된다.
"""

import json
import os

from .songs import Song, SongLibrary, DEFAULT_SONGS_DIR
from .speed import (
    SpeedControl,
    SPEED_PRESETS,
    MIN_SPEED,
    MAX_SPEED,
    DEFAULT_SPEED,
)

# ── 입력 소스 상수 ────────────────────────────────────────────────
# local: PC 에 직접 연결된 MIDI 키보드 / udp: 라즈베리파이(hw/pi_midi) 수신
INPUT_SOURCES = ("local", "udp")
DEFAULT_INPUT_SOURCE = "local"

# 프로젝트 루트: src/settings/__init__.py → 세 단계 위
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEFAULT_CONFIG_PATH = os.path.join(_PROJECT_ROOT, "data", "settings.json")

__all__ = [
    "Settings",
    "Song",
    "SongLibrary",
    "SpeedControl",
    "SPEED_PRESETS",
    "MIN_SPEED",
    "MAX_SPEED",
    "DEFAULT_SPEED",
    "INPUT_SOURCES",
    "DEFAULT_INPUT_SOURCE",
]


class Settings:
    """곡 선택 + 속도 + 입력 소스를 보관하고 JSON 으로 영속화한다."""

    def __init__(self, songs_dir: str = DEFAULT_SONGS_DIR,
                 config_path: str = DEFAULT_CONFIG_PATH):
        self.config_path = config_path
        self.songs = SongLibrary(songs_dir)
        self._speed = SpeedControl()
        self._input_source: str = DEFAULT_INPUT_SOURCE
        self.load()

    @property
    def songs_dir(self) -> str:
        return self.songs.songs_dir

    # ── 곡 선택 (SongLibrary 위임) ────────────────────────────────
    def refresh_songs(self) -> list[Song]:
        return self.songs.refresh()

    def list_songs(self) -> list[Song]:
        return self.songs.list()

    def select_song(self, song_id: str) -> Song:
        return self.songs.select(song_id)

    def get_selected_song(self) -> Song | None:
        return self.songs.selected

    # ── 속도 (SpeedControl 위임) ──────────────────────────────────
    @property
    def speed(self) -> float:
        return self._speed.value

    def set_speed(self, value: float) -> float:
        return self._speed.set(value)

    def set_speed_preset(self, index: int) -> float:
        return self._speed.set_preset(index)

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
        return {"selected_song": self.songs.selected_id,
                "speed": self._speed.value,
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

        self.songs.restore_selection(data.get("selected_song"))

        speed = data.get("speed")
        if speed is not None:
            try:
                self._speed.set(speed)
            except ValueError:
                pass

        input_source = data.get("input_source")
        if input_source in INPUT_SOURCES:
            self._input_source = input_source
