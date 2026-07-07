const video = document.querySelector("#cameraVideo");
const frame = document.querySelector("#cameraFrame");
const image = document.querySelector("#cameraImage");
const emptyState = document.querySelector("#emptyState");
const detectionOverlay = document.querySelector("#detectionOverlay");
const detectionStatus = document.querySelector("#detectionStatus");
const navButtons = document.querySelectorAll("[data-view-target]");
const viewPanels = document.querySelectorAll(".view-panel");
const refreshRecordings = document.querySelector("#refreshRecordings");
const recordingsList = document.querySelector("#recordingsList");
const recordingPlayer = document.querySelector("#recordingPlayer");
const recordingEmptyState = document.querySelector("#recordingEmptyState");
const personCount = document.querySelector("#personCount");
const entryCount = document.querySelector("#entryCount");
const exitCount = document.querySelector("#exitCount");
const conditionTile = document.querySelector("#conditionTile");
const conditionStatus = document.querySelector("#conditionStatus");

const previewHost = window.location.hostname || "127.0.0.1";
const liveMode = (new URLSearchParams(window.location.search).get("live") || "hls").toLowerCase();
const webrtcPreviewUrl = `http://${previewHost}:8889/pramacam`;
const hlsPreviewUrl = `http://${previewHost}:8888/pramacam/index.m3u8`;
const mjpegPreviewUrl = "/live.mjpg";
const fixedPreviewUrl = liveMode === "hls" ? hlsPreviewUrl : liveMode === "mjpeg" ? mjpegPreviewUrl : webrtcPreviewUrl;
const directVideoExtensions = [".mp4", ".webm", ".ogg", ".mov"];
let hlsPlayer = null;
let recordingHlsPlayer = null;
let activeView = "liveView";
let recordingFallbackClip = null;
let recordingFallbackTried = false;
let recordingFallbackInProgress = false;
let latestDetectionPayload = null;

function formatCount(value) {
  const number = Number(value);
  return Number.isFinite(number) ? String(number) : "--";
}

function resizeDetectionOverlay() {
  const rect = detectionOverlay.getBoundingClientRect();
  const pixelRatio = window.devicePixelRatio || 1;
  const width = Math.max(1, Math.round(rect.width * pixelRatio));
  const height = Math.max(1, Math.round(rect.height * pixelRatio));

  if (detectionOverlay.width !== width || detectionOverlay.height !== height) {
    detectionOverlay.width = width;
    detectionOverlay.height = height;
  }
}

function setDetectionStatus(payload) {
  if (!payload || !payload.enabled) {
    detectionStatus.textContent = "AI off";
    detectionStatus.className = "detection-status is-waiting";
    return;
  }

  if (payload.error) {
    detectionStatus.textContent = "AI waiting";
    detectionStatus.className = "detection-status is-waiting";
    return;
  }

  const count = Array.isArray(payload.detections) ? payload.detections.length : 0;
  detectionStatus.textContent = count === 1 ? "1 person" : `${count} persons`;
  detectionStatus.className = count > 0 ? "detection-status is-detecting" : "detection-status is-clear";
}

function renderDetectionOverlay() {
  resizeDetectionOverlay();
  const context = detectionOverlay.getContext("2d");
  const pixelRatio = window.devicePixelRatio || 1;
  const canvasWidth = detectionOverlay.width;
  const canvasHeight = detectionOverlay.height;
  context.clearRect(0, 0, canvasWidth, canvasHeight);

  const detections = latestDetectionPayload?.detections || [];
  if (!detections.length) {
    return;
  }

  context.save();
  context.scale(pixelRatio, pixelRatio);
  const rect = detectionOverlay.getBoundingClientRect();
  context.lineWidth = 3;
  context.font = "700 14px Inter, system-ui, sans-serif";
  context.textBaseline = "top";

  detections.forEach((detection) => {
    if (detection.class !== "person") {
      return;
    }

    const x = detection.xNorm * rect.width;
    const y = detection.yNorm * rect.height;
    const width = detection.widthNorm * rect.width;
    const height = detection.heightNorm * rect.height;
    const label = `Person ${Math.round((detection.confidence || 0) * 100)}%`;
    const labelWidth = context.measureText(label).width + 14;
    const labelY = Math.max(0, y - 28);

    context.strokeStyle = "#2fbf9b";
    context.fillStyle = "rgba(47, 191, 155, 0.12)";
    context.strokeRect(x, y, width, height);
    context.fillRect(x, y, width, height);
    context.fillStyle = "#2fbf9b";
    context.fillRect(x, labelY, labelWidth, 24);
    context.fillStyle = "#061410";
    context.fillText(label, x + 7, labelY + 5);
  });
  context.restore();
}

