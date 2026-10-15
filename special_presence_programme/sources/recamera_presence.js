// Runs beside Track + Count. Every camera publishes; ESP32 selects the inside ID.
const CONFIRM_MS = 2000;
const CLEAR_MS = 2000;
const MAX_FRAME_GAP_MS = 750;
const STALE_MS = 1000;
const PUBLISH_MS = 500;
const MIN_CONFIDENCE = 0.45;
const now = Date.now();
let s = context.get('presence') || {
    lastFrame: 0, candidateSince: null, clearSince: null,
    present: false, confirmedMs: 0, state: 'unknown', candidates: 0, blockers: 0,
    lastPublish: 0, configKey: ''
};
const cfg = global.get('roi_config');
const deviceId = flow.get('device_id') || 'unknown';
const ready = !!global.get('system_ready') && deviceId !== 'unknown' &&
    cfg && Array.isArray(cfg.polygon) && cfg.polygon.length >= 3;
function inPoly(pt, p) {
    let inside = false;
    for (let i = 0, j = p.length - 1; i < p.length; j = i++) {
        const a = p[i], b = p[j];
        if (((a[1] > pt.y) !== (b[1] > pt.y)) &&
            pt.x < (b[0] - a[0]) * (pt.y - a[1]) / (b[1] - a[1]) + a[0]) inside = !inside;
    }
    return inside;
}
const configKey = ready ? JSON.stringify([cfg.polygon, cfg.masks, cfg.presence_polygon]) : '';
if (configKey !== s.configKey) {
    s = { lastFrame: 0, candidateSince: null, clearSince: null, present: false, confirmedMs: 0,
        state: 'unknown', candidates: 0, blockers: 0, lastPublish: 0, configKey };
}
const tick = msg.topic === '__presence_tick';
if (!tick && ready) {
    const data = msg.payload && (msg.payload.data || msg.payload);
    // A missing boxes array is invalid, NOT an empty detection frame.
    if (data && Array.isArray(data.boxes)) {
        const gap = s.lastFrame ? now - s.lastFrame : Infinity;
        if (gap > MAX_FRAME_GAP_MS) {
            s.candidateSince = null;
            s.clearSince = null;
            s.state = s.present ? 'present' : 'unknown';
        }
        s.lastFrame = now;
        const labels = Array.isArray(data.labels) && data.labels.length ? data.labels : ['person'];
        const masks = Array.isArray(cfg.masks) ? cfg.masks : [];
        const interior = Array.isArray(cfg.presence_polygon) && cfg.presence_polygon.length >= 3
            ? cfg.presence_polygon : null;
        let candidates = 0, blockers = 0;
        for (const b of data.boxes) {
            if (!Array.isArray(b) || b.length < 6) continue;
            const [cx, cy, w, h] = b.slice(0, 4).map(Number);
            let score = Number(b[4]), cls = Number(b[5]);
            if (!Number.isInteger(cls) && Number.isInteger(Number(b[4]))) {
                cls = Number(b[4]); score = Number(b[5]);
            }
            if (![cx, cy, w, h, score].every(Number.isFinite) || w <= 0 || h <= 0 ||
                score < MIN_CONFIDENCE) continue;
            const label = String(labels[cls] || (cls === 0 ? 'person' : '')).toLowerCase();
            if (label !== 'person') continue;
            const foot = { x: cx, y: cy + h / 2 };
            if (masks.some(m => Array.isArray(m) && m.length >= 3 && inPoly(foot, m))) continue;
            const inDoor = inPoly(foot, cfg.polygon);
            const inInterior = !interior || inPoly(foot, interior);
            // Door detections block clearing while someone walks out of the presence zone.
            if (inDoor || inInterior) blockers++;
            if (!inDoor && inInterior) candidates++;
        }
        s.candidates = candidates; s.blockers = blockers;
        if (candidates > 0) {
            s.clearSince = null;
            if (s.candidateSince === null) s.candidateSince = now;
            if (!s.present && now - s.candidateSince > CONFIRM_MS) {
                s.present = true;
                s.confirmedMs = now - s.candidateSince;
            }
            s.state = s.present ? 'present' : 'pending';
        } else {
            s.candidateSince = null;
            if (blockers > 0) {
                s.clearSince = null;
                s.state = s.present ? 'present' : 'pending';
            } else {
                if (s.clearSince === null) s.clearSince = now;
                if (now - s.clearSince >= CLEAR_MS) {
                    s.present = false; s.confirmedMs = 0; s.state = 'clear';
                } else {
                    s.state = s.present ? 'present' : 'pending';
                }
            }
        }
    } else {
        // Invalid inference data breaks continuity and cannot establish a clear cell.
        s.lastFrame = 0;
        s.candidateSince = null;
        s.clearSince = null;
        s.state = 'unknown';
    }
}
const valid = !!ready && s.lastFrame > 0 && now - s.lastFrame <= STALE_MS;
if (!valid) {
    s.candidateSince = null; s.clearSince = null;
    s.state = 'unknown';
}
context.set('presence', s);
flow.set('last_presence_status', { state: valid ? s.state : 'unknown', valid,
    candidates: s.candidates, frame_age_ms: s.lastFrame ? now - s.lastFrame : -1 });
node.status({ fill: !valid ? 'grey' : (s.present ? 'red' : (s.state === 'clear' ? 'green' : 'yellow')),
    shape: 'dot', text: 'presence: ' + (valid ? s.state : 'unknown') });
if (deviceId === 'unknown' || now - s.lastPublish < PUBLISH_MS) return null;
s.lastPublish = now;
context.set('presence', s);
return { topic: 'eagle/' + deviceId + '/presence', payload: JSON.stringify({
    state: valid ? s.state : 'unknown', valid,
    present: valid && s.present,
    candidates: s.candidates,
    continuous_ms: valid && s.candidateSince !== null ? now - s.candidateSince : 0,
    confirmed_ms: valid && s.present ? s.confirmedMs : 0,
    frame_age_ms: s.lastFrame ? now - s.lastFrame : -1,
    door: flow.get('door_label') || '?', ts: now
}) };
