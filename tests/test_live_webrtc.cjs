const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

let now = 0;
let waiting;
let cancelled = false;
const readers = [];
class Reader {
  constructor(conf) {
    this.conf = conf;
    this.pc = { connectionState: 'connected', getStats: async () => new Map() };
    readers.push(this);
  }
  close() { this.closed = true; }
}
const context = {
  window: { MediaMTXWebRTCReader: Reader }, document: { hidden: false },
  performance: { now: () => now }, console,
  setInterval: () => 1, clearInterval: () => { cancelled = true; },
};
vm.createContext(context);
vm.runInContext(fs.readFileSync(path.join(__dirname, '../live-webrtc.js'), 'utf8'), context);
const video = { currentTime: 0, readyState: 0, play: async () => {} };
const player = new context.window.LiveWebRTCPlayer(video, 'http://localhost:8889/pramacam', (v) => { waiting = v; });

async function run() {
  assert.equal(readers[0].conf.url, 'http://localhost:8889/pramacam/whep');
  assert.equal(waiting, true);
  const receiver = { jitterBufferTarget: null };
  const stream = {};
  readers[0].conf.onTrack({ receiver, streams: [stream] });
  assert.equal(receiver.jitterBufferTarget, 0);
  assert.equal(video.srcObject, stream);
  now = 5000;
  await player.check();
  assert.equal(readers.length, 1, 'allow camera/keyframe startup');
  now = 10001;
  await player.check();
  assert.equal(readers.length, 2, 'reconnect a frozen camera after startup');
  assert.equal(readers[0].closed, true);
  readers[0].conf.onTrack({ receiver, streams: ['obsolete'] });
  assert.equal(video.srcObject, null, 'ignore a closed connection callback');

  video.readyState = 4;
  now = 20002;
  video.currentTime = 1;
  await player.check();
  assert.equal(readers.length, 2, 'healthy moving video stays connected');
  assert.equal(waiting, false);

  let emitted = 0;
  readers[1].pc.getStats = async () => {
    emitted += 10;
    return new Map([['v', { id: 'v', type: 'inbound-rtp', kind: 'video',
      jitterBufferEmittedCount: emitted, jitterBufferDelay: emitted * 2.5 }]]);
  };
  for (let i = 0; i < 4; i++) {
    now += 500;
    video.currentTime += 0.5;
    await player.check();
  }
  assert.equal(readers.length, 3, 'three recent high-buffer samples reconnect');
  assert.equal(readers[1].closed, true);
  let shownFrames = 10;
  video.getVideoPlaybackQuality = () => ({ totalVideoFrames: shownFrames, droppedVideoFrames: 0 });
  now += 10001;
  await player.check();
  shownFrames++;
  now += 500;
  await player.check();
  now += 2100;
  video.currentTime += 2.1;
  await player.check();
  assert.equal(readers.length, 4, 'detect frozen pictures even when the media clock advances');
  context.document.hidden = true;
  now += 20000;
  await player.check();
  assert.equal(readers.length, 4, 'do not churn connections in hidden tabs');
  player.close();
  assert.equal(readers[3].closed, true);
  assert.equal(cancelled, true);
  assert.equal(video.srcObject, null);
  console.log('WebRTC preview checks passed');
}
run().catch((error) => { console.error(error); process.exitCode = 1; });
