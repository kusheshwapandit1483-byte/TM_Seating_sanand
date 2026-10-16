#!/usr/bin/env python3
"""Apply preview mode and reload the installed 35 GB program, preserving its kiosk method."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from urllib.request import build_opener, ProxyHandler

ROOT = Path(__file__).resolve().parents[1]


def show(name, prop):
    return subprocess.check_output(["systemctl", "show", name, "-p", prop, "--value"], text=True).strip()


def main(mode):
    if os.geteuid() != 0:
        raise RuntimeError("Run this helper through sudo")
    directory = show("tm-camera-monitor.service", "WorkingDirectory")
    if not directory or Path(directory).resolve() != ROOT:
        raise RuntimeError("The installed monitor uses a different folder. No service settings have been changed.")
    settings = ROOT / "preview-settings.json"
    if settings.is_symlink():
        raise RuntimeError("Refusing to overwrite a settings symlink")
    old = settings.read_bytes() if settings.exists() else None
    if old is not None:
        backup = Path("/var/backups/tm-camera") / f"preview-settings-{time.time_ns()}.json"
        backup.parent.mkdir(parents=True, exist_ok=True)
        backup.write_bytes(old)
    settings.write_text(json.dumps({"mode": mode}) + "\n", encoding="utf-8")
    settings.chmod(0o644)
    try:
        subprocess.run(["systemctl", "restart", "tm-camera-monitor.service"], check=True)
        opener = build_opener(ProxyHandler({}))
        for attempt in range(20):
            try:
                with opener.open("http://127.0.0.1:8080/api/recording/status", timeout=2) as response:
                    status = json.load(response)
                if status.get("mode") == "occupied" and status.get("storageLimitGB") == 35:
                    break
            except (OSError, ValueError):
                pass
            time.sleep(1)
        else:
            raise RuntimeError("Recording server health check failed; inspect journalctl -u tm-camera-monitor.service")
        subprocess.run(["systemctl", "is-active", "--quiet", "tm-camera-monitor.service"], check=True)
    except Exception:
        if old is None:
            settings.unlink(missing_ok=True)
        else:
            settings.write_bytes(old)
        raise
    print(f"Recording server verified: occupancy mode, 35 GB. Preview mode: {mode}.")
    # Existing Radxa units can open the direct MediaMTX page, bypassing app.js.
    # Point a managed kiosk service at the dashboard, preserving its user/launcher.
    if show("tm-camera-kiosk.service", "LoadState") == "loaded":
        path = Path("/etc/systemd/system/tm-camera-kiosk.service.d/95-preview-recovery.conf")
        if path.is_symlink():
            raise RuntimeError("Refusing to overwrite a kiosk configuration symlink")
        previous = path.read_bytes() if path.exists() else None
        if previous is not None:
            backup = Path("/var/backups/tm-camera") / f"preview-kiosk-{time.time_ns()}.conf"
            backup.parent.mkdir(parents=True, exist_ok=True)
            backup.write_bytes(previous)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('[Service]\nEnvironment="KIOSK_URL=http://127.0.0.1:8080/?preview_recovery=1"\n', encoding="utf-8")
        try:
            subprocess.run(["systemctl", "daemon-reload"], check=True)
            subprocess.run(["systemctl", "restart", "tm-camera-kiosk.service"], check=True)
        except Exception:
            if previous is None:
                path.unlink()
            else:
                path.write_bytes(previous)
            subprocess.run(["systemctl", "daemon-reload"], check=False)
            raise
        print("Kiosk service refreshed. No reboot is required.")
    else:
        print("Existing Firefox/Openbox startup preserved. Press Ctrl+Shift+R in that browser once.\n"
              "The selected preview mode is now saved for future reboots.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("webrtc", "mjpeg"), required=True)
    args = parser.parse_args()
    try:
        main(args.mode)
    except Exception as exc:
        print(f"Preview update stopped: {exc}", file=sys.stderr)
        sys.exit(1)
