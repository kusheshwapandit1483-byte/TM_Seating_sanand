#!/usr/bin/env python3
import json
import os
import signal
import shutil
import socket
import subprocess
import threading
import time
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, unquote, urlparse

ROOT = Path(__file__).resolve().parent
RECORDINGS_DIR = Path(os.environ.get("RECORDINGS_DIR", ROOT / "recordings")).resolve()
SETTINGS_FILE = Path(os.environ.get("SETTINGS_FILE", ROOT / "settings.json")).resolve()
HLS_CACHE_DIR = Path(os.environ.get("HLS_CACHE_DIR", RECORDINGS_DIR / ".hls")).resolve()
RECORDING_SOURCE = os.environ.get("RECORDING_SOURCE", "rtsp://127.0.0.1:8554/pramacam")
SEGMENT_SECONDS = int(os.environ.get("SEGMENT_SECONDS", "1800"))
DEFAULT_RETENTION_DAYS = int(os.environ.get("RETENTION_DAYS", "2"))
MAX_RETENTION_DAYS = 2
MIN_RETENTION_DAYS = 1
FFMPEG_BIN = os.environ.get("FFMPEG_BIN", "ffmpeg")
HLS_SEGMENT_SECONDS = int(os.environ.get("HLS_SEGMENT_SECONDS", "6"))
PORT = int(os.environ.get("PORT", "8080"))
ESP32_MQTT_HOST = os.environ.get("ESP32_MQTT_HOST", "192.168.10.1")
ESP32_MQTT_PORT = int(os.environ.get("ESP32_MQTT_PORT", "1883"))
ESP32_MQTT_TOPIC = os.environ.get("ESP32_MQTT_TOPIC", "eagle/system/status")
AI_DETECTION_ENABLED = os.environ.get("AI_DETECTION_ENABLED", "0").lower() in ("1", "true", "yes", "on")
AI_DETECTION_BACKEND = os.environ.get("AI_DETECTION_BACKEND", "opencv-onnx")
AI_DETECTION_SOURCE = os.environ.get("AI_DETECTION_SOURCE", RECORDING_SOURCE)
AI_DETECTION_MODEL = os.environ.get("AI_DETECTION_MODEL", "")
AI_DETECTION_INPUT_SIZE = int(os.environ.get("AI_DETECTION_INPUT_SIZE", "640"))
AI_DETECTION_CONFIDENCE = float(os.environ.get("AI_DETECTION_CONFIDENCE", "0.35"))
AI_DETECTION_IOU = float(os.environ.get("AI_DETECTION_IOU", "0.45"))
AI_DETECTION_INTERVAL = float(os.environ.get("AI_DETECTION_INTERVAL", "0.15"))
AI_DETECTION_HOLD_SECONDS = float(os.environ.get("AI_DETECTION_HOLD_SECONDS", "1.0"))

recorder_process = None
recorder_started_at = None
recorder_log = None
recorder_lock = threading.Lock()
shutdown_event = threading.Event()
esp32_monitor = None
person_detection_monitor = None


def mqtt_remaining_length(length):
    encoded = bytearray()
    while True:
        digit = length % 128
        length //= 128
        if length > 0:
            digit |= 128
        encoded.append(digit)
        if length == 0:
            return bytes(encoded)


def mqtt_string(value):
    payload = value.encode("utf-8")
    return len(payload).to_bytes(2, "big") + payload


def read_exact(sock, length):
    chunks = bytearray()
    while len(chunks) < length:
        chunk = sock.recv(length - len(chunks))
        if not chunk:
            raise ConnectionError("MQTT socket closed")
        chunks.extend(chunk)
    return bytes(chunks)


def read_mqtt_packet(sock):
    header = read_exact(sock, 1)[0]
    multiplier = 1
    remaining = 0
    while True:
        digit = read_exact(sock, 1)[0]
        remaining += (digit & 127) * multiplier
        if (digit & 128) == 0:
            break
        multiplier *= 128
        if multiplier > 128 * 128 * 128:
            raise ValueError("Malformed MQTT remaining length")
    return header, read_exact(sock, remaining)


