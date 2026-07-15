#!/usr/bin/env bash
# Raspberry Pi OS Bullseye 이하(dhcpcd 기반)용 Keymento 설정 스크립트.
# pi_setup.sh 는 Bookworm(NetworkManager/nmcli) 전용이라 dhcpcd 시스템에서는
# "NetworkManager is not running" 으로 실패한다 → 이 스크립트를 대신 실행.
#
# NetworkManager 대신 hostapd(AP) + dnsmasq(DHCP) + dhcpcd(고정 IP) 조합으로
# 같은 핫스팟(SSID=Keymento, Pi=10.42.0.1/24)을 만든다. pi_sender/key_sender 의
# 브로드캐스트 목적지(10.42.0.255)와 서브넷이 같아 코드 수정은 필요 없다.
#
# ⚠️ 실행 중에는 현재 Wi-Fi 를 건드리지 않는다(SSH 안전). 설정만 심어두고
#    "sudo reboot" 후에 핫스팟이 뜬다. 재부팅하면 wlan0 은 AP 전용이 되어
#    기존 Wi-Fi(인터넷)로는 더 이상 붙지 않는다 — apt/pip/git 은 재부팅 전에!
#
# 되돌리기: /etc/dhcpcd.conf 의 "# Keymento AP" 블록 삭제,
#           sudo systemctl disable hostapd dnsmasq, 재부팅.

set -e

SSID="Keymento"
PSK="keymento1234"          # 시연 전 원하는 비밀번호로 변경 (8자 이상)
AP_IP="10.42.0.1"
DHCP_RANGE="10.42.0.10,10.42.0.100,255.255.255.0,24h"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "=== Keymento pi_midi 설정 (dhcpcd/hostapd) ==="

# 1. 시스템 의존성 — 인터넷 필요, 재부팅 전에 끝내야 한다
sudo apt-get update
sudo apt-get install -y hostapd dnsmasq \
    libasound2-dev libjack-dev python3-venv python3-dev

# 일반 자판 임시 입력(key_sender.py)용 — /dev/input 읽기 권한 (재로그인 후 적용)
sudo usermod -aG input "$USER"

# 2. 파이썬 가상환경 + 의존성 (pi_setup.sh 를 이미 돌렸다면 그대로 통과)
if [ ! -d "$PROJECT_DIR/.venv" ]; then
    python3 -m venv "$PROJECT_DIR/.venv"
fi
"$PROJECT_DIR/.venv/bin/pip" install -r "$SCRIPT_DIR/requirements.txt"

# 3. Wi-Fi 차단 해제 (국가 미설정 이미지에서 soft-block 되어 있는 경우 대비)
sudo rfkill unblock wlan || true

# 4. dhcpcd — wlan0 을 고정 IP 로, wpa_supplicant(클라이언트 접속) 훅 해제
if ! grep -q "^# Keymento AP" /etc/dhcpcd.conf; then
    sudo tee -a /etc/dhcpcd.conf > /dev/null <<EOF

# Keymento AP
interface wlan0
static ip_address=$AP_IP/24
nohook wpa_supplicant
EOF
    echo "dhcpcd.conf 에 고정 IP 추가: $AP_IP"
else
    echo "dhcpcd.conf 는 이미 설정되어 있습니다."
fi

# 5. dnsmasq — 접속 기기(PC/iPad)에 IP 를 나눠주는 DHCP 서버
if [ -f /etc/dnsmasq.conf ] && [ ! -f /etc/dnsmasq.conf.orig ]; then
    sudo mv /etc/dnsmasq.conf /etc/dnsmasq.conf.orig
fi
sudo tee /etc/dnsmasq.conf > /dev/null <<EOF
interface=wlan0
bind-dynamic
dhcp-range=$DHCP_RANGE
EOF

# 6. hostapd — AP 본체 (WPA2)
sudo tee /etc/hostapd/hostapd.conf > /dev/null <<EOF
country_code=KR
interface=wlan0
driver=nl80211
ssid=$SSID
hw_mode=g
channel=6
ieee80211n=1
wmm_enabled=1
auth_algs=1
wpa=2
wpa_key_mgmt=WPA-PSK
wpa_passphrase=$PSK
rsn_pairwise=CCMP
EOF
sudo sed -i 's|^#\?DAEMON_CONF=.*|DAEMON_CONF="/etc/hostapd/hostapd.conf"|' \
    /etc/default/hostapd

# 지금 start 하면 현재 Wi-Fi(SSH)가 끊기므로 enable 만 — 재부팅 시 적용
sudo systemctl unmask hostapd
sudo systemctl enable hostapd dnsmasq

# 7. 부팅 자동 시작 서비스 (MIDI 키보드용 — 없으면 대기만 하므로 무해)
sudo cp "$SCRIPT_DIR/keymento-midi.service" /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable keymento-midi.service

echo ""
echo "=== 설정 완료 — 재부팅하면 핫스팟이 뜹니다 ==="
echo "  sudo reboot"
echo ""
echo "재부팅 후:"
echo "  Wi-Fi '$SSID'(비밀번호 $PSK) 접속 → ssh $USER@$AP_IP"
echo "  자판 입력: $PROJECT_DIR/.venv/bin/python $SCRIPT_DIR/key_sender.py"
