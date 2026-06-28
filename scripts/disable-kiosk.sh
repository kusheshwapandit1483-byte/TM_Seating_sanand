#!/usr/bin/env bash
set -euo pipefail

KIOSK_USER="${KIOSK_USER:-operator}"
SERVICE_NAME="tm-camera-monitor"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run with sudo: sudo bash scripts/disable-kiosk.sh" >&2
  exit 1
fi

rm -f /etc/lightdm/lightdm.conf.d/90-tm-camera-kiosk.conf
rm -f "/home/${KIOSK_USER}/.config/autostart/tm-camera-kiosk.desktop"
rm -f "/home/${KIOSK_USER}/.config/lxsession/LXDE-pi/autostart"
rm -f "/home/${KIOSK_USER}/.config/lxsession/LXDE/autostart"
rm -f "/home/${KIOSK_USER}/.config/labwc/autostart"
systemctl disable "${SERVICE_NAME}.service" >/dev/null 2>&1 || true
systemctl daemon-reload

echo "Kiosk auto-login disabled. Reboot to return to normal login."
echo "Run: sudo reboot"
