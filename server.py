#!/usr/bin/env python3
import json
import os
import signal
import subprocess
import threading
import time
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

ROOT = Path(__file__).resolve().parent
RECORDINGS_DIR = Path(os.environ.get("RECORDINGS_DIR", ROOT / "recordings")).resolve()
SETTINGS_FILE = Path(os.environ.get("SETTINGS_FILE", ROOT / "settings.json")).resolve()
RECORDING_SOURCE = os.environ.get("RECORDING_SOURCE", "rtsp://127.0.0.1:8554/pramacam")
SEGMENT_SECONDS = int(os.environ.get("SEGMENT_SECONDS", "1800"))
DEFAULT_RETENTION_DAYS = int(os.environ.get("RETENTION_DAYS", "2"))
MAX_RETENTION_DAYS = 2
MIN_RETENTION_DAYS = 1
FFMPEG_BIN = os.environ.get("FFMPEG_BIN", "ffmpeg")
PORT = int(os.environ.get("PORT", "8080"))

recorder_process = None
recorder_started_at = None
recorder_log = None
recorder_lock = threading.Lock()
shutdown_event = threading.Event()


def clamp_retention_days(value):
    try:
        days = int(value)
    except (TypeError, ValueError):
        days = DEFAULT_RETENTION_DAYS
    return max(MIN_RETENTION_DAYS, min(MAX_RETENTION_DAYS, days))


def load_settings():
    settings = {"retentionDays": clamp_retention_days(DEFAULT_RETENTION_DAYS)}
    if SETTINGS_FILE.exists():
        try:
            saved = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
            settings["retentionDays"] = clamp_retention_days(saved.get("retentionDays"))
        except (OSError, json.JSONDecodeError):
            pass
    return settings


def save_settings(settings):
    safe_settings = {"retentionDays": clamp_retention_days(settings.get("retentionDays"))}
    SETTINGS_FILE.write_text(json.dumps(safe_settings, indent=2), encoding="utf-8")
    return safe_settings


def get_retention_days():
    return load_settings()["retentionDays"]


def json_response(handler, status, payload):
    body = json.dumps(payload).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(body)


def recorder_running():
    global recorder_process, recorder_started_at, recorder_log
    if recorder_process is None:
        return False
    if recorder_process.poll() is None:
        return True
    recorder_process = None
    recorder_started_at = None
    if recorder_log:
        recorder_log.close()
        recorder_log = None
    return False


def recording_status():
    return {
        "running": recorder_running(),
        "mode": "automatic",
        "source": RECORDING_SOURCE,
        "segmentSeconds": SEGMENT_SECONDS,
        "recordingsDir": str(RECORDINGS_DIR),
        "retentionDays": get_retention_days(),
        "maxRetentionDays": MAX_RETENTION_DAYS,
        "startedAt": recorder_started_at,
    }


def cleanup_old_recordings():
    retention_days = get_retention_days()
    RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)
    cutoff = time.time() - (retention_days * 24 * 60 * 60)
    deleted = 0
    for path in RECORDINGS_DIR.glob("*.mp4"):
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
                deleted += 1
        except OSError:
            pass
    return deleted


def start_recorder():
    global recorder_process, recorder_started_at, recorder_log

    with recorder_lock:
        if recorder_running():
            return {"ok": True, "message": "Recorder already running", "status": recording_status()}

        RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)
        cleanup_old_recordings()
        pattern = RECORDINGS_DIR / "pramacam_%Y-%m-%d_%H-%M-%S.mp4"
        log_path = RECORDINGS_DIR / "recorder.log"
        recorder_log = open(log_path, "ab")

        cmd = [
            FFMPEG_BIN,
            "-hide_banner",
            "-loglevel",
            "warning",
            "-rtsp_transport",
            "tcp",
            "-i",
            RECORDING_SOURCE,
            "-map",
            "0",
            "-c",
            "copy",
            "-f",
            "segment",
            "-segment_time",
            str(SEGMENT_SECONDS),
            "-reset_timestamps",
            "1",
            "-strftime",
            "1",
            str(pattern),
        ]

        try:
            recorder_process = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=recorder_log,
                stderr=recorder_log,
                cwd=str(ROOT),
            )
        except Exception as exc:
            recorder_log.close()
            recorder_log = None
            return {"ok": False, "message": f"Could not start ffmpeg: {exc}", "status": recording_status()}

        time.sleep(0.8)
        if recorder_process.poll() is not None:
            recorder_process = None
            recorder_log.close()
            recorder_log = None
            return {
                "ok": False,
                "message": "ffmpeg stopped immediately. Check recordings/recorder.log",
                "status": recording_status(),
            }

        recorder_started_at = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        return {"ok": True, "message": "Automatic recording started", "status": recording_status()}


