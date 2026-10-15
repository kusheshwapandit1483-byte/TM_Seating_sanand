"""Build the special flow exports from the unchanged original exports."""
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent

def load(name):
    return json.loads((ROOT / name).read_text(encoding='utf-8-sig'))

def save(name, nodes):
    (HERE / name).write_text(json.dumps(nodes, indent=4, ensure_ascii=False) + '\n', encoding='utf-8')

def replace_once(text, old, new):
    if old not in text:
        raise ValueError('Missing source anchor: ' + old[:100])
    return text.replace(old, new, 1)

production = load('production_flow.json')
p = {n['id']: n for n in production}
p['flow_prod']['label'] = 'Production - Interior Presence'
p['flow_prod']['info'] += ' Independent >2 second interior presence confirmation; ESP32 selects inside camera.'
p['prod_gate']['wires'][0].append('prod_presence')
# Preserve the counting algorithm byte-for-byte. Only retain the new ROI setting.
p['prod_apply_roi']['func'] = replace_once(p['prod_apply_roi']['func'],
    "    global.set('roi_config', final);",
    "    final.presence_polygon = Array.isArray(cfg.presence_polygon) ? cfg.presence_polygon : [];\n    global.set('roi_config', final);")
production.extend([
    {'id': 'prod_presence', 'type': 'function', 'z': 'flow_prod',
     'name': 'Interior Presence >2s', 'func': (HERE / 'sources/recamera_presence.js').read_text(),
     'outputs': 1, 'timeout': 0, 'noerr': 0, 'initialize': "context.set('presence', null);",
     'finalize': '', 'libs': [], 'x': 570, 'y': 440, 'wires': [['prod_mqtt']]},
    {'id': 'prod_presence_tick', 'type': 'inject', 'z': 'flow_prod',
     'name': 'Presence freshness 500ms', 'props': [{'p': 'topic', 'vt': 'str'}],
     'repeat': '0.5', 'crontab': '', 'once': True, 'onceDelay': '5',
     'topic': '__presence_tick', 'x': 270, 'y': 440, 'wires': [['prod_presence']]}
])
save('production_flow.json', production)

maintenance = load('maintainance_flow.json')
m = {n['id']: n for n in maintenance}
m['flow_maint']['label'] = 'Maintenance - Interior Presence Setup'
m['maint_apply_roi']['func'] = replace_once(m['maint_apply_roi']['func'],
    "        global.set('roi_config', final);",
    "        final.presence_polygon = Array.isArray(cfg.presence_polygon) ? cfg.presence_polygon : [];\n        global.set('roi_config', final);")
m['roi_backend']['func'] = replace_once(m['roi_backend']['func'],
    "    if (!Array.isArray(cfg.masks)) cfg.masks = [];",
    "    if (!Array.isArray(cfg.masks)) cfg.masks = [];\n    if (!Array.isArray(cfg.presence_polygon)) cfg.presence_polygon = [];\n    if (cfg.presence_polygon.length > 0 && cfg.presence_polygon.length < 3) return null;")
m['device_panel']['format'] = m['device_panel']['format'].replace('TC_DR_E1', 'TM_R1_01').replace('TC_DR_E2', 'TM_R1_02').replace('Door E2', 'Door E1')
ui = m['roi_editor']['format']
ui = replace_once(ui, '<v-btn value="mask" size="small">Mask Polygon</v-btn>',
    '<v-btn value="mask" size="small">Mask Polygon</v-btn>\n          <v-btn value="presence" size="small">Interior Zone</v-btn>')
