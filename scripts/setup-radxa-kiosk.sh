#!/usr/bin/env bash
set -euo pipefail

KIOSK_USER="${KIOSK_USER:-operator}"
KIOSK_URL="${KIOSK_URL:-http://127.0.0.1:8080}"
APP_DIR="${APP_DIR:-$(pwd)}"
APP_RUN_USER="${APP_RUN_USER:-${SUDO_USER:-$(id -un)}}"
APP_SERVICE_NAME="${APP_SERVICE_NAME:-tm-camera-monitor}"
MEDIAMTX_SERVICE_NAME="${MEDIAMTX_SERVICE_NAME:-tm-mediamtx}"
BROWSER_SERVICE_NAME="${BROWSER_SERVICE_NAME:-tm-camera-kiosk}"
MEDIAMTX_DIR="${MEDIAMTX_DIR:-}"
MEDIAMTX_CONFIG="${MEDIAMTX_CONFIG:-}"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run with sudo from the project folder:" >&2
  echo "  sudo APP_DIR=$(pwd) bash scripts/setup-radxa-kiosk.sh" >&2
  exit 1
fi

if [[ ! -f "${APP_DIR}/server.py" ]]; then
  echo "server.py not found in APP_DIR=${APP_DIR}" >&2
  exit 1
fi

if [[ -z "${MEDIAMTX_DIR}" ]]; then
  MEDIAMTX_DIR="$(find "${APP_DIR}" -maxdepth 1 -type d -name "mediamtx_v*_linux_arm64v8" | sort -V | tail -n 1)"
fi

if [[ -z "${MEDIAMTX_DIR}" || ! -x "${MEDIAMTX_DIR}/mediamtx" ]]; then
  echo "MediaMTX executable not found." >&2
  echo "Expected: ${APP_DIR}/mediamtx_v1.11.2_linux_arm64v8/mediamtx" >&2
  echo "Or pass MEDIAMTX_DIR=/path/to/mediamtx_folder" >&2
  exit 1
fi

if [[ -z "${MEDIAMTX_CONFIG}" ]]; then
  MEDIAMTX_CONFIG="${MEDIAMTX_DIR}/mediamtx.yml"
fi

if [[ ! -f "${MEDIAMTX_CONFIG}" ]]; then
  echo "MediaMTX config not found: ${MEDIAMTX_CONFIG}" >&2
  exit 1
fi

MEDIAMTX_EXEC="${MEDIAMTX_DIR}/mediamtx"
MEDIAMTX_ARGS=""
if [[ "$(readlink -f "${MEDIAMTX_CONFIG}")" != "$(readlink -f "${MEDIAMTX_DIR}/mediamtx.yml")" ]]; then
  MEDIAMTX_ARGS=" ${MEDIAMTX_CONFIG}"
fi

if ! id "${APP_RUN_USER}" >/dev/null 2>&1; then
  echo "APP_RUN_USER=${APP_RUN_USER} does not exist" >&2
  exit 1
fi

echo "Installing required packages"
apt-get update
apt-get install -y ffmpeg curl unclutter x11-xserver-utils lightdm
CHROMIUM_PACKAGE=""
for package in chromium chromium-browser; do
  candidate="$(apt-cache policy "${package}" 2>/dev/null | awk '/Candidate:/ {print $2; exit}')"
  if [[ -n "${candidate}" && "${candidate}" != "(none)" ]]; then
    CHROMIUM_PACKAGE="${package}"
    break
  fi
done

if [[ -z "${CHROMIUM_PACKAGE}" ]]; then
  echo "Could not find an installable chromium or chromium-browser package" >&2
  exit 1
fi

apt-get install -y "${CHROMIUM_PACKAGE}"

