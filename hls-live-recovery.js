/* Monitor the existing live HLS video. Recording playback and the recorder are independent. */
class HlsLiveRecovery {
  constructor(video, reload, release, waiting) {
    this.video = video; this.reload = reload; this.release = release; this.waiting = waiting;
    this.active = false; this.destroyed = false; this.failures = 0; this.retryTimer = null;
    this.interval = setInterval(() => this.check(), 1000);
  }
  start() {
    if (this.active || this.destroyed) return;
    this.active = true; this.open();
  }
  open() {
    if (!this.active || this.destroyed) return;
    this.started = performance.now(); this.lastFrame = this.started;
    this.hasFrames = false; this.frames = 0; this.mediaTime = 0;
    this.waiting(true);
    try { this.reload(); } catch (error) { this.retry(String(error)); }
  }
  check() {
    if (!this.active || this.destroyed || this.retryTimer !== null || document.hidden) return;
    const now = performance.now();
    const quality = this.video.getVideoPlaybackQuality?.();
    const frames = quality ? quality.totalVideoFrames - quality.droppedVideoFrames : null;
    const time = this.video.currentTime;
    if (this.video.readyState >= 2 && (frames === null ? time > this.mediaTime : frames > this.frames)) {
      this.hasFrames = true; this.lastFrame = now; this.waiting(false);
      if (now - this.started >= 30000) this.failures = 0;
    }
    this.frames = frames; this.mediaTime = time;
    if ((!this.hasFrames && now - this.started >= 20000)
        || (this.hasFrames && now - this.lastFrame >= 10000)) this.retry("HLS video frames stopped");
  }
  retry(reason) {
    if (!this.active || this.destroyed || this.retryTimer !== null) return;
    const delay = Math.min(30000, 2000 * 2 ** Math.min(this.failures++, 4));
    console.warn(`Reloading existing HLS stream in ${delay / 1000}s: ${reason}`);
    this.waiting(true); this.release();
    this.retryTimer = setTimeout(() => { this.retryTimer = null; this.open(); }, delay);
  }
  stop() {
    const wasActive = this.active;
    this.active = false;
    if (this.retryTimer !== null) clearTimeout(this.retryTimer);
    this.retryTimer = null;
    if (wasActive) this.release();
  }
  destroy() { this.stop(); this.destroyed = true; clearInterval(this.interval); }
}
window.HlsLiveRecovery = HlsLiveRecovery;