def stop_recorder():
    global recorder_process, recorder_started_at, recorder_log

    with recorder_lock:
        if not recorder_running():
            return {"ok": True, "message": "Recorder is not running", "status": recording_status()}

        process = recorder_process
        try:
            if process.stdin:
                process.stdin.write(b"q")
                process.stdin.flush()
            process.wait(timeout=8)
        except Exception:
            process.terminate()
            try:
                process.wait(timeout=5)
            except Exception:
                process.kill()

        recorder_process = None
        recorder_started_at = None
        if recorder_log:
            recorder_log.close()
            recorder_log = None
        return {"ok": True, "message": "Recording stopped", "status": recording_status()}


def ensure_recorder_running():
    if shutdown_event.is_set():
        return
    if not recorder_running():
        start_recorder()


def maintenance_loop():
    while not shutdown_event.is_set():
        cleanup_old_recordings()
        ensure_recorder_running()
        shutdown_event.wait(60)


def list_recordings():
    RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)
    cleanup_old_recordings()
    files = []
    for path in sorted(RECORDINGS_DIR.glob("*.mp4"), key=lambda item: item.stat().st_mtime, reverse=True):
        stat = path.stat()
        files.append(
            {
                "name": path.name,
                "url": f"/recordings/{path.name}",
                "sizeBytes": stat.st_size,
                "modifiedAt": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(stat.st_mtime)),
            }
        )
    return files


class CameraHandler(SimpleHTTPRequestHandler):
    def translate_path(self, path):
        parsed = urlparse(path)
        clean_path = unquote(parsed.path)

        if clean_path.startswith("/recordings/"):
            name = clean_path.removeprefix("/recordings/")
            safe_name = Path(name).name
            return str(RECORDINGS_DIR / safe_name)

        if clean_path == "/":
            clean_path = "/index.html"

        return str(ROOT / clean_path.lstrip("/"))

    def end_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        super().end_headers()

    def do_OPTIONS(self):
        self.send_response(HTTPStatus.NO_CONTENT)
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/recording/status":
            ensure_recorder_running()
            json_response(self, HTTPStatus.OK, recording_status())
            return
        if parsed.path == "/api/recordings":
            json_response(self, HTTPStatus.OK, {"recordings": list_recordings()})
            return
        if parsed.path == "/api/settings":
            settings = load_settings()
            settings["maxRetentionDays"] = MAX_RETENTION_DAYS
            json_response(self, HTTPStatus.OK, settings)
            return
        super().do_GET()

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/settings":
            length = int(self.headers.get("Content-Length", "0") or "0")
            try:
                payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
            except json.JSONDecodeError:
                payload = {}
            settings = save_settings({"retentionDays": payload.get("retentionDays")})
            cleanup_old_recordings()
            settings["maxRetentionDays"] = MAX_RETENTION_DAYS
            json_response(self, HTTPStatus.OK, {"ok": True, "settings": settings, "status": recording_status()})
            return
        if parsed.path in ("/api/recording/start", "/api/recording/stop"):
            json_response(
                self,
                HTTPStatus.METHOD_NOT_ALLOWED,
                {"ok": False, "message": "Recording is automatic and cannot be controlled manually."},
            )
            return
        json_response(self, HTTPStatus.NOT_FOUND, {"ok": False, "message": "Not found"})

    def log_message(self, format, *args):
        print("[%s] %s" % (self.log_date_time_string(), format % args))


def shutdown_handler(signum, frame):
    shutdown_event.set()
    stop_recorder()
    raise SystemExit(0)


if __name__ == "__main__":
    signal.signal(signal.SIGINT, shutdown_handler)
    signal.signal(signal.SIGTERM, shutdown_handler)
    RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)
    save_settings(load_settings())
    start_result = start_recorder()
    print(start_result["message"])
    maintenance = threading.Thread(target=maintenance_loop, daemon=True)
    maintenance.start()
    server = ThreadingHTTPServer(("0.0.0.0", PORT), CameraHandler)
    print(f"Camera Monitor running at http://0.0.0.0:{PORT}")
    print(f"Recording source: {RECORDING_SOURCE}")
    print(f"Recordings folder: {RECORDINGS_DIR}")
    print(f"Retention: {get_retention_days()} day(s), maximum {MAX_RETENTION_DAYS} day(s)")
    server.serve_forever()