class ESP32StatusMonitor:
    def __init__(self, host, port, topic):
        self.host = host
        self.port = port
        self.topic = topic
        self.lock = threading.Lock()
        self.latest = None
        self.connected = False
        self.last_error = "not connected"
        self.last_message_at = None
        self.thread = threading.Thread(target=self.run, daemon=True)

    def start(self):
        self.thread.start()

    def snapshot(self):
        with self.lock:
            payload = dict(self.latest or {})
            last_message_at = self.last_message_at
            age_seconds = time.time() - last_message_at if last_message_at else None
            return {
                "connected": self.connected,
                "host": self.host,
                "port": self.port,
                "topic": self.topic,
                "lastMessageAt": last_message_at,
                "ageSeconds": age_seconds,
                "error": self.last_error,
                "data": payload,
            }

    def set_status(self, connected, error=None):
        with self.lock:
            self.connected = connected
            self.last_error = None if connected else error

    def set_latest(self, payload):
        with self.lock:
            self.latest = payload
            self.connected = True
            self.last_error = None
            self.last_message_at = time.time()

    def connect_packet(self):
        client_id = f"tm-dashboard-{os.getpid()}"
        variable_header = mqtt_string("MQTT") + bytes([4, 2]) + (30).to_bytes(2, "big")
        payload = mqtt_string(client_id)
        body = variable_header + payload
        return bytes([0x10]) + mqtt_remaining_length(len(body)) + body

    def subscribe_packet(self):
        packet_id = (1).to_bytes(2, "big")
        body = packet_id + mqtt_string(self.topic) + bytes([0])
        return bytes([0x82]) + mqtt_remaining_length(len(body)) + body

    def send_ping(self, sock):
        sock.sendall(b"\xc0\x00")

    def handle_publish(self, header, body, sock):
        if len(body) < 2:
            return
        topic_len = int.from_bytes(body[:2], "big")
        topic_start = 2
        topic_end = topic_start + topic_len
        topic = body[topic_start:topic_end].decode("utf-8", errors="replace")
        qos = (header >> 1) & 0x03
        payload_start = topic_end
        packet_id = None
        if qos:
            packet_id = body[payload_start:payload_start + 2]
            payload_start += 2
        if topic == self.topic:
            payload_text = body[payload_start:].decode("utf-8", errors="replace")
            self.set_latest(json.loads(payload_text))
        if qos == 1 and packet_id:
            sock.sendall(b"\x40\x02" + packet_id)

    def mqtt_session(self):
        with socket.create_connection((self.host, self.port), timeout=10) as sock:
            sock.settimeout(10)
            sock.sendall(self.connect_packet())
            header, body = read_mqtt_packet(sock)
            if header >> 4 != 2 or len(body) < 2 or body[1] != 0:
                raise ConnectionError("MQTT CONNACK failed")
            sock.sendall(self.subscribe_packet())
            header, body = read_mqtt_packet(sock)
            if header >> 4 != 9:
                raise ConnectionError("MQTT SUBACK failed")
            self.set_status(True, None)
            last_ping = time.time()
            while not shutdown_event.is_set():
                try:
                    header, body = read_mqtt_packet(sock)
                except socket.timeout:
                    now = time.time()
                    if now - last_ping >= 20:
                        self.send_ping(sock)
                        last_ping = now
                    continue
                packet_type = header >> 4
                if packet_type == 3:
                    self.handle_publish(header, body, sock)
                elif packet_type == 13:
                    continue

    def run(self):
        while not shutdown_event.is_set():
            try:
                self.mqtt_session()
            except Exception as exc:
                self.set_status(False, str(exc))
                shutdown_event.wait(5)



