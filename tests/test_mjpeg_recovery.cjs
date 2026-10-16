const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

let now = 0, id = 1, waiting;
const timers = new Map(), blobs = new Set(), sources = [];
const image = { removeAttribute() {}, hidden: false };
const context = {
  AbortController, Blob, Date,
  window: { addEventListener() {}, removeEventListener() {} }, document: { hidden: false },
  performance: { now: () => now }, console: { warn() {} },
  URL: { createObjectURL() { const key = `blob:${id++}`; blobs.add(key); return key; },
    revokeObjectURL(key) { blobs.delete(key); } },
  fetch: async (url, options) => {
    let source;
    const body = new ReadableStream({ start(controller) { source = controller; } });
    sources.push(source);
    options.signal.addEventListener('abort', () => source.error(new Error('aborted')));
    return { ok: true, body };
  },
  setTimeout(fn, ms) { const key = id++; timers.set(key, { fn, due: now + ms }); return key; },
  clearTimeout(key) { timers.delete(key); },
  setInterval(fn, ms) { const key = id++; timers.set(key, { fn, due: now + ms, interval: ms }); return key; },
  clearInterval(key) { timers.delete(key); },
};
vm.createContext(context);
vm.runInContext(fs.readFileSync(path.join(__dirname, '../mjpeg-recovery.js'), 'utf8'), context);
function advance(ms) {
  const target = now + ms;
  for (;;) {
    const entry = [...timers.entries()].filter(([, value]) => value.due <= target).sort((a, b) => a[1].due - b[1].due)[0];
    if (!entry) break;
    const [key, value] = entry; now = value.due;
    if (value.interval) value.due += value.interval; else timers.delete(key);
    value.fn();
  }
  now = target;
}
const tick = () => new Promise((resolve) => setImmediate(resolve));
const jpeg = new Uint8Array([255, 216, 1, 2, 255, 217]);

async function run() {
  const parser = new context.window.JpegFrames();
  assert.equal(parser.feed(new Uint8Array([9, 255])), null);
  assert.equal(parser.feed(new Uint8Array([216, 1, 2, 255])), null);
  assert.deepEqual([...parser.feed(new Uint8Array([217]))], [...jpeg], 'parse markers split across chunks');
  const two = new Uint8Array([...jpeg, ...jpeg.slice(0, 2), 3, ...jpeg.slice(-2)]);
  assert.deepEqual([...parser.feed(two)], [255, 216, 3, 255, 217], 'drop old complete frames when newer ones are ready');
  assert.throws(() => parser.feed(new Uint8Array(4 * 1024 * 1024 + 1)), /oversized/);
  const player = new context.window.RecoveringMjpeg(image, '/live.mjpg', (value) => { waiting = value; });
  player.start(); await tick();
  sources.at(-1).enqueue(jpeg); await tick();
  assert.equal(blobs.size, 1);
  image.onload();
  assert.equal(waiting, false);
  assert.equal(blobs.size, 0, 'release each decoded blob URL');
  advance(10000); await tick();
  assert.equal(waiting, true, 'a stalled source triggers reconnection');
  advance(2000); await tick();
  assert.equal(sources.length, 2);
  sources.at(-1).enqueue(jpeg); await tick(); image.onload();
  assert.equal(waiting, false, 'the new source restores the picture');
  for (let i = 0; i < 100; i++) {
    const previous = sources.length;
    sources.at(-1).close(); await tick();
    for (let elapsed = 0; elapsed < 40000 && sources.length === previous; elapsed += 1000) {
      advance(1000); await tick();
    }
    assert.equal(sources.length, previous + 1, 'each ended stream reconnects');
    assert.ok(timers.size <= 2);
    assert.ok(blobs.size <= 1);
  }
  player.stop(); await tick();
  const attempts = sources.length;
  advance(3600000); await tick();
  assert.equal(sources.length, attempts);
  player.destroy();
  assert.equal(blobs.size, 0);
  assert.equal(timers.size, 0);
  console.log('MJPEG recovery checks passed: fragmented frames, stalled streams, 100 disconnects and bounded cleanup');
}
run().catch((error) => { console.error(error); process.exitCode = 1; });