if ! id "${KIOSK_USER}" >/dev/null 2>&1; then
  echo "Creating kiosk user: ${KIOSK_USER}"
  if getent group "${KIOSK_USER}" >/dev/null 2>&1; then
    adduser --disabled-password --gecos "Camera Kiosk" --ingroup "${KIOSK_USER}" "${KIOSK_USER}"
  else
    adduser --disabled-password --gecos "Camera Kiosk" "${KIOSK_USER}"
  fi
fi

passwd -l "${KIOSK_USER}" >/dev/null 2>&1 || true
gpasswd -d "${KIOSK_USER}" sudo >/dev/null 2>&1 || true
for group in video audio render input; do
  if getent group "${group}" >/dev/null 2>&1; then
    usermod -aG "${group}" "${KIOSK_USER}"
  fi
done

cat > "/etc/systemd/system/${MEDIAMTX_SERVICE_NAME}.service" <<SERVICE
[Unit]
Description=TM MediaMTX RTSP Bridge
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${APP_RUN_USER}
WorkingDirectory=${MEDIAMTX_DIR}
ExecStart=${MEDIAMTX_EXEC}${MEDIAMTX_ARGS}
Restart=always
RestartSec=5
LimitNOFILE=1048576

[Install]
WantedBy=multi-user.target
SERVICE

cat > "/etc/systemd/system/${APP_SERVICE_NAME}.service" <<SERVICE
[Unit]
Description=TM Camera Monitor
After=network-online.target ${MEDIAMTX_SERVICE_NAME}.service
Wants=network-online.target ${MEDIAMTX_SERVICE_NAME}.service

[Service]
Type=simple
User=${APP_RUN_USER}
WorkingDirectory=${APP_DIR}
Environment=PYTHONUNBUFFERED=1
Environment=RECORDING_SOURCE=rtsp://127.0.0.1:8554/pramacam
ExecStartPre=/bin/sleep 3
ExecStart=/usr/bin/python3 ${APP_DIR}/server.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
SERVICE

cat > "/etc/systemd/system/${BROWSER_SERVICE_NAME}.service" <<SERVICE
[Unit]
Description=TM Camera Kiosk Browser
After=graphical.target lightdm.service ${MEDIAMTX_SERVICE_NAME}.service ${APP_SERVICE_NAME}.service
Wants=graphical.target lightdm.service ${MEDIAMTX_SERVICE_NAME}.service ${APP_SERVICE_NAME}.service

[Service]
Type=simple
User=${KIOSK_USER}
Environment="KIOSK_URL=${KIOSK_URL}"
Environment=DISPLAY=:0
Environment=XAUTHORITY=/home/${KIOSK_USER}/.Xauthority
ExecStartPre=/bin/sleep 15
ExecStart=/usr/local/bin/tm-camera-kiosk-start
Restart=always
RestartSec=8

[Install]
WantedBy=graphical.target
SERVICE

cat > /usr/local/bin/tm-camera-kiosk-start <<'KIOSK'
#!/usr/bin/env bash
set -euo pipefail

URL="${KIOSK_URL:-http://127.0.0.1:8080}"
LOG_FILE="${HOME}/tm-camera-kiosk.log"

exec >>"${LOG_FILE}" 2>&1
echo "$(date -Is) starting kiosk for ${URL}"

for _ in $(seq 1 90); do
  if curl -fsS "${URL}" >/dev/null 2>&1; then
    break
  fi
  sleep 1
done

xset s off >/dev/null 2>&1 || true
xset -dpms >/dev/null 2>&1 || true
xset s noblank >/dev/null 2>&1 || true
unclutter -idle 0.5 -root >/dev/null 2>&1 &

CHROME=""
for candidate in chromium-browser chromium; do
  if command -v "${candidate}" >/dev/null 2>&1; then
    CHROME="${candidate}"
    break
  fi
done

if [[ -z "${CHROME}" ]]; then
  echo "Chromium not found" >&2
  exit 1
fi

PROFILE_DIR="${HOME}/.config/tm-camera-chromium"
mkdir -p "${PROFILE_DIR}"

