"""run.bat 이 앱을 띄우기 전에 부르는 환경 점검·설치.

처음 실행할 때만 실제로 일한다.
  1. 실행에 꼭 필요한 패키지(PACKAGES)가 이 파이썬에서 제대로 불러와지는지 본다.
  2. 빠진 것이 있으면 먼저 requirements.txt 를 그대로 설치한다 — 팀이 쓰는
     버전 그대로다. requirements.txt 는 버전을 정확히 고정해 둬서(예: numpy
     2.4.4 는 파이썬 3.11 이상, python-rtmidi 는 3.12 이하만 설치 파일이 있음)
     파이썬 3.11~3.12 가 아니면 이 단계가 실패할 수 있다. pip 는 하나라도
     실패하면 아무것도 안 깔고 멈추므로, 그때는 실행에 필요한 패키지만 하나씩
     설치한다 — requirements.txt 의 고정 버전을 먼저, 안 되면 호환 버전(FALLBACK)
     으로. requirements.txt 파일 자체는 건드리지 않는다.
  3. 다 되면 가상환경 안에 표식(.keymento-ready)을 남긴다. 다음부터는 표식만
     보고 바로 넘어간다 — requirements.txt 나 파이썬이 바뀌면 다시 확인한다.

종료 코드
  0  준비됨 (sounddevice 처럼 없어도 되는 패키지가 빠졌으면 경고만 한다)
  1  실패 — 이유와 해결 방법을 출력한다
  3  이 파이썬 버전으로는 python-rtmidi 를 설치할 수 없다(3.13 이상은 윈도우용
     설치 파일이 없다). run.bat 이 3.12 이하 파이썬으로 가상환경을 다시 만든다.

사용: python tools/ensure_env.py [--force]   (--force: 표식을 무시하고 다시 확인)
"""

import hashlib
import importlib
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REQUIREMENTS = os.path.join(ROOT, "requirements.txt")
STAMP = os.path.join(sys.prefix, ".keymento-ready")
MIN_PYTHON = (3, 9)
STAMP_VERSION = "1"             # 점검 내용이 바뀌면 올려서 모두 다시 확인하게 한다

EXIT_OK, EXIT_FAIL, EXIT_NEED_OLDER_PYTHON = 0, 1, 3


def _check_cv2():
    import cv2
    if not hasattr(getattr(cv2, "aruco", None), "ArucoDetector"):
        raise ImportError(f"OpenCV {cv2.__version__} 에는 ArUco 검출기가 없습니다(4.7 이상 필요)")


def _check_rtmidi():
    import rtmidi
    rtmidi.MidiIn                                   # noqa: B018 — 확장 모듈이 진짜 로드됐는지


# requirements.txt 의 고정 버전을 이 파이썬에 설치할 수 없을 때 쓰는 호환 버전.
# OpenCV 5 는 API 가 바뀌어 아직 확인하지 않았으므로 4.x 안에서 고른다.
FALLBACK = {
    "opencv-python": "opencv-python>=4.7,<5",     # ArUco 검출기는 4.7 부터
    "numpy": "numpy>=1.21",
    "mido": "mido>=1.2.10",
    "python-rtmidi": "python-rtmidi>=1.4",
    "sounddevice": "sounddevice>=0.4",
}

# (pip 이름, 확인 함수, 꼭 필요한가, 무엇에 쓰나)
PACKAGES = [
    ("opencv-python", _check_cv2, True, "카메라·ArUco 보정·AR 화면"),
    ("numpy", lambda: importlib.import_module("numpy"), True, "영상·소리 계산"),
    ("mido", lambda: importlib.import_module("mido"), True, "MIDI 곡 읽기"),
    ("python-rtmidi", _check_rtmidi, True, "MIDI 건반 입력"),
    ("sounddevice", lambda: importlib.import_module("sounddevice"), False,
     "지연이 작은 내장 소리(없으면 OS MIDI 소리로 대신)"),
]


def say(text=""):
    print(text, flush=True)


def stamp_text():
    with open(REQUIREMENTS, "rb") as f:
        digest = hashlib.sha256(f.read()).hexdigest()
    return f"{STAMP_VERSION}|{sys.version}|{sys.executable}|{digest}"


def stamp_is_fresh():
    try:
        with open(STAMP, encoding="utf-8") as f:
            return f.read() == stamp_text()
    except OSError:
        return False


def write_stamp():
    try:
        with open(STAMP, "w", encoding="utf-8") as f:
            f.write(stamp_text())
    except OSError:
        pass                    # 시스템 파이썬처럼 쓸 수 없는 곳이면 매번 확인할 뿐이다


