const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

function fixture(quality = true) {
  let now = 0, serial = 0, decoded = 0, dropped = 0, opens = 0, releases = 0;
  const timers = new Map();
  const video = { currentTime: 0, readyState: 4 };
  if (quality) video.getVideoPlaybackQuality = () => ({ totalVideoFrames: decoded, droppedVideoFrames: dropped });
  const context = { window: {}, document: { hidden: false }, performance: { now: () => now }, console: { warn() {} },
    setInterval: (fn, delay) => { const id = ++serial; timers.set(id, { fn, due: now + delay, delay }); return id; },
    setTimeout: (fn, delay) => { const id = ++serial; timers.set(id, { fn, due: now + delay }); return id; },
    clearTimeout: (id) => timers.delete(id), clearInterval: (id) => timers.delete(id),
  };
  vm.createContext(context);
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../hls-live-recovery.js'), 'utf8'), context);
  const player = new context.window.HlsLiveRecovery(video, () => { opens++; decoded = 0; dropped = 0; video.currentTime = 0; },
    () => { releases++; }, () => {});
  function advance(ms) {
    const end = now + ms;
    for (;;) {
      const next = [...timers].filter(([, timer]) => timer.due <= end).sort((a, b) => a[1].due - b[1].due)[0];
      if (!next) break;
      const [id, timer] = next; now = timer.due;
      if (timer.delay) timer.due += timer.delay; else timers.delete(id);
      timer.fn();
    }
    now = end;
  }
  return { player, video, timers, context, advance, get opens() { return opens; }, get releases() { return releases; },
    frame() { decoded++; video.currentTime++; }, drop() { decoded++; dropped++; video.currentTime++; } };
}

{
  const f = fixture(); f.player.start(); f.player.start(); assert.equal(f.opens, 1);
  for (let i = 0; i < 72 * 3600; i++) { f.frame(); f.advance(1000); }
  assert.equal(f.opens, 1); assert.equal(f.releases, 0);
  f.advance(10000); assert.equal(f.releases, 1); assert.equal(f.opens, 1);
  f.advance(2000); assert.equal(f.opens, 2);
  f.player.stop(); const opens = f.opens; f.advance(60000); assert.equal(f.opens, opens);
  f.player.start(); assert.equal(f.opens, opens + 1);
  f.player.destroy(); assert.equal(f.timers.size, 0);
}
{
  const f = fixture(); f.player.start();
  for (let i = 0; i < 100; i++) {
    const opens = f.opens;
    for (let seconds = 0; f.player.retryTimer === null && seconds < 21; seconds++) f.advance(1000);
    assert.notEqual(f.player.retryTimer, null);
    f.player.retry('duplicate'); assert.equal(f.timers.size, 2);
    for (let seconds = 0; f.opens === opens && seconds < 31; seconds++) f.advance(1000);
    assert.equal(f.opens, opens + 1); assert.equal(f.timers.size, 1);
  }
  f.player.destroy(); assert.equal(f.timers.size, 0);
}
{
  const f = fixture(); f.player.start(); f.frame(); f.advance(1000);
  for (let i = 0; i < 10; i++) { f.drop(); f.advance(1000); }
  assert.equal(f.releases, 1); // Advancing audio/media time with dropped video is not a healthy picture.
  f.player.destroy();
}
{
  const f = fixture(false); f.player.start(); f.frame(); f.advance(1000);
  assert.equal(f.player.hasFrames, true);
  f.context.document.hidden = true; f.advance(60000); assert.equal(f.releases, 0);
  f.context.document.hidden = false; f.advance(1000); assert.equal(f.releases, 1);
  f.player.stop(); assert.equal(f.timers.size, 1); f.player.destroy();
}
// Exercise the actual dashboard's HLS wiring and release only its live element.
{
  const elements = new Map(), players = [];
  function element(key) {
    if (!elements.has(key)) elements.set(key, { hidden: false, dataset: {},
      classList: { remove() {}, add() {}, toggle() {} }, addEventListener() {}, removeAttribute() {}, pause() {}, load() {} });
    return elements.get(key);
  }
  const context = { URL, URLSearchParams, console,
    document: { hidden: false, querySelector: element, querySelectorAll: () => [], addEventListener() {} },
    window: { location: { hostname: '127.0.0.1', search: '?live=hls&boot=123' }, addEventListener() {},
      HlsLiveRecovery: class {
        constructor(video, reload, release) { this.video = video; this.reload = reload; this.release = release; this.starts = 0; this.stops = 0; players.push(this); }
        start() { this.starts++; }
        stop() { this.stops++; this.release(); }
      } }, fetch: () => new Promise(() => {}), setInterval() {},
  };
  vm.createContext(context); vm.runInContext(fs.readFileSync(path.join(__dirname, '../app.js'), 'utf8'), context);
  assert.equal(players.length, 1); assert.equal(players[0].video, elements.get('#cameraVideo'));
  vm.runInContext('showView("recordingsView")', context); assert.equal(players[0].stops, 1);
  vm.runInContext('showView("liveView")', context); assert.equal(players[0].starts, 2);
  assert.equal(players.length, 1);
}
console.log('HLS recovery tests passed: 72 simulated healthy hours, frozen/dropped frames, retries, visibility, cleanup and dashboard navigation');
