#!/usr/bin/env bash
set -euo pipefail

KIOSK_USER="${KIOSK_USER:-operator}"
SERVICE_NAME="tm-camera-monitor"
BROWSER_SERVICE_NAME="tm-camera-kiosk"
KIOSK_URL="${KIOSK_URL:-http://127.0.0.1:8080}"

echo "== User =="
id "${KIOSK_USER}" || true

echo
echo "== LightDM config =="
cat /etc/lightdm/lightdm.conf.d/99-tm-camera-kiosk.conf 2>/dev/null || echo "missing /etc/lightdm/lightdm.conf.d/99-tm-camera-kiosk.conf"
echo
echo "== All LightDM autologin settings =="
grep -R "autologin-user\|autologin-session\|user-session" /etc/lightdm/lightdm.conf /etc/lightdm/lightdm.conf.d 2>/dev/null || true

echo
echo "== Service status =="
systemctl --no-pager --full status "${SERVICE_NAME}.service" || true

echo
echo "== Browser service status =="
systemctl --no-pager --full status "${BROWSER_SERVICE_NAME}.service" || true

echo
echo "== LightDM status =="
systemctl --no-pager --full status lightdm.service || systemctl --no-pager --full status lightdm || true

echo
echo "== Local webpage =="
curl -I --max-time 5 "${KIOSK_URL}" || true

echo
echo "== Kiosk files =="
ls -la "/home/${KIOSK_USER}/.config/autostart" 2>/dev/null || true
ls -la "/home/${KIOSK_USER}/.config/lxsession/LXDE-pi" 2>/dev/null || true
ls -la "/home/${KIOSK_USER}/.config/lxsession/LXDE" 2>/dev/null || true
ls -la "/home/${KIOSK_USER}/.config/labwc" 2>/dev/null || true

echo
echo "== Kiosk log =="
tail -80 "/home/${KIOSK_USER}/tm-camera-kiosk.log" 2>/dev/null || echo "no kiosk log yet"

echo
echo "== Recent app logs =="
journalctl -u "${SERVICE_NAME}.service" -n 80 --no-pager || true

echo
echo "== Recent kiosk browser logs =="
journalctl -u "${BROWSER_SERVICE_NAME}.service" -n 80 --no-pager || true
