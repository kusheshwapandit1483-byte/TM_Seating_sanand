/* Live preview only. Occupancy, relays and recording do not use this player. */
class LiveWebRTCPlayer {
  constructor(video, url, onWaiting) {
    this.video = video;
    this.url = url;
    this.onWaiting = onWaiting;
    this.targetSeconds = 2;
    this.reader = null;
    this.lastTime = -1;
    this.lastFrames = -1;
    this.lastProgress = performance.now();
    this.startedAt = this.lastProgress;
    this.previousStats = new Map();
    this.slowSamples = 0;
    this.checking = false;
    this.closed = false;
    this.connect();
    this.timer = setInterval(() => this.check(), 500);
  }

  connect() {
    if (this.closed) return;
    if (this.reader) this.reader.close();
    this.video.srcObject = null;
    this.onWaiting(true);
    this.startedAt = performance.now();
    this.lastProgress = this.startedAt;
    this.lastTime = -1;
    this.lastFrames = -1;
    this.previousStats.clear();
    this.slowSamples = 0;
    const reader = new window.MediaMTXWebRTCReader({
      url: `${this.url.replace(/\/$/, "")}/whep`,
      onError: (error) => {
        if (this.closed || this.reader !== reader) return;
        console.warn("Live preview:", error);
        this.onWaiting(true);
      },
      onTrack: (event) => {
        if (this.closed || this.reader !== reader) return;
        // This is a preference, not an end-to-end latency guarantee.
        try {
          if ("jitterBufferTarget" in event.receiver) event.receiver.jitterBufferTarget = 0;
        } catch (error) {
          console.debug("Receiver uses automatic buffering", error);
        }
        this.video.srcObject = event.streams[0];
        this.video.play().catch(() => this.onWaiting(true));
      },
    });
    this.reader = reader;
  }

  async check() {
    if (this.closed || this.checking || document.hidden) return;
    const reader = this.reader;
    const now = performance.now();
    const quality = this.video.getVideoPlaybackQuality?.();
    const frames = quality ? quality.totalVideoFrames - quality.droppedVideoFrames : null;
    const advanced = frames === null ? this.video.currentTime !== this.lastTime : frames > this.lastFrames;
    if (advanced && this.video.readyState >= 2) {
      this.lastTime = this.video.currentTime;
      this.lastFrames = frames;
      this.lastProgress = now;
      this.onWaiting(false);
    }
    // Allow handshake/keyframe startup; do not reconnect continuously on a dead camera.
    if (now - this.startedAt < 10000) return;
    if (now - this.lastProgress > this.targetSeconds * 1000) {
      this.connect();
      return;
    }
    const pc = reader && reader.pc;
    if (!pc || pc.connectionState !== "connected") return;
    this.checking = true;
    try {
      const stats = await pc.getStats();
      if (this.closed || this.reader !== reader) return;
      let slow = false;
      stats.forEach((stat) => {
        if (stat.type !== "inbound-rtp" || (stat.kind || stat.mediaType) !== "video") return;
        const old = this.previousStats.get(stat.id);
        this.previousStats.set(stat.id, stat);
        if (!old) return;
        const frames = stat.jitterBufferEmittedCount - old.jitterBufferEmittedCount;
        const delay = stat.jitterBufferDelay - old.jitterBufferDelay;
        // Recent receiver buffering only; camera/network delay is not measured here.
        if (frames > 0 && delay / frames > this.targetSeconds) slow = true;
      });
      this.slowSamples = slow ? this.slowSamples + 1 : 0;
      if (this.slowSamples >= 3) this.connect();
    } catch (error) {
      console.debug("Live preview statistics unavailable", error);
    } finally {
      this.checking = false;
    }
  }

  close() {
    this.closed = true;
    clearInterval(this.timer);
    if (this.reader) this.reader.close();
    this.video.srcObject = null;
  }
}

window.LiveWebRTCPlayer = LiveWebRTCPlayer;
