# Keymento 웹 UI

곡 선택 · 연주 속도 · 입력 소스를 브라우저에서 제어하고, 판정 진행 상황과
최종 점수를 실시간으로 보여주는 웹 UI입니다. 표준 라이브러리만 사용하므로
추가 패키지 설치가 필요 없습니다.

## 실행

```powershell
cd C:\project\Keymento
venv\Scripts\activate
python src\webui\server.py
```

- PC 브라우저: <http://localhost:8321/>
- 태블릿/폰(같은 Wi-Fi): `http://<PC-IP>:8321/`
- 첫 실행 시 Windows 방화벽 허용 창이 뜨면 **허용**을 눌러야 다른 기기에서 접속됩니다.

## 화면 구성

| 영역 | 기능 |
|------|------|
| 곡 선택 | `songs/` 폴더의 MIDI 곡 목록에서 선택 |
| 속도 | 0.5 / 0.75 / 1.0 / 1.25 / 1.5x 프리셋 |
| 입력 | **로컬 MIDI**(기본, PC에 키보드 직결) / **라즈베리파이(UDP)** 모드 |
| 진행 상황 | 카운트다운 → 다음 목표 음, 진행 바, 노트별 판정 피드 |
| 최종 결과 | 종합 점수, 음정/박자 정확도, Perfect~Miss 개수 |

선택한 곡/속도/입력은 `data/settings.json` 에 저장되어 다음 실행 때 복원됩니다.
연주 중에는 설정 변경이 잠기고, 중지 버튼으로 세션을 끊을 수 있습니다.

## 입력 모드

- **로컬 MIDI (기본)** — PC USB에 연결된 MIDI 키보드를 그대로 사용합니다.
- **라즈베리파이 (UDP)** — Pi에서 `hw/pi_midi/pi_sender.py` 를 실행해 두면
  Pi에 연결된 키보드 입력을 받아 판정합니다. 박자 판정은 Pi가 찍은 타건
  시각의 간격을 사용하므로 Wi-Fi 지연·지터가 판정에 섞이지 않습니다.

  ```bash
  # 라즈베리파이에서
  python3 hw/pi_midi/pi_sender.py --pc-ip <PC-IP>
  ```

## 서버 옵션

| 옵션 | 기본값 | 설명 |
|------|--------|------|
| `--port` | 8321 | HTTP 포트 |
| `--midi-port` | 0 | 로컬 입력용 rtmidi 포트 번호 |
| `--udp-port` | 9998 | 라즈베리파이 수신 UDP 포트 |
| `--countdown` | 5 | 연주 시작 전 카운트다운(초) |
| `--no-sound` | off | 건반 소리 에코 끄기 |

## API (다른 모듈에서 연동 시)

| 메서드 | 경로 | 설명 |
|--------|------|------|
| GET | `/api/state?since=<seq>` | 전체 상태 + seq 이후 세션 이벤트 |
| POST | `/api/select` | `{"song_id": "..."}` |
| POST | `/api/speed` | `{"speed": 1.25}` |
| POST | `/api/input` | `{"source": "local" \| "udp"}` |
| POST | `/api/start` / `/api/stop` | 세션 시작/중단 |