async function refreshDetections() {
  try {
    latestDetectionPayload = await apiRequest("/api/detections/latest");
    setDetectionStatus(latestDetectionPayload);
    renderDetectionOverlay();
  } catch (error) {
    latestDetectionPayload = null;
    detectionStatus.textContent = "AI offline";
    detectionStatus.className = "detection-status is-waiting";
    renderDetectionOverlay();
  }
}


function getCondition(data) {
  const state = String(data.state || "").toUpperCase();
  const occupancy = Number(data.occupancy || 0);
  const relayClear = data.relay === true;

  if (state === "FAULT") {
    return { label: "Fault", className: "condition-fault" };
  }
  if (state === "STARTUP") {
    return { label: "Restart", className: "condition-waiting" };
  }
  if (state === "AWAIT_START") {
    return { label: "Await Start", className: "condition-waiting" };
  }
  if (state === "RUN" && occupancy > 0) {
    return { label: "Occupied", className: "condition-occupied" };
  }
  if (state === "RUN" && occupancy === 0 && !relayClear) {
    return { label: "Await Clear", className: "condition-clearing" };
  }
  if (state === "RUN" && occupancy === 0 && relayClear) {
    return { label: "Clear", className: "condition-clear" };
  }
  return { label: state || "Waiting", className: "condition-waiting" };
}

function updateCondition(data) {
  const condition = getCondition(data);
  conditionStatus.textContent = condition.label;
  conditionTile.classList.remove("condition-waiting", "condition-clear", "condition-clearing", "condition-occupied", "condition-fault");
  conditionTile.classList.add(condition.className);
}
async function apiRequest(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });

  if (!response.ok) {
    throw new Error(`Request failed: ${response.status}`);
  }

  return response.json();
}

async function refreshEsp32Status() {
  try {
    const status = await apiRequest("/api/esp32/status");
    const data = status.data || {};
    if (typeof data.occupancy !== "undefined") {
      personCount.textContent = formatCount(data.occupancy);
    }
    if (typeof data.total_in !== "undefined") {
      entryCount.textContent = formatCount(data.total_in);
    }
    if (typeof data.total_out !== "undefined") {
      exitCount.textContent = formatCount(data.total_out);
    }
    if (data.state) {
      updateCondition(data);
    }
  } catch (error) {
    // Keep the last valid count visible during short ESP32/network delays.
  }
}
function cleanUrl(url) {
  return url.split("?")[0].toLowerCase();
}

function isHlsUrl(url) {
  return cleanUrl(url).endsWith(".m3u8");
}

function isDirectVideoUrl(url) {
  return directVideoExtensions.some((extension) => cleanUrl(url).endsWith(extension));
}

function isMjpegUrl(url) {
  const clean = cleanUrl(url);
  return clean.endsWith(".mjpg") || clean.endsWith(".mjpeg");
}

function showEmptyState(show) {
  emptyState.classList.toggle("is-hidden", !show);
}

function destroyHls() {
  if (hlsPlayer) {
    hlsPlayer.destroy();
    hlsPlayer = null;
  }
}

function loadHls(url) {
  destroyHls();
  image.hidden = true;
  image.removeAttribute("src");
  frame.hidden = true;
  video.hidden = false;

  if (video.canPlayType("application/vnd.apple.mpegurl")) {
    video.src = url;
    video.load();
    video.play().catch(() => undefined);
    return;
  }

  if (window.Hls && window.Hls.isSupported()) {
    hlsPlayer = new window.Hls({ lowLatencyMode: true, backBufferLength: 30 });
    hlsPlayer.loadSource(url);
    hlsPlayer.attachMedia(video);
    hlsPlayer.on(window.Hls.Events.MANIFEST_PARSED, () => video.play().catch(() => undefined));
    hlsPlayer.on(window.Hls.Events.ERROR, (event, data) => {
      if (data.fatal) {
        if (data.type === window.Hls.ErrorTypes.NETWORK_ERROR) {
          hlsPlayer.startLoad();
        } else if (data.type === window.Hls.ErrorTypes.MEDIA_ERROR) {
          hlsPlayer.recoverMediaError();
        } else {
          showEmptyState(true);
          setTimeout(() => loadHls(url), 5000);
        }
      }
    });
  }
}