def requirement_specs():
    """requirements.txt → {pip 이름(소문자): 설치 문자열}."""
    specs = {}
    with open(REQUIREMENTS, encoding="utf-8") as f:
        for line in f:
            line = line.split("#", 1)[0].strip()
            match = re.match(r"[A-Za-z0-9_.\-]+", line)
            if match:
                specs[match.group(0).lower()] = line
    return specs


def missing_packages():
    """불러오지 못하는 패키지 [(pip 이름, 꼭 필요한가, 쓰임, 오류)]."""
    importlib.invalidate_caches()
    missing = []
    for name, check, required, purpose in PACKAGES:
        try:
            check()
        except Exception as exc:  # noqa: BLE001 — 없음·DLL 오류·구버전 모두 '없음'
            missing.append((name, required, purpose, exc))
    return missing


def pip(*args):
    cmd = [sys.executable, "-m", "pip", "install", "--disable-pip-version-check",
           "--prefer-binary", *args]
    return subprocess.call(cmd) == 0


def ensure_pip():
    if subprocess.call([sys.executable, "-m", "pip", "--version"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) == 0:
        return True
    say("[*] pip 이 없어 설치합니다 (ensurepip)…")
    return subprocess.call([sys.executable, "-m", "ensurepip", "--upgrade"]) == 0


def main(argv):
    force = "--force" in argv
    say_prefix = f"[*] Python {sys.version.split()[0]} ({sys.executable})"

    if sys.version_info < MIN_PYTHON:
        say(f"[!] 파이썬 {'.'.join(map(str, MIN_PYTHON))} 이상이 필요합니다. "
            f"지금: {sys.version.split()[0]}")
        say("    https://www.python.org/downloads/ 에서 3.12 를 설치한 뒤 다시 실행하세요.")
        return EXIT_FAIL
    if not force and stamp_is_fresh():
        return EXIT_OK

    missing = missing_packages()
    if not missing:
        write_stamp()
        if force:
            say(f"{say_prefix}: 필요한 패키지가 모두 있습니다.")
        return EXIT_OK

    say(say_prefix)
    say("[*] 처음 실행이라 필요한 패키지를 설치합니다 (인터넷 연결 필요, 몇 분 걸릴 수 있음):")
    for name, required, purpose, _ in missing:
        say(f"      - {name}  ({purpose}){'' if required else '  [선택]'}")
    say()
    if not ensure_pip():
        say("[!] pip 을 준비하지 못했습니다. 파이썬을 다시 설치해 주세요.")
        return EXIT_FAIL

    say("[*] requirements.txt 를 그대로 설치합니다…")
    if not pip("-r", REQUIREMENTS):
        version = f"{sys.version_info.major}.{sys.version_info.minor}"
        say()
        say(f"[*] requirements.txt 를 파이썬 {version} 에 그대로 설치하지 못했습니다 — 고정된")
        say("    버전이 이 파이썬을 지원하지 않을 수 있습니다. 실행에 필요한 패키지만")
        say("    하나씩 설치합니다 (고정 버전 먼저, 안 되면 호환 버전).")
        specs = requirement_specs()
        for name, *_ in missing_packages():
            pinned = specs.get(name.lower())
            if pinned and pip(pinned):
                continue
            pip(FALLBACK.get(name, name))

    missing = missing_packages()
    required = [m for m in missing if m[1]]
    optional = [m for m in missing if not m[1]]
    if not required:
        for name, _, purpose, exc in optional:
            say(f"[!] {name} 없이 진행합니다 — {purpose}. ({exc})")
        write_stamp()
        say("[*] 패키지 준비 완료.")
        return EXIT_OK

    say()
    say("[!] 다음 패키지를 설치하지 못했습니다:")
    for name, _, purpose, exc in required:
        say(f"      - {name}  ({purpose}) — {exc}")
    names = {m[0] for m in required}
    if "python-rtmidi" in names and sys.version_info >= (3, 13):
        say()
        say(f"    python-rtmidi 는 파이썬 {sys.version_info.major}.{sys.version_info.minor}"
            " 용 윈도우 설치 파일이 없습니다 (3.12 이하만 있음).")
        say("    run.bat 이 3.12 이하 파이썬을 찾아 가상환경을 다시 만듭니다.")
        say("    3.12 가 없다면 https://www.python.org/downloads/ 에서 3.12 를 설치한 뒤")
        say("    run.bat 을 다시 실행하세요.")
        return EXIT_NEED_OLDER_PYTHON
    say()
    say("    인터넷 연결(회사·학교 망이면 프록시)을 확인하고 run.bat 을 다시 실행하세요.")
    say(f"    직접 설치: \"{sys.executable}\" -m pip install -r requirements.txt")
    return EXIT_FAIL


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
