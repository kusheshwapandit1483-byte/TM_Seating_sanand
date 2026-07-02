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
echo "== Recent MediaMTX logs =="
journalctl -u "${MEDIAMTX_SERVICE_NAME}.service" -n 80 --no-pager || true

echo
echo "== Recent app logs =="
journalctl -u "${APP_SERVICE_NAME}.service" -n 80 --no-pager || true

echo
echo "== Recent kiosk logs =="
journalctl -u "${BROWSER_SERVICE_NAME}.service" -n 80 --no-pager || true
tail -80 "/home/${KIOSK_USER}/tm-camera-kiosk.log" 2>/dev/null || true