function loadDirectVideo(url) {
  destroyHls();
  image.hidden = true;
  image.removeAttribute("src");
  frame.hidden = true;
  video.hidden = false;
  video.src = url;
  video.load();
  video.play().catch(() => undefined);
}

function loadPreviewPage(url) {
  destroyHls();
  image.hidden = true;
  image.removeAttribute("src");
  video.pause();
  video.removeAttribute("src");
  video.load();
  video.hidden = true;
  frame.src = url;
  frame.hidden = false;
  showEmptyState(false);
}

function loadMjpeg(url) {
  destroyHls();
  frame.hidden = true;
  frame.removeAttribute("src");
  video.pause();
  video.removeAttribute("src");
  video.load();
  video.hidden = true;
  image.hidden = false;
  image.src = `${url}?t=${Date.now()}`;
  showEmptyState(false);
}

function loadFixedPreview() {
  showEmptyState(false);
  if (isMjpegUrl(fixedPreviewUrl)) {
    loadMjpeg(fixedPreviewUrl);
    return;
  }
  if (isHlsUrl(fixedPreviewUrl)) {
    loadHls(fixedPreviewUrl);
    return;
  }
  if (isDirectVideoUrl(fixedPreviewUrl)) {
    loadDirectVideo(fixedPreviewUrl);
    return;
  }
  loadPreviewPage(fixedPreviewUrl);
}

function formatBytes(bytes) {
  if (!bytes) {
    return "0 MB";
  }
  const mb = bytes / (1024 * 1024);
  if (mb < 1024) {
    return `${mb.toFixed(1)} MB`;
  }
  return `${(mb / 1024).toFixed(2)} GB`;
}