class PersonDetectionMonitor:
    def __init__(self, source, backend, model_path, input_size, confidence, iou, interval, hold_seconds):
        self.source = source
        self.backend = backend
        self.model_path = model_path
        self.input_size = input_size
        self.confidence = confidence
        self.iou = iou
        self.interval = interval
        self.hold_seconds = hold_seconds
        self.lock = threading.Lock()
        self.running = False
        self.last_error = None
        self.last_update_at = None
        self.frame_width = None
        self.frame_height = None
        self.detections = []
        self.last_positive_detections = []
        self.last_positive_at = None
        self.held = False
        self.thread = threading.Thread(target=self.run, daemon=True)

    def start(self):
        self.thread.start()

    def snapshot(self):
        with self.lock:
            last_update_at = self.last_update_at
            age_seconds = time.time() - last_update_at if last_update_at else None
            return {
                "enabled": AI_DETECTION_ENABLED,
                "running": self.running,
                "backend": self.backend,
                "source": self.source,
                "model": self.model_path,
                "inputSize": self.input_size,
                "confidenceThreshold": self.confidence,
                "holdSeconds": self.hold_seconds,
                "held": self.held,
                "frameWidth": self.frame_width,
                "frameHeight": self.frame_height,
                "updatedAt": last_update_at,
                "ageSeconds": age_seconds,
                "error": self.last_error,
                "detections": list(self.detections),
            }

    def set_error(self, message):
        with self.lock:
            self.running = False
            self.last_error = message

    def set_detections(self, frame_width, frame_height, detections):
        now = time.time()
        visible_detections = detections
        held = False
        if detections:
            self.last_positive_detections = detections
            self.last_positive_at = now
        elif self.last_positive_at and now - self.last_positive_at <= self.hold_seconds:
            visible_detections = self.last_positive_detections
            held = True

        with self.lock:
            self.running = True
            self.last_error = None
            self.last_update_at = now
            self.frame_width = int(frame_width) if frame_width else None
            self.frame_height = int(frame_height) if frame_height else None
            self.detections = visible_detections
            self.held = held

    def load_detector(self, cv2):
        if self.backend == "hog":
            hog = cv2.HOGDescriptor()
            hog.setSVMDetector(cv2.HOGDescriptor_getDefaultPeopleDetector())
            return hog
        if self.backend != "opencv-onnx":
            raise ValueError(f"Unsupported AI_DETECTION_BACKEND: {self.backend}")
        if not self.model_path:
            raise ValueError("Set AI_DETECTION_MODEL to a person-capable ONNX model path")
        model = Path(self.model_path).expanduser()
        if not model.exists():
            raise FileNotFoundError(f"AI model not found: {model}")
        return cv2.dnn.readNetFromONNX(str(model))

    def detect_hog(self, cv2, detector, frame):
        height, width = frame.shape[:2]
        boxes, weights = detector.detectMultiScale(frame, winStride=(8, 8), padding=(16, 16), scale=1.05)
        detections = []
        for box, confidence in zip(boxes, weights):
            confidence = float(confidence)
            if confidence < self.confidence:
                continue
            x, y, w, h = [int(value) for value in box]
            detections.append(self.make_detection(x, y, w, h, confidence, width, height))
        return detections

    def detect_onnx(self, cv2, detector, frame):
        height, width = frame.shape[:2]
        blob = cv2.dnn.blobFromImage(
            frame,
            1 / 255.0,
            (self.input_size, self.input_size),
            swapRB=True,
            crop=False,
        )
        detector.setInput(blob)
        output_names = detector.getUnconnectedOutLayersNames()
        outputs = detector.forward(output_names)
        rows = outputs[0]
        while len(rows.shape) > 2:
            rows = rows[0]
        if rows.shape[0] in (84, 85) and rows.shape[1] > rows.shape[0]:
            rows = rows.transpose()

        x_scale = width / float(self.input_size)
        y_scale = height / float(self.input_size)
        boxes = []
        scores = []
        for row in rows:
            parsed = self.parse_yolo_person_row(row, x_scale, y_scale)
            if not parsed:
                continue
            x, y, w, h, confidence = parsed
            boxes.append([int(x), int(y), int(w), int(h)])
            scores.append(float(confidence))

        indexes = cv2.dnn.NMSBoxes(boxes, scores, self.confidence, self.iou)
        if len(indexes) == 0:
            return []
        detections = []
        for index in indexes.flatten():
            x, y, w, h = boxes[int(index)]
            detections.append(self.make_detection(x, y, w, h, scores[int(index)], width, height))
        return detections

    def parse_yolo_person_row(self, row, x_scale, y_scale):
        values = row.tolist()
        if len(values) == 6:
            class_id = int(values[5])
            confidence = float(values[4])
            if class_id != 0 or confidence < self.confidence:
                return None
            x1, y1, x2, y2 = values[:4]
            return x1 * x_scale, y1 * y_scale, (x2 - x1) * x_scale, (y2 - y1) * y_scale, confidence

        if len(values) >= 85:
            confidence = float(values[4]) * float(values[5])
        elif len(values) >= 84:
            confidence = float(values[4])
        else:
            return None

        if confidence < self.confidence:
            return None
        cx, cy, w, h = [float(value) for value in values[:4]]
        x = (cx - w / 2) * x_scale
        y = (cy - h / 2) * y_scale
        return x, y, w * x_scale, h * y_scale, confidence

    def make_detection(self, x, y, width, height, confidence, frame_width, frame_height):
        x = max(0, min(int(x), frame_width - 1))
        y = max(0, min(int(y), frame_height - 1))
        width = max(1, min(int(width), frame_width - x))
        height = max(1, min(int(height), frame_height - y))
        return {
            "class": "person",
            "confidence": round(float(confidence), 4),
            "x": x,
            "y": y,
            "width": width,
            "height": height,
            "xNorm": x / frame_width,
            "yNorm": y / frame_height,
            "widthNorm": width / frame_width,
            "heightNorm": height / frame_height,
        }

    def run(self):
        if not AI_DETECTION_ENABLED:
            self.set_error("AI detection disabled. Start with AI_DETECTION_ENABLED=1 to enable person detection.")
            return
        try:
            import cv2
        except Exception as exc:
            self.set_error(f"OpenCV import failed: {exc}")
            return

        try:
            detector = self.load_detector(cv2)
        except Exception as exc:
            self.set_error(str(exc))
            return

        while not shutdown_event.is_set():
            capture = cv2.VideoCapture(self.source)
            if not capture.isOpened():
                self.set_error(f"Could not open detection source: {self.source}")
                shutdown_event.wait(3)
                continue
            try:
                while not shutdown_event.is_set():
                    ok, frame = capture.read()
                    if not ok or frame is None:
                        self.set_error("Detection source frame read failed")
                        break
                    if self.backend == "hog":
                        detections = self.detect_hog(cv2, detector, frame)
                    else:
                        detections = self.detect_onnx(cv2, detector, frame)
                    height, width = frame.shape[:2]
                    self.set_detections(width, height, detections)
                    shutdown_event.wait(self.interval)
            except Exception as exc:
                self.set_error(f"Detection failed: {exc}")
                shutdown_event.wait(2)
            finally:
                capture.release()
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
    HLS_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cutoff = time.time() - (retention_days * 24 * 60 * 60)
    deleted = 0
    active_recording_stems = set()

    for path in RECORDINGS_DIR.glob("*.mp4"):
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
                shutil.rmtree(HLS_CACHE_DIR / path.stem, ignore_errors=True)
                deleted += 1
            else:
                active_recording_stems.add(path.stem)
        except OSError:
            pass

    for path in HLS_CACHE_DIR.iterdir():
        try:
            if path.is_dir() and path.name not in active_recording_stems:
                shutil.rmtree(path, ignore_errors=True)
        except OSError:
            pass

    return deleted


