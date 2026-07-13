"""곡 선택 모듈: songs/ 폴더를 스캔해 곡 목록과 선택 상태를 관리한다."""

import os
from dataclasses import dataclass

# 프로젝트 루트: src/settings/songs.py → 세 단계 위
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEFAULT_SONGS_DIR = os.path.join(_PROJECT_ROOT, "songs")


@dataclass(frozen=True)
class Song:
    """선택 가능한 곡 하나."""
    id: str          # 파일명(확장자 제외), 안정적인 식별자
    name: str        # 사람이 읽기 좋은 표시 이름
    path: str        # 절대 경로(.mid)


def _to_display_name(stem: str) -> str:
    """'happy-birthday-to-you-c-major' → 'Happy Birthday To You C Major'."""
    return " ".join(word.capitalize() for word in stem.replace("_", "-").split("-") if word)


class SongLibrary:
    """곡 목록과 '어떤 곡이 선택돼 있는가'를 관리한다."""

    def __init__(self, songs_dir: str = DEFAULT_SONGS_DIR):
        self.songs_dir = songs_dir
        self._songs: list[Song] = []
        self._selected_id: str | None = None
        self.refresh()

    def refresh(self) -> list[Song]:
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
        if self._selected_id is not None and self.find(self._selected_id) is None:
            self._selected_id = None
        return songs

    def list(self) -> list[Song]:
        """현재 사용 가능한 곡 목록."""
        return list(self._songs)

    def find(self, song_id: str) -> Song | None:
        """id 로 곡을 찾는다. 없으면 None."""
        return next((s for s in self._songs if s.id == song_id), None)

    def select(self, song_id: str) -> Song:
        """곡을 선택한다. 존재하지 않으면 ValueError."""
        song = self.find(song_id)
        if song is None:
            raise ValueError(f"존재하지 않는 곡 id: {song_id!r}")
        self._selected_id = song_id
        return song

    def restore_selection(self, song_id: str) -> None:
        """저장된 선택을 복원한다. 곡이 존재할 때만 반영 (load 용)."""
        if song_id is not None and self.find(song_id) is not None:
            self._selected_id = song_id

    @property
    def selected(self) -> Song | None:
        """현재 선택된 곡. 없으면 None."""
        if self._selected_id is None:
            return None
        return self.find(self._selected_id)

    @property
    def selected_id(self) -> str | None:            # 영속화(JSON 저장)용
        return self._selected_id
