#!/usr/bin/env bash
set -euo pipefail

KIOSK_USER="${KIOSK_USER:-operator}"
APP_SERVICE_NAME="${APP_SERVICE_NAME:-tm-camera-monitor}"
MEDIAMTX_SERVICE_NAME="${MEDIAMTX_SERVICE_NAME:-tm-mediamtx}"
BROWSER_SERVICE_NAME="${BROWSER_SERVICE_NAME:-tm-camera-kiosk}"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run with sudo: sudo bash scripts/disable-radxa-kiosk.sh" >&2
  exit 1
fi

systemctl disable "${BROWSER_SERVICE_NAME}.service" >/dev/null 2>&1 || true
systemctl stop "${BROWSER_SERVICE_NAME}.service" >/dev/null 2>&1 || true
systemctl disable "${APP_SERVICE_NAME}.service" >/dev/null 2>&1 || true
systemctl stop "${APP_SERVICE_NAME}.service" >/dev/null 2>&1 || true
systemctl disable "${MEDIAMTX_SERVICE_NAME}.service" >/dev/null 2>&1 || true
systemctl stop "${MEDIAMTX_SERVICE_NAME}.service" >/dev/null 2>&1 || true

rm -f "/etc/systemd/system/${BROWSER_SERVICE_NAME}.service"
rm -f "/etc/systemd/system/${APP_SERVICE_NAME}.service"
rm -f "/etc/systemd/system/${MEDIAMTX_SERVICE_NAME}.service"
rm -f /etc/lightdm/lightdm.conf.d/99-tm-camera-kiosk.conf
rm -f "/home/${KIOSK_USER}/.config/autostart/tm-camera-kiosk.desktop"
rm -f "/home/${KIOSK_USER}/.config/lxsession/LXDE-pi/autostart"
rm -f "/home/${KIOSK_USER}/.config/lxsession/LXDE/autostart"
rm -f "/home/${KIOSK_USER}/.config/labwc/autostart"
rm -f /usr/local/bin/tm-camera-kiosk-start

systemctl daemon-reload

echo "Radxa kiosk mode disabled. Reboot to return to normal login."
echo "Run: sudo reboot"
