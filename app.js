const streamInput = document.querySelector("#streamUrl");
const loadButton = document.querySelector("#loadStream");
const demoButton = document.querySelector("#demoStream");
const clearButton = document.querySelector("#clearStream");
const video = document.querySelector("#cameraVideo");
const frame = document.querySelector("#cameraFrame");
const emptyState = document.querySelector("#emptyState");
const connectionDot = document.querySelector("#connectionDot");
const connectionText = document.querySelector("#connectionText");
const clock = document.querySelector("#clock");
const playPause = document.querySelector("#playPause");
const muteToggle = document.querySelector("#muteToggle");
const fullscreenButton = document.querySelector("#fullscreenButton");
const speedButton = document.querySelector("#speedButton");
const qualityButtons = document.querySelectorAll(".quality-button");
const navButtons = document.querySelectorAll("[data-view-target]");
const viewPanels = document.querySelectorAll(".view-panel");
const pageTitle = document.querySelector("#pageTitle");
const recordingStatus = document.querySelector("#recordingStatus");
const retentionDays = document.querySelector("#retentionDays");
const openRecordings = document.querySelector("#openRecordings");
const refreshRecordings = document.querySelector("#refreshRecordings");
const backToLive = document.querySelector("#backToLive");
const recordingSpeedButton = document.querySelector("#recordingSpeedButton");
const recordingsList = document.querySelector("#recordingsList");
const recordingPlayer = document.querySelector("#recordingPlayer");
const recordingEmptyState = document.querySelector("#recordingEmptyState");

const storageKey = "tm-camera-preview-url";
const directVideoExtensions = [".mp4", ".webm", ".ogg", ".mov"];
let hlsPlayer = null;
let demoStream = null;
let demoAnimation = null;
let activeView = "liveView";
const previewSpeeds = [1, 1.25, 1.5, 2];
let previewSpeedIndex = 0;
const recordingSpeeds = [1, 1.25, 1.5, 2, 4];
let recordingSpeedIndex = 0;



function applyRecordingSpeed() {
  const speed = recordingSpeeds[recordingSpeedIndex];
  recordingPlayer.playbackRate = speed;
  recordingSpeedButton.textContent = `Speed ${speed}x`;
  recordingSpeedButton.title = `Recorded video speed ${speed}x`;
}

function cycleRecordingSpeed() {
  recordingSpeedIndex = (recordingSpeedIndex + 1) % recordingSpeeds.length;
  applyRecordingSpeed();
}
function applyPreviewSpeed() {
  const speed = previewSpeeds[previewSpeedIndex];
  speedButton.textContent = `${speed}x`;
  speedButton.title = `Preview speed ${speed}x`;

  if (!video.hidden) {
    video.playbackRate = speed;
  } else {
    frame.contentWindow?.postMessage({ action: "set-speed", speed }, "*");
  }
}

function cyclePreviewSpeed() {
  previewSpeedIndex = (previewSpeedIndex + 1) % previewSpeeds.length;
  applyPreviewSpeed();
}
function setStatus(state, text) {
  connectionDot.classList.toggle("is-live", state === "live");
  connectionDot.classList.toggle("is-error", state === "error");
  connectionText.textContent = text;
}

function updateClock() {
  const now = new Date();
  clock.dateTime = now.toISOString();
  clock.textContent = now.toLocaleString([], {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    day: "2-digit",
    month: "short",
  });
}

