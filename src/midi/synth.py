"""파형을 직접 만들어 오디오 장치로 내보내는 실시간 소프트 신디사이저.

OS 가 제공하는 MIDI 신디사이저(Windows 의 "Microsoft GS Wavetable Synth" 등)에
의존하지 않는다. 그렇게 만든 이유는 두 가지다.

  1. **macOS 에는 기본 MIDI 출력 포트가 아예 없다.** rtmidi 경로(sound.py 의
     MIDI 백엔드)는 IAC 드라이버나 GarageBand 를 사용자가 따로 켜 줘야만
     소리가 난다. 파형을 직접 만들면 OS 와 무관하게 스피커로 나간다.
  2. **GS Wavetable Synth 는 자체 출력 지연이 크다** (수십 ms). 건반을 눌러도
     소리가 한 박 늦게 나는 체감의 주범이다. 여기서는 블록 크기를 직접
     잡으므로 지연을 예측·통제할 수 있다.

■ 지연의 구성
    타건 → (명령 큐 대기: 최대 1블록) → (오디오 장치 버퍼)
  BLOCK_SIZE=128, SAMPLE_RATE=44100 이면 블록 하나가 약 2.9ms 다.
  장치 버퍼는 **어느 호스트 API 로 여느냐**에 좌우된다 — Windows 의
  기본값인 MME 는 90ms 안팎이고, 같은 스피커라도 WASAPI 로 열면 3ms
  수준이다. 그래서 장치를 기본값에 맡기지 않고 저지연 호스트 API 를
  직접 골라 연다(_device_candidates).

■ 스레드 구조
  note_on/note_off 는 판정 세션 스레드에서 호출되고, 파형 생성은
  sounddevice 가 만든 오디오 콜백 스레드에서 돈다. 둘 사이는 deque
  하나로만 통신한다 — 오디오 콜백에서 락을 잡으면 그동안 오디오가
  끊기므로(글리치), 락 대신 append/popleft 가 원자적인 deque 를 쓴다.

사용 예:
    synth = SoftSynth()      # 장치를 못 열면 RuntimeError
    synth.note_on(60, 100)
    synth.note_off(60)
    synth.close()
"""

import collections
import math

import numpy as np

try:
    import sounddevice as _sd
except Exception:          # noqa: BLE001 — 미설치/드라이버 부재 모두 동일 취급
    _sd = None


SAMPLE_RATE = 44100         # 장치 기본값을 못 읽었을 때만 쓰는 폴백
BLOCK_SIZE = 128            # 작을수록 지연이 줄지만 오디오가 끊길 위험이 는다
MAX_VOICES = 16             # 동시에 울릴 수 있는 음의 수
MASTER_GAIN = 0.25          # 전체 음량 (합산 후 tanh 로 부드럽게 클립)

# 배음 구성 (기본파의 몇 배음인가, 상대 진폭).
# 사인파 하나만 쓰면 오르골 같은 소리가 나서, 배음을 얹어 피아노에
# 가까운 음색을 만든다.
HARMONICS = ((1, 1.0), (2, 0.45), (3, 0.22), (4, 0.11), (5, 0.05))

ATTACK_SEC = 0.004          # 시작 램프. 0 이면 '틱' 하는 클릭 노이즈가 난다
SUSTAIN_TAU = 2.4           # 누르고 있는 동안의 감쇠 시정수(초) — 피아노처럼 서서히
RELEASE_TAU = 0.12          # 건반을 뗀 뒤의 감쇠 시정수(초)
SILENCE_LEVEL = 1e-4        # 이보다 작아진 보이스는 제거한다

_TWO_PI = 2.0 * math.pi

# 우선적으로 여는 호스트 API (소문자 부분 일치).
# Windows 기본값 MME 는 출력 지연이 90ms 안팎이라 건반이 늦게 울린다.
# WASAPI 로 같은 스피커를 열면 20ms 근처까지 떨어진다. macOS(Core Audio)
# 와 Linux(ALSA)는 기본 호스트 API 가 이미 저지연이라 사실상 그대로다.
# WDM-KS 는 장치를 배타적으로 잡아 다른 앱 소리를 막을 수 있어 뺐다.
_PREFERRED_HOSTAPIS = ("wasapi", "core audio", "alsa")


def _device_candidates():
    """열어 볼 출력 장치 번호를 순서대로 돌려준다.

    [저지연 호스트 API 의 기본 출력 장치, None(시스템 기본)] 형태다.
    앞의 것이 실패해도 뒤의 것으로 소리는 나게 한다 — 지연이 큰 소리가
    소리가 아예 없는 것보다는 낫다.
    """
    candidates = []
    try:
        for hostapi in _sd.query_hostapis():
            name = hostapi["name"].lower()
            if not any(key in name for key in _PREFERRED_HOSTAPIS):
                continue
            device = hostapi.get("default_output_device", -1)
            if device is not None and device >= 0:
                candidates.append(device)
    except Exception:          # noqa: BLE001 — 조회 실패 시 기본 장치로
        pass
    candidates.append(None)    # None = PortAudio 가 고르는 기본 장치
    return candidates