def resolve_recording_path(name):
    safe_name = Path(str(name or "")).name
    recording_path = (RECORDINGS_DIR / safe_name).resolve()
    if (
        RECORDINGS_DIR not in recording_path.parents
        or recording_path.suffix.lower() != ".mp4"
        or not recording_path.exists()
        or not recording_path.is_file()
    ):
        return None
    return recording_path


def start_recorder():
    global recorder_process, recorder_started_at, recorder_log

    with recorder_lock:
        if recorder_running():
            return {"ok": True, "message": "Recorder already running", "status": recording_status()}

        RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)
        cleanup_old_recordings()
        pattern = RECORDINGS_DIR / "data_%Y-%m-%d_%H-%M-%S.mp4"
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



def ensure_recording_hls(name):
    recording_path = resolve_recording_path(name)
    if not recording_path:
        return {"ok": False, "message": "Recording not found"}

    HLS_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    hls_dir = HLS_CACHE_DIR / recording_path.stem
    playlist_path = hls_dir / "index.m3u8"
    if playlist_path.exists() and playlist_path.stat().st_mtime >= recording_path.stat().st_mtime:
        return {
            "ok": True,
            "message": "HLS playlist ready",
            "playlistUrl": f"/recording-hls/{quote(recording_path.stem)}/index.m3u8",
            "name": recording_path.name,
        }

    temp_dir = HLS_CACHE_DIR / f".{recording_path.stem}.tmp"
    shutil.rmtree(temp_dir, ignore_errors=True)
    temp_dir.mkdir(parents=True, exist_ok=True)
    segment_pattern = "segment_%05d.ts"
    temp_playlist = temp_dir / "index.m3u8"
    cmd = [
        FFMPEG_BIN,
        "-hide_banner",
        "-loglevel",
        "warning",
        "-y",
        "-i",
        str(recording_path),
        "-map",
        "0:v:0",
        "-map",
        "0:a?",
        "-c",
        "copy",
        "-f",
        "hls",
        "-hls_time",
        str(HLS_SEGMENT_SECONDS),
        "-hls_playlist_type",
        "vod",
        "-hls_segment_filename",
        segment_pattern,
        "index.m3u8",
    ]

    try:
        result = subprocess.run(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            cwd=str(temp_dir),
            timeout=900,
            check=False,
        )
    except Exception as exc:
        shutil.rmtree(temp_dir, ignore_errors=True)
        return {"ok": False, "message": f"Could not prepare HLS playback: {exc}"}

    if result.returncode != 0 or not temp_playlist.exists():
        error_text = result.stderr.decode("utf-8", errors="replace").strip()
        shutil.rmtree(temp_dir, ignore_errors=True)
        message = error_text[-500:] if error_text else "ffmpeg could not prepare HLS playback"
        return {"ok": False, "message": message}

    shutil.rmtree(hls_dir, ignore_errors=True)
    temp_dir.rename(hls_dir)
    return {
        "ok": True,
        "message": "HLS playlist ready",
        "playlistUrl": f"/recording-hls/{quote(recording_path.stem)}/index.m3u8",
        "name": recording_path.name,
    }