function showEmptyState(show) {
  emptyState.classList.toggle("is-hidden", !show);
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

function destroyHls() {
  if (hlsPlayer) {
    hlsPlayer.destroy();
    hlsPlayer = null;
  }
}

function stopDemo() {
  if (demoAnimation) {
    cancelAnimationFrame(demoAnimation);
    demoAnimation = null;
  }

  if (demoStream) {
    demoStream.getTracks().forEach((track) => track.stop());
    demoStream = null;
  }
}

function resetPreview() {
  destroyHls();
  stopDemo();
  video.pause();
  video.removeAttribute("src");
  video.srcObject = null;
  video.load();
  frame.removeAttribute("src");
  frame.hidden = true;
  video.hidden = false;
}

function playVideoWhenReady() {
  video.play().catch(() => {
    setStatus("waiting", "Press play to start");
  });
}

function loadHls(url) {
  video.hidden = false;
  frame.hidden = true;
  setStatus("waiting", "Connecting HLS");

  if (video.canPlayType("application/vnd.apple.mpegurl")) {
    video.src = url;
    video.load();
    applyPreviewSpeed();
    playVideoWhenReady();
    return;
  }

  if (window.Hls && window.Hls.isSupported()) {
    hlsPlayer = new window.Hls({
      lowLatencyMode: true,
      backBufferLength: 30,
    });
    hlsPlayer.loadSource(url);
    hlsPlayer.attachMedia(video);
    hlsPlayer.on(window.Hls.Events.MANIFEST_PARSED, () => {
      applyPreviewSpeed();
      playVideoWhenReady();
    });
    hlsPlayer.on(window.Hls.Events.ERROR, () => {
      setStatus("error", "HLS stream unavailable");
    });
    return;
  }

  setStatus("error", "HLS player not loaded");
}

function loadDirectVideo(url) {
  video.src = url;
  video.hidden = false;
  frame.hidden = true;
  video.load();
  applyPreviewSpeed();
  setStatus("waiting", "Connecting");
  playVideoWhenReady();
}

function loadPreviewPage(url) {
  frame.src = url;
  frame.hidden = false;
  video.hidden = true;
  setStatus("waiting", "Loading preview page");
}

function loadDemoPreview() {
  resetPreview();

  const canvas = document.createElement("canvas");
  canvas.width = 1280;
  canvas.height = 720;
  const ctx = canvas.getContext("2d");
  const startedAt = Date.now();

  function draw() {
    const elapsed = (Date.now() - startedAt) / 1000;
    const sweep = Math.floor((elapsed * 90) % canvas.width);

    ctx.fillStyle = "#05070a";
    ctx.fillRect(0, 0, canvas.width, canvas.height);

    ctx.fillStyle = "#101923";
    for (let x = -120; x < canvas.width; x += 160) {
      ctx.fillRect(x + sweep, 0, 80, canvas.height);
    }

    ctx.fillStyle = "#2fbf9b";
    ctx.fillRect(0, 0, canvas.width, 58);
    ctx.fillStyle = "#061410";
    ctx.font = "700 26px system-ui, sans-serif";
    ctx.fillText("TM CAMERA MONITOR - DEMO PREVIEW", 28, 38);

    ctx.fillStyle = "#f2f5f7";
    ctx.font = "700 52px system-ui, sans-serif";
    ctx.fillText("Preview player is working", 84, 330);
    ctx.font = "26px system-ui, sans-serif";
    ctx.fillText("Connect Raspberry Pi RTSP bridge for real camera footage", 86, 382);
    ctx.fillText(new Date().toLocaleString(), 86, 430);

    ctx.strokeStyle = "#2fbf9b";
    ctx.lineWidth = 4;
    ctx.strokeRect(44, 92, canvas.width - 88, canvas.height - 136);

    demoAnimation = requestAnimationFrame(draw);
  }

  draw();
  demoStream = canvas.captureStream(30);
  video.srcObject = demoStream;
  video.hidden = false;
  applyPreviewSpeed();
  frame.hidden = true;
  showEmptyState(false);
  setStatus("live", "Demo preview");
  playVideoWhenReady();
}

function loadStream(url) {
  const trimmedUrl = url.trim();

  resetPreview();

  if (!trimmedUrl) {
    setStatus("waiting", "Waiting for stream");
    showEmptyState(true);
    return;
  }

  if (trimmedUrl.startsWith("rtsp://")) {
    setStatus("error", "RTSP needs a browser bridge");
    showEmptyState(false);
    return;
  }

  localStorage.setItem(storageKey, trimmedUrl);
  showEmptyState(false);

  if (isHlsUrl(trimmedUrl)) {
    loadHls(trimmedUrl);
    return;
  }

  if (isDirectVideoUrl(trimmedUrl)) {
    loadDirectVideo(trimmedUrl);
    return;
  }

  loadPreviewPage(trimmedUrl);
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


function formatDuration(seconds) {
  if (seconds % 3600 === 0) {
    const hours = seconds / 3600;
    return `${hours} hour${hours === 1 ? "" : "s"}`;
  }
  if (seconds % 60 === 0) {
    const minutes = seconds / 60;
    return `${minutes} minute${minutes === 1 ? "" : "s"}`;
  }
  return `${seconds} seconds`;
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

async function refreshRecordingStatus() {
  try {
    const status = await apiRequest("/api/recording/status");
    if (status.running) {
      recordingStatus.textContent = `Recording automatically. Clips every ${formatDuration(status.segmentSeconds)}. Retention ${status.retentionDays} day${status.retentionDays === 1 ? "" : "s"}.`;
    } else {
      recordingStatus.textContent = `Recorder is reconnecting automatically. Retention ${status.retentionDays} day${status.retentionDays === 1 ? "" : "s"}.`;
    }
  } catch (error) {
    recordingStatus.textContent = "Run python server.py to enable automatic recording.";
  }
}



async function loadSettings() {
  try {
    const settings = await apiRequest("/api/settings");
    retentionDays.value = String(settings.retentionDays || 2);
  } catch (error) {
    retentionDays.value = "2";
  }
}

async function saveRetentionDays() {
  const selectedDays = Math.min(2, Math.max(1, Number(retentionDays.value) || 2));
  retentionDays.value = String(selectedDays);
  try {
    const result = await apiRequest("/api/settings", {
      method: "POST",
      body: JSON.stringify({ retentionDays: selectedDays }),
    });
    const status = result.status;
    if (status) {
      recordingStatus.textContent = `Recording automatically. Clips every ${formatDuration(status.segmentSeconds)}. Retention ${status.retentionDays} day${status.retentionDays === 1 ? "" : "s"}.`;
    }
    await refreshRecordingsList();
  } catch (error) {
    recordingStatus.textContent = "Could not save retention setting. Check server connection.";
  }
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

function playRecordingInBrowser(clip) {
  recordingPlayer.src = clip.url;
  recordingPlayer.load();
  applyRecordingSpeed();
  recordingEmptyState.classList.add("is-hidden");
  recordingPlayer.play().catch(() => undefined);
}

async function openRecordingInVlc(clip) {
  recordingPlayer.pause();
  recordingPlayer.removeAttribute("src");
  recordingPlayer.load();
  showRecordingMessage("Opening in VLC", "Close VLC to return to this webpage.");

  try {
    await apiRequest("/api/recordings/open-vlc", {
      method: "POST",
      body: JSON.stringify({ name: clip.name }),
    });
    showRecordingMessage("Opened in VLC", "Use VLC timeline controls to jump to any time, then close VLC to return here.");
  } catch (error) {
    showRecordingMessage("VLC did not open", "Using browser playback as fallback. Install VLC on the Raspberry Pi if needed.");
    playRecordingInBrowser(clip);
  }
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
      clipMeta.textContent = `${formatDate(clip.modifiedAt)} - ${formatBytes(clip.sizeBytes)} - open in VLC`;

      button.append(clipName, clipMeta);
      button.addEventListener("click", () => {
        document.querySelectorAll(".clip-button").forEach((item) => item.classList.remove("is-selected"));
        button.classList.add("is-selected");
        openRecordingInVlc(clip);
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
  pageTitle.textContent = targetId === "recordingsView" ? "Recordings" : "Live View";

  if (targetId === "recordingsView") {
    refreshRecordingsList();
  }
}

loadButton.addEventListener("click", () => loadStream(streamInput.value));
demoButton.addEventListener("click", loadDemoPreview);
openRecordings.addEventListener("click", () => showView("recordingsView"));
backToLive.addEventListener("click", () => showView("liveView"));
recordingSpeedButton.addEventListener("click", cycleRecordingSpeed);
refreshRecordings.addEventListener("click", refreshRecordingsList);
retentionDays.addEventListener("change", saveRetentionDays);

navButtons.forEach((button) => {
  button.addEventListener("click", () => showView(button.dataset.viewTarget));
});

clearButton.addEventListener("click", () => {
  streamInput.value = "";
  resetPreview();
  localStorage.removeItem(storageKey);
  showEmptyState(true);
  setStatus("waiting", "Waiting for stream");
});

streamInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter") {
    loadStream(streamInput.value);
  }
});

frame.addEventListener("load", () => {
  if (!frame.hidden) {
    setStatus("live", "Preview page loaded");
  }
});

video.addEventListener("playing", () => {
  if (demoStream) {
    setStatus("live", "Demo preview");
  } else {
    setStatus("live", "Live preview");
  }
  playPause.textContent = "Pause";
});

video.addEventListener("pause", () => {
  playPause.textContent = "Play";
});

video.addEventListener("error", () => {
  setStatus("error", "Preview unavailable");
});

playPause.addEventListener("click", () => {
  if (video.hidden) {
    frame.contentWindow?.postMessage({ action: "toggle-play" }, "*");
    return;
  }

  if (video.paused) {
    video.play();
  } else {
    video.pause();
  }
});

muteToggle.addEventListener("click", () => {
  if (video.hidden) {
    frame.contentWindow?.postMessage({ action: "toggle-mute" }, "*");
    return;
  }

  video.muted = !video.muted;
  muteToggle.textContent = video.muted ? "Mute" : "Sound";
});

speedButton.addEventListener("click", cyclePreviewSpeed);

fullscreenButton.addEventListener("click", () => {
  const target = document.querySelector(".video-stage");
  if (document.fullscreenElement) {
    document.exitFullscreen();
  } else {
    target.requestFullscreen();
  }
});

qualityButtons.forEach((button) => {
  button.addEventListener("click", () => {
    qualityButtons.forEach((item) => item.classList.remove("is-selected"));
    button.classList.add("is-selected");
  });
});

const savedUrl = localStorage.getItem(storageKey);
if (savedUrl) {
  streamInput.value = savedUrl;
  loadStream(savedUrl);
}

recordingEmptyState.classList.remove("is-hidden");
updateClock();
refreshRecordingStatus();
setInterval(updateClock, 1000);
setInterval(refreshRecordingStatus, 5000);
setInterval(() => {
  if (activeView === "recordingsView") {
    refreshRecordingsList();
  }
}, 15000);