def _device_samplerate(device):
    """장치가 요구하는 샘플레이트.

    WASAPI 공유 모드는 윈도우 믹서의 레이트(보통 48000)만 받아들이고
    다른 값은 'Invalid sample rate' 로 거절한다. 44100 을 고집하면
    저지연 경로가 통째로 막히므로 장치 값을 따라간다.
    """
    try:
        info = _sd.query_devices(device, "output")
        rate = int(info["default_samplerate"])
        return rate if rate > 0 else SAMPLE_RATE
    except Exception:          # noqa: BLE001 — 조회 실패 시 폴백
        return SAMPLE_RATE


class _Voice:
    """울리고 있는 음 하나. (오디오 콜백 스레드에서만 수정된다)"""

    __slots__ = ("note", "omega", "phase", "level", "amp",
                 "releasing", "attack_pos", "harmonics")

    def __init__(self, note, velocity, samplerate):
        self.note = note
        freq = 440.0 * (2.0 ** ((note - 69) / 12.0))
        self.omega = _TWO_PI * freq / samplerate    # 샘플당 위상 증가량
        self.phase = 0.0
        self.level = 1.0                            # 엔벨로프 현재값
        # 벨로시티 → 진폭. 제곱 쪽으로 굽혀야 세게 친 느낌이 산다.
        self.amp = (max(velocity, 1) / 127.0) ** 1.4
        self.releasing = False
        self.attack_pos = 0
        # 나이퀴스트를 넘는 배음은 앨리어싱(금속성 잡음)을 만드므로 뺀다.
        nyquist = samplerate / 2.0
        self.harmonics = tuple(
            (mult, gain) for mult, gain in HARMONICS
            if freq * mult < nyquist
        )


