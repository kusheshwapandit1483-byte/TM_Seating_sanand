#!/usr/bin/env bash
set -euo pipefail

KIOSK_USER="${KIOSK_USER:-operator}"
KIOSK_URL="${KIOSK_URL:-http://127.0.0.1:8080}"
APP_SERVICE_NAME="${APP_SERVICE_NAME:-tm-camera-monitor}"
MEDIAMTX_SERVICE_NAME="${MEDIAMTX_SERVICE_NAME:-tm-mediamtx}"
BROWSER_SERVICE_NAME="${BROWSER_SERVICE_NAME:-tm-camera-kiosk}"

echo "== User =="
id "${KIOSK_USER}" || true
groups "${KIOSK_USER}" || true

echo
echo "== LightDM config =="
cat /etc/lightdm/lightdm.conf.d/99-tm-camera-kiosk.conf 2>/dev/null || echo "missing /etc/lightdm/lightdm.conf.d/99-tm-camera-kiosk.conf"

echo
echo "== Kiosk lock and power config =="
cat "/home/${KIOSK_USER}/.config/kscreenlockerrc" 2>/dev/null || echo "missing kscreenlockerrc"
cat "/home/${KIOSK_USER}/.config/powerdevilrc" 2>/dev/null || echo "missing powerdevilrc"
cat "/home/${KIOSK_USER}/.config/xfce4/xfconf/xfce-perchannel-xml/xfce4-screensaver.xml" 2>/dev/null || true
cat "/home/${KIOSK_USER}/.config/xfce4/xfconf/xfce-perchannel-xml/xfce4-power-manager.xml" 2>/dev/null || true
ls -l "/home/${KIOSK_USER}/.config/autostart"/*locker*.desktop "/home/${KIOSK_USER}/.config/autostart"/*screensaver*.desktop 2>/dev/null || true

echo
echo "== Display manager logs =="
journalctl -u lightdm.service -n 60 --no-pager 2>/dev/null || true
journalctl -u sddm.service -n 60 --no-pager 2>/dev/null || true

echo
echo "== Service status: MediaMTX =="
systemctl --no-pager --full status "${MEDIAMTX_SERVICE_NAME}.service" || true

echo
echo "== Service status: App =="
systemctl --no-pager --full status "${APP_SERVICE_NAME}.service" || true

echo
echo "== Service status: Kiosk browser =="
systemctl --no-pager --full status "${BROWSER_SERVICE_NAME}.service" || true

echo
echo "== Local URLs =="
curl -I --max-time 5 "${KIOSK_URL}" || true
curl -I --max-time 5 "http://127.0.0.1:8889/pramacam" || true

echo
echo "== Service unit files =="
ls -l "/etc/systemd/system/${MEDIAMTX_SERVICE_NAME}.service" "/etc/systemd/system/${APP_SERVICE_NAME}.service" "/etc/systemd/system/${BROWSER_SERVICE_NAME}.service" 2>/dev/null || true
systemctl cat "${MEDIAMTX_SERVICE_NAME}.service" 2>/dev/null || true
systemctl cat "${APP_SERVICE_NAME}.service" 2>/dev/null || true
systemctl cat "${BROWSER_SERVICE_NAME}.service" 2>/dev/null || true

echo
echo "== MediaMTX folders in project =="
APP_DIR="${APP_DIR:-$(pwd)}"
find "${APP_DIR}" -maxdepth 1 -type d -name "mediamtx_v*_linux_arm64v8" -print 2>/dev/null || true

echo
echo "== MediaMTX and app processes =="
pgrep -a mediamtx || true
pgrep -a -f "server.py|tm-camera-chromium|chromium.*8080" || true

echo
echo "== Listening ports =="
if command -v ss >/dev/null 2>&1; then
  ss -lntup 2>/dev/null | grep -E '(:8554|:8888|:8889|:8000|:8001|:8080)' || true
else
  netstat -lntup 2>/dev/null | grep -E '(:8554|:8888|:8889|:8000|:8001|:8080)' || true
fi

echo
echo "== Recent MediaMTX logs =="
journalctl -u "${MEDIAMTX_SERVICE_NAME}.service" -n 80 --no-pager || true

echo
echo "== Recent app logs =="
journalctl -u "${APP_SERVICE_NAME}.service" -n 80 --no-pager || true

echo
echo "== Recent kiosk logs =="
journalctl -u "${BROWSER_SERVICE_NAME}.service" -n 80 --no-pager || true
tail -80 "/home/${KIOSK_USER}/tm-camera-kiosk.log" 2>/dev/null || true