exec "${CHROME}" \
  --kiosk "${URL}" \
  --user-data-dir="${PROFILE_DIR}" \
  --password-store=basic \
  --no-first-run \
  --no-default-browser-check \
  --noerrdialogs \
  --disable-infobars \
  --disable-session-crashed-bubble \
  --disable-restore-session-state \
  --disable-component-update \
  --check-for-update-interval=31536000 \
  --autoplay-policy=no-user-gesture-required
KIOSK
chmod 755 /usr/local/bin/tm-camera-kiosk-start

install -d -m 755 "/home/${KIOSK_USER}/.config/autostart"
cat > "/home/${KIOSK_USER}/.config/autostart/tm-camera-kiosk.desktop" <<DESKTOP
[Desktop Entry]
Type=Application
Name=TM Camera Kiosk
Exec=env KIOSK_URL=${KIOSK_URL} /usr/local/bin/tm-camera-kiosk-start
Terminal=false
X-GNOME-Autostart-enabled=true
DESKTOP

for session in LXDE-pi LXDE; do
  install -d -m 755 "/home/${KIOSK_USER}/.config/lxsession/${session}"
  cat > "/home/${KIOSK_USER}/.config/lxsession/${session}/autostart" <<LXDE
@xset s off
@xset -dpms
@xset s noblank
@env KIOSK_URL=${KIOSK_URL} /usr/local/bin/tm-camera-kiosk-start
LXDE
done

install -d -m 755 "/home/${KIOSK_USER}/.config/labwc"
cat > "/home/${KIOSK_USER}/.config/labwc/autostart" <<LABWC
env KIOSK_URL=${KIOSK_URL} /usr/local/bin/tm-camera-kiosk-start &
LABWC

chown -R "${KIOSK_USER}:${KIOSK_USER}" "/home/${KIOSK_USER}/.config"

SESSION_NAME=""
for session in plasma xfce LXDE lightdm-xsession labwc wayfire; do
  if [[ -f "/usr/share/xsessions/${session}.desktop" || -f "/usr/share/wayland-sessions/${session}.desktop" ]]; then
    SESSION_NAME="${session}"
    break
  fi
done

install -d -m 755 /etc/lightdm/lightdm.conf.d
cat > /etc/lightdm/lightdm.conf.d/99-tm-camera-kiosk.conf <<LIGHTDM
[Seat:*]
autologin-user=${KIOSK_USER}
autologin-user-timeout=0
autologin-guest=false
LIGHTDM

if [[ -n "${SESSION_NAME}" ]]; then
  cat >> /etc/lightdm/lightdm.conf.d/99-tm-camera-kiosk.conf <<LIGHTDM
user-session=${SESSION_NAME}
autologin-session=${SESSION_NAME}
LIGHTDM
fi

systemctl daemon-reload
systemctl enable "${MEDIAMTX_SERVICE_NAME}.service"
systemctl enable "${APP_SERVICE_NAME}.service"
systemctl enable "${BROWSER_SERVICE_NAME}.service"
systemctl enable lightdm.service >/dev/null 2>&1 || systemctl enable lightdm >/dev/null 2>&1 || true
systemctl set-default graphical.target >/dev/null 2>&1 || true

echo
echo "Radxa kiosk setup complete."
echo "Kiosk user: ${KIOSK_USER} (locked password, no sudo)"
echo "MediaMTX service: ${MEDIAMTX_SERVICE_NAME}.service"
echo "App service: ${APP_SERVICE_NAME}.service"
echo "Browser service: ${BROWSER_SERVICE_NAME}.service"
echo "Kiosk URL: ${KIOSK_URL}"
echo
echo "Start now:"
echo "  sudo systemctl start ${MEDIAMTX_SERVICE_NAME}.service"
echo "  sudo systemctl start ${APP_SERVICE_NAME}.service"
echo "  sudo systemctl start ${BROWSER_SERVICE_NAME}.service"
echo "  sudo systemctl restart lightdm"
echo "  sudo reboot"
