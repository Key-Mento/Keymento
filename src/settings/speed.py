"""속도(배율) 관리 모듈: 프리셋과 범위 검증.

속도(speed)의 의미: 판정 목표 타이밍을 스케일하는 배율.
판정 쪽에서 `목표 간격 / speed` 로 적용하면 0.5x 는 간격이 2배로
늘어나 느리게 연주해도 정확 판정이 나온다. (perform/session.py 참고)
"""

SPEED_PRESETS = [0.5, 0.75, 1.0, 1.25, 1.5]
MIN_SPEED = 0.25
MAX_SPEED = 2.0
DEFAULT_SPEED = 1.0


class SpeedControl:
    """현재 속도 값을 보관하고, 설정 시 검증·clamp 한다."""

    def __init__(self, value: float = DEFAULT_SPEED):
        self._value = value

    @property
    def value(self) -> float:
        return self._value

    def set(self, value: float) -> float:
        """임의 배율 설정. MIN_SPEED~MAX_SPEED 범위로 clamp 한 값을 반환."""
        try:
            value = float(value)
        except (TypeError, ValueError):
            raise ValueError(f"속도는 숫자여야 합니다: {value!r}")
        if value <= 0:
            raise ValueError(f"속도는 0보다 커야 합니다: {value}")
        self._value = max(MIN_SPEED, min(MAX_SPEED, value))
        return self._value

    def set_preset(self, index: int) -> float:
        """SPEED_PRESETS 인덱스로 속도를 설정한다."""
        if not 0 <= index < len(SPEED_PRESETS):
            raise IndexError(f"프리셋 인덱스 범위 초과: {index}")
        self._value = SPEED_PRESETS[index]
        return self._value
