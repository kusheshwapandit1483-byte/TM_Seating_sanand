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
VLC_BIN = os.environ.get("VLC_BIN", "vlc")
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


def read_json_body(handler):
    length = int(handler.headers.get("Content-Length", "0") or "0")
    try:
        return json.loads(handler.rfile.read(length).decode("utf-8") or "{}")
    except json.JSONDecodeError:
        return {}


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



def open_recording_in_vlc(name):
    if not name:
        return {"ok": False, "message": "Recording name is required"}

    safe_name = Path(str(name)).name
    recording_path = (RECORDINGS_DIR / safe_name).resolve()
    if (
        RECORDINGS_DIR not in recording_path.parents
        or recording_path.suffix.lower() != ".mp4"
        or not recording_path.exists()
        or not recording_path.is_file()
    ):
        return {"ok": False, "message": "Recording not found"}

    cmd = [
        VLC_BIN,
        "--started-from-file",
        "--no-video-title-show",
        str(recording_path),
    ]

    try:
        subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            cwd=str(ROOT),
            start_new_session=True,
        )
    except Exception as exc:
        return {"ok": False, "message": f"Could not open VLC: {exc}"}

    return {"ok": True, "message": "Opened in VLC", "name": recording_path.name}


def serve_recording_file(handler, path):
    safe_path = Path(path).resolve()
    if (
        RECORDINGS_DIR not in safe_path.parents
        or safe_path.suffix.lower() != ".mp4"
        or not safe_path.exists()
        or not safe_path.is_file()
    ):
        handler.send_error(HTTPStatus.NOT_FOUND, "Recording not found")
        return

    file_size = safe_path.stat().st_size
    range_header = handler.headers.get("Range")
    start = 0
    end = file_size - 1
    status = HTTPStatus.OK

    if range_header:
        try:
            units, range_value = range_header.split("=", 1)
            if units.strip().lower() == "bytes":
                start_text, end_text = range_value.split("-", 1)
                if start_text:
                    start = int(start_text)
                if end_text:
                    end = int(end_text)
                status = HTTPStatus.PARTIAL_CONTENT
        except (ValueError, TypeError):
            handler.send_error(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
            return

    if file_size == 0 or start < 0 or end >= file_size or start > end:
        handler.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
        handler.send_header("Content-Range", f"bytes */{file_size}")
        handler.end_headers()
        return

    content_length = end - start + 1
    handler.send_response(status)
    handler.send_header("Content-Type", "video/mp4")
    handler.send_header("Accept-Ranges", "bytes")
    handler.send_header("Content-Length", str(content_length))
    handler.send_header("Cache-Control", "no-store")
    if status == HTTPStatus.PARTIAL_CONTENT:
        handler.send_header("Content-Range", f"bytes {start}-{end}/{file_size}")
    handler.end_headers()

    with safe_path.open("rb") as file:
        file.seek(start)
        remaining = content_length
        while remaining > 0:
            chunk = file.read(min(1024 * 1024, remaining))
            if not chunk:
                break
            try:
                handler.wfile.write(chunk)
            except (BrokenPipeError, ConnectionResetError):
                break
            remaining -= len(chunk)

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
        if parsed.path.startswith("/recordings/"):
            serve_recording_file(self, self.translate_path(self.path))
            return
        if parsed.path == "/api/settings":
            settings = load_settings()
            settings["maxRetentionDays"] = MAX_RETENTION_DAYS
            json_response(self, HTTPStatus.OK, settings)
            return
        super().do_GET()

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/recordings/open-vlc":
            payload = read_json_body(self)
            result = open_recording_in_vlc(payload.get("name"))
            status = HTTPStatus.OK if result["ok"] else HTTPStatus.BAD_REQUEST
            json_response(self, status, result)
            return
        if parsed.path == "/api/settings":
            payload = read_json_body(self)
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