def stream_file_body(handler, safe_path, start=0, length=None):
    with safe_path.open("rb") as file:
        file.seek(start)
        remaining = length
        while remaining is None or remaining > 0:
            read_size = 1024 * 1024 if remaining is None else min(1024 * 1024, remaining)
            chunk = file.read(read_size)
            if not chunk:
                break
            try:
                handler.wfile.write(chunk)
            except (BrokenPipeError, ConnectionResetError):
                break
            if remaining is not None:
                remaining -= len(chunk)


def serve_hls_file(handler, path):
    safe_path = Path(path).resolve()
    if (
        HLS_CACHE_DIR not in safe_path.parents
        or safe_path.suffix.lower() not in (".m3u8", ".ts")
        or not safe_path.exists()
        or not safe_path.is_file()
    ):
        handler.send_error(HTTPStatus.NOT_FOUND, "HLS file not found")
        return

    content_type = "application/vnd.apple.mpegurl" if safe_path.suffix.lower() == ".m3u8" else "video/mp2t"
    file_size = safe_path.stat().st_size
    handler.send_response(HTTPStatus.OK)
    handler.send_header("Content-Type", content_type)
    handler.send_header("Content-Length", str(file_size))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    stream_file_body(handler, safe_path)

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
                elif end_text:
                    suffix_length = int(end_text)
                    start = max(file_size - suffix_length, 0)
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

    stream_file_body(handler, safe_path, start, content_length)

