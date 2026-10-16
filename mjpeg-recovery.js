/* Decode the live multipart stream with bounded buffering and frame supervision. */
class JpegFrames {
  constructor() { this.buffer = new Uint8Array(); }
  static find(bytes, first, second, start = 0) {
    for (let i = start; i < bytes.length - 1; i++) {
      if (bytes[i] === first && bytes[i + 1] === second) return i;
    }
    return -1;
  }
  feed(chunk) {
    if (this.buffer.length + chunk.length > 4 * 1024 * 1024) throw new Error("oversized JPEG stream");
    const joined = new Uint8Array(this.buffer.length + chunk.length);
    joined.set(this.buffer); joined.set(chunk, this.buffer.length);
    this.buffer = joined;
    let latest = null;
    for (;;) {
      const start = JpegFrames.find(this.buffer, 255, 216);
      if (start < 0) { this.buffer = this.buffer.slice(-1); break; }
      const end = JpegFrames.find(this.buffer, 255, 217, start + 2);
      if (end < 0) { this.buffer = this.buffer.slice(start); break; }
      latest = this.buffer.slice(start, end + 2);
      this.buffer = this.buffer.slice(end + 2);
    }
    return latest;
  }
}

class RecoveringMjpeg {
  constructor(image, url, onWaiting) {
    this.image = image; this.url = url; this.onWaiting = onWaiting;
    this.active = false; this.destroyed = false; this.generation = 0;
    this.failures = 0; this.retryTimer = null; this.controller = null; this.objectUrl = null;
    this.interval = setInterval(() => this.check(), 1000);
    this.onOnline = () => { if (this.active) this.connect(); };
    window.addEventListener("online", this.onOnline);
  }
  clear() {
    this.generation++;
    if (this.retryTimer !== null) clearTimeout(this.retryTimer);
    this.retryTimer = null;
    if (this.controller) this.controller.abort();
    this.controller = null;
    this.image.onload = null; this.image.onerror = null;
    this.image.removeAttribute("src");
    if (this.objectUrl) URL.revokeObjectURL(this.objectUrl);
    this.objectUrl = null; this.pendingFrame = false;
  }
  start() {
    if (this.active || this.destroyed) return;
    this.active = true; this.connect();
  }
  async connect() {
    if (!this.active || this.destroyed) return;
    this.clear();
    const generation = this.generation;
    const controller = new AbortController();
    this.controller = controller;
    this.startedAt = performance.now(); this.lastFrameAt = this.startedAt; this.hasFrames = false;
    this.onWaiting(true);
    let reader = null;
    try {
      const response = await fetch(`${this.url}${this.url.includes("?") ? "&" : "?"}t=${Date.now()}`, {
        cache: "no-store", signal: controller.signal,
      });
      if (!response.ok || !response.body) throw new Error("preview source unavailable");
      reader = response.body.getReader();
      const frames = new JpegFrames();
      while (this.active && generation === this.generation) {
        const { value, done } = await reader.read();
        if (!this.active || generation !== this.generation) break;
        if (done) throw new Error("preview stream ended");
        const frame = frames.feed(value);
        if (frame && !this.pendingFrame) {
          this.pendingFrame = true;
          const url = URL.createObjectURL(new Blob([frame], { type: "image/jpeg" }));
          this.objectUrl = url;
          this.image.onload = () => {
            if (generation !== this.generation) return;
            URL.revokeObjectURL(url); this.objectUrl = null; this.pendingFrame = false;
            this.lastFrameAt = performance.now(); this.hasFrames = true;
            if (this.lastFrameAt - this.startedAt >= 30000) this.failures = 0;
            this.onWaiting(false);
          };
          this.image.onerror = () => { if (generation === this.generation) this.retry("JPEG decode failed"); };
          this.image.src = url;
        }
      }
    } catch (error) {
      if (this.active && generation === this.generation) this.retry(String(error));
    } finally {
      if (reader) reader.cancel().catch(() => {});
    }
  }
  retry(reason) {
    if (!this.active || this.destroyed || this.retryTimer !== null) return;
    this.clear(); this.onWaiting(true);
    const delay = Math.min(30000, 2000 * (2 ** Math.min(this.failures, 4)));
    this.failures++;
    console.warn(`MJPEG preview reconnecting in ${delay / 1000}s: ${reason}`);
    this.retryTimer = setTimeout(() => { this.retryTimer = null; this.connect(); }, delay);
  }
  check() {
    if (!this.active || this.destroyed || this.retryTimer !== null || document.hidden) return;
    const now = performance.now();
    if ((!this.hasFrames && now - this.startedAt >= 20000)
        || (this.hasFrames && now - this.lastFrameAt >= 10000)) this.retry("video frames stopped");
  }
  stop() { this.active = false; this.clear(); }
  destroy() {
    this.stop(); this.destroyed = true; clearInterval(this.interval);
    window.removeEventListener("online", this.onOnline);
  }
}
window.JpegFrames = JpegFrames;
window.RecoveringMjpeg = RecoveringMjpeg;