function formatDate(value) {
  if (!value) {
    return "Unknown time";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return date.toLocaleString([], {
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function renderEmptyRecordings(message) {
  recordingsList.textContent = "";
  const empty = document.createElement("div");
  empty.className = "empty-list";
  empty.textContent = message;
  recordingsList.appendChild(empty);
}

function showRecordingMessage(title, message) {
  const titleNode = recordingEmptyState.querySelector("strong");
  const messageNode = recordingEmptyState.querySelector("span");
  titleNode.textContent = title;
  messageNode.textContent = message;
  recordingEmptyState.classList.remove("is-hidden");
}

function destroyRecordingHls() {
  if (recordingHlsPlayer) {
    recordingHlsPlayer.destroy();
    recordingHlsPlayer = null;
  }
}

function playRecordingSource(url) {
  destroyRecordingHls();
  recordingPlayer.pause();
  recordingPlayer.removeAttribute("src");
  recordingPlayer.load();

  if (isHlsUrl(url) && recordingPlayer.canPlayType("application/vnd.apple.mpegurl")) {
    recordingPlayer.src = url;
    recordingPlayer.load();
    recordingEmptyState.classList.add("is-hidden");
    recordingPlayer.play().catch(() => undefined);
    return;
  }

  if (isHlsUrl(url) && window.Hls && window.Hls.isSupported()) {
    recordingHlsPlayer = new window.Hls({ backBufferLength: 60 });
    recordingHlsPlayer.loadSource(url);
    recordingHlsPlayer.attachMedia(recordingPlayer);
    recordingHlsPlayer.on(window.Hls.Events.MANIFEST_PARSED, () => {
      recordingEmptyState.classList.add("is-hidden");
      recordingPlayer.play().catch(() => undefined);
    });
    recordingHlsPlayer.on(window.Hls.Events.ERROR, () => {
      showRecordingMessage("Playback unavailable", "Could not play this prepared recording. Try refresh, then select it again.");
    });
    return;
  }

  recordingPlayer.src = url;
  recordingPlayer.load();
  recordingEmptyState.classList.add("is-hidden");
  recordingPlayer.play().catch(() => undefined);
}
async function prepareRecordingHlsFallback() {
  if (!recordingFallbackClip || recordingFallbackInProgress) {
    return;
  }
  if (recordingFallbackTried) {
    showRecordingMessage("Playback unavailable", "This recording could not be played in the browser.");
    return;
  }

  recordingFallbackTried = true;
  recordingFallbackInProgress = true;
  showRecordingMessage("Preparing recording", "Large video needs browser playback preparation. Please wait.");

  try {
    const result = await apiRequest("/api/recordings/hls", {
      method: "POST",
      body: JSON.stringify({ name: recordingFallbackClip.name }),
    });
    recordingFallbackInProgress = false;
    playRecordingSource(result.playlistUrl);
  } catch (error) {
    recordingFallbackInProgress = false;
    showRecordingMessage("Playback unavailable", "This recording could not be prepared for browser playback. Try refresh, then select it again.");
  }
}

async function playRecordingClip(clip) {
  recordingFallbackClip = clip;
  recordingFallbackTried = false;
  recordingFallbackInProgress = false;
  showRecordingMessage("Loading recording", "Opening the recorded video.");
  playRecordingSource(clip.url);
}
async function refreshRecordingsList() {
  try {
    const data = await apiRequest("/api/recordings");
    const recordings = data.recordings || [];

    if (!recordings.length) {
      renderEmptyRecordings("No recordings yet. Automatic recording creates the first playable clip after the current segment finishes.");
      return;
    }

    recordingsList.innerHTML = "";
    recordings.forEach((clip) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "clip-button";

      const clipName = document.createElement("span");
      clipName.className = "clip-name";
      clipName.textContent = clip.name;

      const clipMeta = document.createElement("span");
      clipMeta.className = "clip-meta";
      clipMeta.textContent = `${formatDate(clip.modifiedAt)} - ${formatBytes(clip.sizeBytes)}`;

      button.append(clipName, clipMeta);
      button.addEventListener("click", () => {
        document.querySelectorAll(".clip-button").forEach((item) => item.classList.remove("is-selected"));
        button.classList.add("is-selected");
        playRecordingClip(clip);
      });
      recordingsList.appendChild(button);
    });
  } catch (error) {
    renderEmptyRecordings("Could not load recordings. Open the app through python server.py, not directly as a file.");
  }
}

function showView(targetId) {
  activeView = targetId;
  navButtons.forEach((button) => {
    button.classList.toggle("is-active", button.dataset.viewTarget === targetId);
  });
  viewPanels.forEach((panel) => {
    panel.classList.toggle("is-active", panel.id === targetId);
  });

  if (targetId === "recordingsView") {
    refreshRecordingsList();
  }
}

navButtons.forEach((button) => {
  button.addEventListener("click", () => showView(button.dataset.viewTarget));
});

refreshRecordings.addEventListener("click", refreshRecordingsList);
recordingPlayer.addEventListener("error", prepareRecordingHlsFallback);

frame.addEventListener("load", () => showEmptyState(false));
frame.addEventListener("error", () => showEmptyState(true));
image.addEventListener("load", () => showEmptyState(false));
image.addEventListener("error", () => showEmptyState(true));
video.addEventListener("playing", () => showEmptyState(false));
video.addEventListener("error", () => showEmptyState(true));

// Prevent HLS latency drift manually without freezing the player
setInterval(() => {
  if (activeView === "liveView" && liveMode === "hls" && !video.hidden) {
    if (video.buffered && video.buffered.length > 0) {
      const liveEdge = video.buffered.end(video.buffered.length - 1);
      const latency = liveEdge - video.currentTime;
      if (latency > 10) {
        // If we are more than 10 seconds behind, skip forward
        video.currentTime = liveEdge - 2;
      }
    }
  }
}, 5000);

window.addEventListener("resize", renderDetectionOverlay);

recordingEmptyState.classList.remove("is-hidden");
loadFixedPreview();
refreshEsp32Status();
refreshDetections();
setInterval(refreshEsp32Status, 1000);
setInterval(refreshDetections, 500);
setInterval(() => {
  if (activeView === "recordingsView") {
    refreshRecordingsList();
  }
}, 15000);
