#!/usr/bin/env python3
import json
import os
import signal
import subprocess
import time
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

ROOT = Path(__file__).resolve().parent
RECORDINGS_DIR = Path(os.environ.get("RECORDINGS_DIR", ROOT / "recordings")).resolve()
RECORDING_SOURCE = os.environ.get("RECORDING_SOURCE", "rtsp://127.0.0.1:8554/pramacam")
SEGMENT_SECONDS = int(os.environ.get("SEGMENT_SECONDS", "1800"))
RETENTION_DAYS = int(os.environ.get("RETENTION_DAYS", "2"))
FFMPEG_BIN = os.environ.get("FFMPEG_BIN", "ffmpeg")
PORT = int(os.environ.get("PORT", "8080"))

recorder_process = None
recorder_started_at = None
recorder_log = None


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
        "source": RECORDING_SOURCE,
        "segmentSeconds": SEGMENT_SECONDS,
        "recordingsDir": str(RECORDINGS_DIR),
        "retentionDays": RETENTION_DAYS,
        "startedAt": recorder_started_at,
    }


def cleanup_old_recordings():
    if RETENTION_DAYS <= 0:
        return 0

    RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)
    cutoff = time.time() - (RETENTION_DAYS * 24 * 60 * 60)
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

    if recorder_running():
        return {"ok": True, "message": "Recorder already running", "status": recording_status()}

    RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)
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
        return {"ok": False, "message": "ffmpeg stopped immediately. Check recordings/recorder.log", "status": recording_status()}

    recorder_started_at = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    return {"ok": True, "message": "Recording started", "status": recording_status()}


def stop_recorder():
    global recorder_process, recorder_started_at, recorder_log

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


def list_recordings():
    RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)
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
            json_response(self, HTTPStatus.OK, recording_status())
            return
        if parsed.path == "/api/recordings":
            json_response(self, HTTPStatus.OK, {"recordings": list_recordings()})
            return
        super().do_GET()

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/recording/start":
            json_response(self, HTTPStatus.OK, start_recorder())
            return
        if parsed.path == "/api/recording/stop":
            json_response(self, HTTPStatus.OK, stop_recorder())
            return
        json_response(self, HTTPStatus.NOT_FOUND, {"ok": False, "message": "Not found"})

    def log_message(self, format, *args):
        print("[%s] %s" % (self.log_date_time_string(), format % args))


def shutdown_handler(signum, frame):
    stop_recorder()
    raise SystemExit(0)


if __name__ == "__main__":
    signal.signal(signal.SIGINT, shutdown_handler)
    signal.signal(signal.SIGTERM, shutdown_handler)
    RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer(("0.0.0.0", PORT), CameraHandler)
    print(f"Camera Monitor running at http://0.0.0.0:{PORT}")
    print(f"Recording source: {RECORDING_SOURCE}")
    print(f"Recordings folder: {RECORDINGS_DIR}")
    print(f"Retention: {RETENTION_DAYS} day(s)")
    server.serve_forever()