ui = replace_once(ui, '      <div class="section save-section">', '''      <div class="section">
        <div class="title">CELL INTERIOR PRESENCE ZONE</div>
        <div class="hint">For the INSIDE camera: draw the visible cell interior in Interior Zone mode. Presence requires a person outside the door ROI for more than 2 seconds. Door detections prevent premature clearing. ESP32 chooses which camera is inside. An empty zone uses the whole unmasked view outside the door ROI.</div>
        <div class="info-row"><span>Saved zone</span><span>{{ presencePolygon.length }} points</span></div>
        <div class="btn-row">
          <v-btn size="small" color="success" @click="savePresenceZone" :disabled="presenceDrawing.length<3">Use Interior Zone</v-btn>
          <v-btn size="small" color="warning" @click="clearPresenceZone">Use Whole View</v-btn>
        </div>
        <div class="hint">Finish with Save ROI Config. Avoid including the corridor through the doorway.</div>
      </div>
      <div class="section save-section">''')
ui = replace_once(ui, '      masks: [],', '      masks: [],\n      presencePolygon: [],\n      presenceDrawing: [],')
ui = replace_once(ui, '        this.masks = (cfg.masks || []).slice();',
    '        this.masks = (cfg.masks || []).slice();\n        this.presencePolygon = (cfg.presence_polygon || []).map(p => [p[0], p[1]]);')
ui = replace_once(ui, "      if (this.mode === 'poly' || this.mode === 'mask') this.draw();",
    "      if (['poly', 'mask', 'presence'].includes(this.mode)) this.draw();")
ui = replace_once(ui, "      } else if (this.mode === 'mask') {\n        this.maskDrawing.push(p);",
    "      } else if (this.mode === 'presence') {\n        this.presenceDrawing.push(p);\n        this.draw();\n      } else if (this.mode === 'mask') {\n        this.maskDrawing.push(p);")
ui = replace_once(ui, "      } else if (this.mode === 'mask' && this.maskDrawing.length) {",
    "      } else if (this.mode === 'presence' && this.presenceDrawing.length) {\n        this.presenceDrawing.pop();\n        this.draw();\n      } else if (this.mode === 'mask' && this.maskDrawing.length) {")
ui = replace_once(ui, '    saveAll() {', '''    savePresenceZone() {
      if (this.presenceDrawing.length < 3) return;
      this.presencePolygon = this.presenceDrawing.map(p => this.displayToModel(p.x, p.y));
      this.presenceDrawing = [];
      this.draw();
    },
    clearPresenceZone() {
      this.presencePolygon = [];
      this.presenceDrawing = [];
      this.draw();
    },
    saveAll() {''')
ui = replace_once(ui, '          masks: this.masks\n', '          masks: this.masks,\n          presence_polygon: this.presencePolygon\n')
ui = replace_once(ui, '      // Saved masks', '''      // Interior presence polygon in model coordinates; drawing in display coordinates.
      if (this.presencePolygon.length >= 3) {
        const pts = this.presencePolygon.map(p => this.modelToDisplay(p[0], p[1]));
        c.strokeStyle = '#00e5ff'; c.fillStyle = 'rgba(0,229,255,0.10)'; c.lineWidth = 2;
        c.beginPath(); c.moveTo(pts[0][0], pts[0][1]);
        for (let i = 1; i < pts.length; i++) c.lineTo(pts[i][0], pts[i][1]);
        c.closePath(); c.fill(); c.stroke();
        c.fillStyle = '#00e5ff'; c.fillText('CELL INTERIOR', pts[0][0] + 6, pts[0][1] - 6);
      }
      if (this.presenceDrawing.length) {
        c.strokeStyle = '#00e5ff'; c.lineWidth = 2; c.setLineDash([4,4]);
        c.beginPath(); c.moveTo(this.presenceDrawing[0].x, this.presenceDrawing[0].y);
        for (let i = 1; i < this.presenceDrawing.length; i++) c.lineTo(this.presenceDrawing[i].x, this.presenceDrawing[i].y);
        if (this.mode === 'presence' && this.mouse.x >= 0) c.lineTo(this.mouse.x, this.mouse.y);
        c.stroke(); c.setLineDash([]);
      }

      // Saved masks''')
m['roi_editor']['format'] = ui
save('maintainance_flow.json', maintenance)
print('Built production_flow.json and maintainance_flow.json')