class CameraHandler(SimpleHTTPRequestHandler):
    def translate_path(self, path):
        parsed = urlparse(path)
        clean_path = unquote(parsed.path)

        if clean_path.startswith("/recording-hls/"):
            name = clean_path.removeprefix("/recording-hls/")
            safe_parts = [Path(part).name for part in Path(name).parts if part not in ("", ".", "..")]
            return str(HLS_CACHE_DIR.joinpath(*safe_parts))

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
        if parsed.path == "/api/esp32/status":
            payload = esp32_monitor.snapshot() if esp32_monitor else {"connected": False, "error": "monitor not started", "data": {}}
            json_response(self, HTTPStatus.OK, payload)
            return
        if parsed.path == "/api/detections/latest":
            payload = person_detection_monitor.snapshot() if person_detection_monitor else {
                "enabled": False,
                "running": False,
                "error": "person detection monitor not started",
                "detections": [],
            }
            json_response(self, HTTPStatus.OK, payload)
            return
        if parsed.path == "/api/recording/status":
            ensure_recorder_running()
            json_response(self, HTTPStatus.OK, recording_status())
            return
        if parsed.path == "/api/recordings":
            json_response(self, HTTPStatus.OK, {"recordings": list_recordings()})
            return
        if parsed.path.startswith("/recording-hls/"):
            serve_hls_file(self, self.translate_path(self.path))
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
        if parsed.path == "/api/recordings/hls":
            payload = read_json_body(self)
            result = ensure_recording_hls(payload.get("name"))
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
    esp32_monitor = ESP32StatusMonitor(ESP32_MQTT_HOST, ESP32_MQTT_PORT, ESP32_MQTT_TOPIC)
    esp32_monitor.start()
    person_detection_monitor = PersonDetectionMonitor(
        AI_DETECTION_SOURCE,
        AI_DETECTION_BACKEND,
        AI_DETECTION_MODEL,
        AI_DETECTION_INPUT_SIZE,
        AI_DETECTION_CONFIDENCE,
        AI_DETECTION_IOU,
        AI_DETECTION_INTERVAL,
        AI_DETECTION_HOLD_SECONDS,
    )
    person_detection_monitor.start()
    maintenance = threading.Thread(target=maintenance_loop, daemon=True)
    maintenance.start()
    server = ThreadingHTTPServer(("0.0.0.0", PORT), CameraHandler)
    print(f"Camera Monitor running at http://0.0.0.0:{PORT}")
    print(f"Recording source: {RECORDING_SOURCE}")
    print(f"ESP32 MQTT: {ESP32_MQTT_HOST}:{ESP32_MQTT_PORT} topic {ESP32_MQTT_TOPIC}")
    print(f"AI detection: {AI_DETECTION_BACKEND} enabled={AI_DETECTION_ENABLED} source={AI_DETECTION_SOURCE}")
    print(f"Recordings folder: {RECORDINGS_DIR}")
    print(f"Retention: {get_retention_days()} day(s), maximum {MAX_RETENTION_DAYS} day(s)")
    server.serve_forever()
