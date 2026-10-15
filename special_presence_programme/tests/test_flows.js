const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const base = path.resolve(__dirname, '..');
const prod = JSON.parse(fs.readFileSync(path.join(base, 'production_flow.json'), 'utf8'));
const maint = JSON.parse(fs.readFileSync(path.join(base, 'maintainance_flow.json'), 'utf8'));
const original = JSON.parse(fs.readFileSync(path.join(base, '..', 'production_flow.json'), 'utf8'));
const get = (nodes, id) => nodes.find(n => n.id === id);
assert.equal(get(prod, 'prod_track').func, get(original, 'prod_track').func,
    'Original camera counting function must remain unchanged');
for (const nodes of [prod, maint]) {
    const ids = new Set(nodes.map(n => n.id));
    assert.equal(ids.size, nodes.length);
    for (const n of nodes) {
        for (const targets of n.wires || []) for (const target of targets) assert(ids.has(target), target);
        if (n.type === 'function') new Function('msg', 'context', 'flow', 'global', 'node', n.func);
        if (n.type === 'ui-template') {
            const script = n.format.match(/<script>([\s\S]*?)<\/script>/)[1];
            new Function(script.replace('export default', 'return'));
        }
    }
}
const run = new Function('msg', 'context', 'flow', 'global', 'node', 'Date', get(prod, 'prod_presence').func);
function harness(extra = {}) {
    let now = 10000;
    const store = new Map(), f = new Map([['device_id', 'TM_R1_01'], ['door_label', 'E1']]);
    const cfg = { polygon: [[100,100],[300,100],[300,300],[100,300]], masks: [], ...extra };
    const context = { get: k => store.get(k), set: (k,v) => store.set(k,v) };
    const flow = { get: k => f.get(k), set: (k,v) => f.set(k,v) };
    const global = { get: k => k === 'roi_config' ? cfg : k === 'system_ready' ? true : undefined };
    const output = [];
    function step(boxes, delta = 100, tick = false) {
        now += delta;
        const msg = tick ? { topic: '__presence_tick' } : { payload: { boxes, labels: ['person'] } };
        const out = run(msg, context, flow, global, { status() {} }, { now: () => now });
        if (out) output.push(JSON.parse(out.payload));
        return store.get('presence');
    }
    return { step, store, cfg, output };
}
const interiorPerson = [[400,360,40,80,0.9,0]]; // Foot (400,400), outside door.
const doorPerson = [[200,160,40,80,0.9,0]]; // Foot (200,200), inside door.
let h = harness();
for (let i=0;i<21;i++) h.step(interiorPerson);
assert.equal(h.store.get('presence').present, false, 'Exactly 2000ms is not MORE than 2s');
h.step(interiorPerson);
assert.equal(h.store.get('presence').present, true);
for (let i=0;i<40;i++) h.step(doorPerson);
assert.equal(h.store.get('presence').present, true, 'Moving into door ROI must not clear latch');
assert(h.output.at(-1).confirmed_ms > 2000,
    'First ESP message may arrive after person moved to door; confirmation evidence must survive');
assert.equal(h.output.at(-1).continuous_ms, 0);
for (let i=0;i<20;i++) h.step([]);
assert.equal(h.store.get('presence').present, true, 'Clear period has not completed');
h.step([]);
assert.equal(h.store.get('presence').state, 'clear');
assert.equal(h.store.get('presence').present, false);
console.log('PASS: >2s confirmation, door holds latch, 2s clear confirmation');
h = harness();
for (let i=0;i<15;i++) h.step(interiorPerson);
h.step([]);
for (let i=0;i<15;i++) h.step(interiorPerson);
assert.equal(h.store.get('presence').present, false);
h.step(interiorPerson, 900);
assert.equal(h.store.get('presence').candidateSince, h.store.get('presence').lastFrame);
assert.equal(h.store.get('presence').present, false);
console.log('PASS: empty frame and excessive frame gap break continuous detection');
h = harness();
for (let i=0;i<22;i++) h.step(interiorPerson);
h.step(undefined, 1100, true);
assert.equal(h.output.at(-1).valid, false);
assert.equal(h.output.at(-1).state, 'unknown');
assert.equal(h.store.get('presence').present, true, 'Stale stream cannot clear existing latch');
assert.equal(h.output.at(-1).continuous_ms, 0);
console.log('PASS: stale stream publishes unknown while retaining internal latch');
h = harness({ presence_polygon: [[350,350],[500,350],[500,500],[350,500]] });
for(let i=0;i<30;i++) h.step([[550,360,40,80,0.9,0]]);
assert.equal(h.store.get('presence').state, 'clear');
for(let i=0;i<22;i++) h.step(interiorPerson);
assert.equal(h.store.get('presence').present, true);
console.log('PASS: optional interior polygon excludes outside corridor');
h = harness({ masks: [[[350,350],[500,350],[500,500],[350,500]]] });
for(let i=0;i<30;i++) h.step(interiorPerson);
assert.equal(h.store.get('presence').state, 'clear');
h = harness();
for(let i=0;i<30;i++) h.step([[400,360,40,80,0.3,0]]);
assert.equal(h.store.get('presence').present, false);
for(let i=0;i<30;i++) h.step([[400,360,40,80,0,0.9]]);
assert.equal(h.store.get('presence').present, true, 'Alternate class/score format');
assert(h.output.every(p => Buffer.byteLength(JSON.stringify(p)) <= 512));
console.log('PASS: masks, confidence filter, alternate payload layout, payload size');
h = harness();
for(let i=0;i<15;i++) h.step(interiorPerson);
for(let i=0;i<30;i++) h.step(undefined, 100, true);
assert.equal(h.store.get('presence').present, false, 'Timer ticks cannot manufacture live confirmation');
assert.equal(h.output.at(-1).valid, false);
console.log('PASS: clock ticks without inference cannot confirm presence');
h = harness();
for(let i=0;i<15;i++) h.step(interiorPerson);
h.step(undefined);
assert.equal(h.store.get('presence').state, 'unknown');
assert.equal(h.store.get('presence').lastFrame, 0);
for(let i=0;i<15;i++) h.step(interiorPerson);
assert.equal(h.store.get('presence').present, false);
console.log('PASS: malformed inference breaks continuity and cannot declare clear');
const backend = new Function('msg', 'global', 'node', get(maint,'roi_backend').func);
const roi = {polygon:[[0,0],[100,0],[100,100]], count_edge_index:0,
    presence_polygon:[[200,200],[400,200],[400,400]], masks:[], entry_area:'outside'};
const saved = backend({topic:'save_roi',payload:roi}, {set(){}}, {status(){}});
assert.deepEqual(JSON.parse(saved[1].payload).presence_polygon, roi.presence_polygon);
const apply = new Function('msg','global','node',get(prod,'prod_apply_roi').func);
let applied;
apply({payload:saved[1].payload}, {set:(key,v)=>{if(key==='roi_config')applied=v;}}, {status(){},warn(){}});
assert.deepEqual(applied.presence_polygon,roi.presence_polygon);
console.log('PASS: maintenance save and production load preserve interior polygon');
console.log('PASS: both JSONs, all functions/templates, node wiring, unchanged original counting');
