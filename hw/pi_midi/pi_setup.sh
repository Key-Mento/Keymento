#!/usr/bin/env bash
# Raspberry Pi setup for the pi_midi module — 무인 운용(전원만 꽂으면 동작) 설정
#
# 한 번 실행하면 이후에는 전원 연결만으로:
#   1. Pi 가 자체 Wi-Fi 핫스팟("Keymento")을 띄우고           ← 학교 Wi-Fi 불필요
#   2. keymento-midi.service 가 pi_sender.py 를 자동 시작하며
#   3. USB MIDI 키보드가 꽂히는 대로 UDP 브로드캐스트 전송을 시작한다.
#
# PC 쪽은 Wi-Fi "Keymento" 에 접속한 뒤 세션을 udp 모드로 실행하면 된다.
# (Pi = 10.42.0.1, 전송은 10.42.0.255 브로드캐스트라 PC IP 설정 불필요)
#
# MIDI 키보드가 없을 때: 일반 USB 자판을 꽂고 key_sender.py 를 수동 실행
# (서비스는 MIDI 장치만 기다리므로 자판에는 반응하지 않는다. key_sender.py 참고)
#
# 전제: Raspberry Pi OS Bookworm 이상(NetworkManager 기본), 프로젝트가
#       /home/pi/Keymento 에 클론되어 있음 (다르면 keymento-midi.service 수정).

set -e

SSID="Keymento"
PSK="keymento1234"          # 시연 전 원하는 비밀번호로 변경 (8자 이상)
AP_CON_NAME="Keymento-AP"
AP_IP="10.42.0.1/24"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "=== Keymento pi_midi 설정 ==="

# 1. 시스템 의존성 (python-rtmidi / evdev 빌드용)
sudo apt-get update
sudo apt-get install -y libasound2-dev libjack-dev python3-venv python3-dev

# 일반 자판 임시 입력(key_sender.py)용 — /dev/input 읽기 권한 (재로그인 후 적용)
sudo usermod -aG input "$USER"

# 2. 파이썬 가상환경 + 의존성
if [ ! -d "$PROJECT_DIR/.venv" ]; then
    python3 -m venv "$PROJECT_DIR/.venv"
fi
"$PROJECT_DIR/.venv/bin/pip" install -r "$SCRIPT_DIR/requirements.txt"

# 3. Wi-Fi 핫스팟 (NetworkManager) — 부팅 시 자동으로 뜬다(autoconnect)
if ! nmcli -t -f NAME con show | grep -qx "$AP_CON_NAME"; then
    sudo nmcli con add type wifi ifname wlan0 con-name "$AP_CON_NAME" \
        autoconnect yes ssid "$SSID" mode ap \
        wifi.band bg \
        wifi-sec.key-mgmt wpa-psk wifi-sec.psk "$PSK" \
        ipv4.method shared ipv4.addresses "$AP_IP"
    echo "핫스팟 프로필 생성: SSID=$SSID"
else
    echo "핫스팟 프로필이 이미 있습니다: $AP_CON_NAME"
fi
sudo nmcli con up "$AP_CON_NAME" || echo "(핫스팟은 재부팅 후 자동 활성화됩니다)"

# 4. 부팅 자동 시작 서비스
sudo cp "$SCRIPT_DIR/keymento-midi.service" /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now keymento-midi.service

echo ""
echo "=== 설정 완료 ==="
echo "상태 확인 : systemctl status keymento-midi"
echo "로그 보기 : journalctl -u keymento-midi -f"
echo "PC 사용법 : Wi-Fi '$SSID' 접속(비밀번호 $PSK) 후 세션을 udp 모드로 실행"