class SoftSynth:
    """오디오 장치로 직접 파형을 내보내는 폴리포닉 신디사이저.

    장치를 열지 못하면 __init__ 에서 RuntimeError 를 던진다. 호출자
    (sound.NotePlayer)가 그걸 받아 다른 백엔드로 넘어간다.
    """

    name = "synth"

    def __init__(self, samplerate=None, blocksize=BLOCK_SIZE, device=None):
        if _sd is None:
            raise RuntimeError(
                "sounddevice 가 없어 소프트 신디를 쓸 수 없습니다. "
                "설치: pip install sounddevice")

        self.samplerate = samplerate or SAMPLE_RATE
        self.blocksize = blocksize
        self.device = None
        self._forced_samplerate = samplerate     # None 이면 장치 값을 따른다
        self._voices = []                        # list[_Voice]
        self._commands = collections.deque()     # 세션 스레드 → 오디오 스레드
        self._curve_cache = {}                   # frames → (감쇠커브, 위상스텝)
        self._attack_samples = 1
        self._stream = None

        candidates = [device] if device is not None else _device_candidates()
        errors = []
        for candidate in candidates:
            try:
                self._open(candidate)
                return
            except Exception as exc:   # noqa: BLE001 — 다음 장치로 넘어간다
                errors.append(f"device={candidate}: {exc}")

        raise RuntimeError("오디오 장치를 열 수 없습니다 — "
                           + " / ".join(errors))

    def _open(self, device):
        samplerate = (self._forced_samplerate
                      or _device_samplerate(device))
        stream = _sd.OutputStream(
            samplerate=samplerate,
            blocksize=self.blocksize,
            channels=1,
            dtype="float32",
            latency="low",
            device=device,
            callback=self._callback,
        )
        stream.start()

        self._stream = stream
        self.device = device
        # 샘플레이트가 확정된 뒤에야 시간 상수를 샘플 수로 환산할 수 있다.
        self.samplerate = samplerate
        self._attack_samples = max(int(ATTACK_SEC * samplerate), 1)
        self._curve_cache.clear()

    # ── 공개 API (세션 스레드에서 호출) ──────────────────────────────
    @property
    def latency_ms(self):
        """실제 출력 지연(ms). 장치가 보고한 값 + 명령 큐 대기 1블록."""
        try:
            device_latency = float(self._stream.latency)
        except Exception:          # noqa: BLE001 — 드라이버가 안 알려주면 생략
            device_latency = 0.0
        return (device_latency + self.blocksize / self.samplerate) * 1000.0

    @property
    def device_name(self):
        """소리가 나가는 장치와 호스트 API 이름 (진단용)."""
        try:
            info = _sd.query_devices(self.device, "output")
            api = _sd.query_hostapis(info["hostapi"])["name"]
            return f"{info['name']} [{api}]"
        except Exception:          # noqa: BLE001
            return "기본 출력 장치"

    def note_on(self, note, velocity=100):
        self._commands.append((True, note, velocity))

    def note_off(self, note):
        self._commands.append((False, note, 0))

    def all_notes_off(self):
        self._commands.append((False, None, 0))     # None = 전체

    def close(self):
        stream = getattr(self, "_stream", None)
        if stream is None:
            return
        self._stream = None
        try:
            stream.stop()
            stream.close()
        except Exception:          # noqa: BLE001 — 종료 경로에서는 삼킨다
            pass

    # ── 내부: 명령 반영 (오디오 콜백 스레드) ─────────────────────────
    def _drain_commands(self):
        """큐에 쌓인 note on/off 를 보이스 목록에 반영한다."""
        while True:
            try:
                is_on, note, velocity = self._commands.popleft()
            except IndexError:
                return

            if is_on:
                self._start_voice(note, velocity)
            elif note is None:
                for voice in self._voices:
                    voice.releasing = True
            else:
                for voice in self._voices:
                    if voice.note == note and not voice.releasing:
                        voice.releasing = True

    def _start_voice(self, note, velocity):
        # 같은 음을 다시 누르면(리트리거) 이전 보이스는 서서히 지운다.
        for voice in self._voices:
            if voice.note == note and not voice.releasing:
                voice.releasing = True

        if len(self._voices) >= MAX_VOICES:
            # 가장 조용한 보이스를 희생시킨다 — 가장 덜 들리는 쪽이다.
            quietest = min(self._voices, key=lambda v: v.level * v.amp)
            self._voices.remove(quietest)

        self._voices.append(_Voice(note, velocity, self.samplerate))

    # ── 내부: 파형 생성 (오디오 콜백 스레드) ─────────────────────────
    def _curves(self, frames):
        """블록 길이별 감쇠 커브와 위상 스텝을 만들어 캐시한다.

        매 블록마다 np.exp/np.arange 를 새로 만들면 콜백이 무거워져
        오디오가 끊긴다. 길이는 거의 항상 blocksize 하나로 고정이다.
        """
        cached = self._curve_cache.get(frames)
        if cached is None:
            steps = np.arange(1, frames + 1, dtype=np.float64)
            cached = (
                np.exp(-steps / (SUSTAIN_TAU * self.samplerate)),
                np.exp(-steps / (RELEASE_TAU * self.samplerate)),
                steps,
            )
            self._curve_cache[frames] = cached
        return cached

    def _render(self, frames):
        """활성 보이스를 모두 합성해 float32 모노 버퍼를 만든다."""
        sustain_curve, release_curve, steps = self._curves(frames)
        buffer = np.zeros(frames, dtype=np.float64)
        finished = []

        for voice in self._voices:
            # 1) 엔벨로프: 지수 감쇠 (누른 중 / 뗀 뒤의 시정수가 다르다)
            curve = release_curve if voice.releasing else sustain_curve
            envelope = voice.level * curve
            voice.level = float(envelope[-1])

            # 2) 어택 램프: 시작 몇 ms 를 0 에서 끌어올려 클릭을 없앤다
            attack_samples = self._attack_samples
            if voice.attack_pos < attack_samples:
                ramp_len = min(attack_samples - voice.attack_pos, frames)
                ramp = np.ones(frames)
                ramp[:ramp_len] = (
                    np.arange(voice.attack_pos, voice.attack_pos + ramp_len)
                    / attack_samples
                )
                envelope = envelope * ramp
                voice.attack_pos += ramp_len

            # 3) 배음 합. phase 를 이어 붙여야 블록 경계에서 잡음이 안 난다.
            angles = voice.phase + voice.omega * steps
            signal = np.zeros(frames, dtype=np.float64)
            for mult, gain in voice.harmonics:
                signal += gain * np.sin(mult * angles)
            voice.phase = (voice.phase + voice.omega * frames) % _TWO_PI

            buffer += signal * envelope * voice.amp

            if voice.level < SILENCE_LEVEL:
                finished.append(voice)

        for voice in finished:
            self._voices.remove(voice)

        # 화음에서 진폭이 더해져 넘치는 것을 tanh 로 부드럽게 눌러 준다.
        # (작은 값에서는 거의 그대로 통과하므로 음색을 해치지 않는다)
        np.tanh(buffer * MASTER_GAIN, out=buffer)
        return buffer.astype(np.float32)

    def _callback(self, outdata, frames, time_info, status):
        """sounddevice 오디오 콜백. 예외를 흘리면 스트림이 죽으므로 삼킨다."""
        try:
            self._drain_commands()
            outdata[:, 0] = self._render(frames)
        except Exception:          # noqa: BLE001
            outdata.fill(0)


def is_available():
    """sounddevice 임포트에 성공했는지 (장치 유무까지는 보지 않는다)."""
    return _sd is not None


if __name__ == "__main__":
    import time

    synth = SoftSynth()
    print(f"출력 지연: 약 {synth.latency_ms:.1f}ms")
    for midi_note in (60, 62, 64, 65, 67):
        synth.note_on(midi_note, 100)
        time.sleep(0.35)
        synth.note_off(midi_note)
    time.sleep(0.5)
    synth.close()
