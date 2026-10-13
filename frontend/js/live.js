const statusEl = document.getElementById('status');
const autoBtn = document.getElementById('autoBtn');

let cams = {};          // role -> {role, is_trigger, online, error, device_id, params, preview, capture, stream_on}
let camOrder = [];      // reference camera first
let refRole = null;     // the reference camera: background subtraction + capture trigger
let camSpec = {};
let choices = { preview: [], capture: [] };
let bgs = {};           // role -> {exists, updated}

const CAM_LABELS = {
  exposure_ms: 'Exposure', iso: 'ISO', ae_limit_ms: 'AE limit', wb: 'WB', lens_position: 'Focus (lens)',
  sharpness: 'Sharpness', luma_denoise: 'Luma denoise',
  chroma_denoise: 'Chroma denoise', ae_comp: 'AE comp', brightness: 'Brightness', contrast: 'Contrast',
  hw_saturation: 'HW saturation', antibanding: 'Flicker',
};
const CAM_STEP = { iso: 100, wb: 100 };
const ANTIBANDING_LABELS = { AUTO: 'Auto', MAINS_50_HZ: '50Hz', MAINS_60_HZ: '60Hz', OFF: 'Off' };
const SIZE_LABEL = (v) => (v === 'full' ? 'Full (sensor)' : v.replace('x', '×'));

function fmtCam(name, v) {
  if (name === 'exposure_ms') return v == 0 ? 'Auto' : v + 'ms';
  if (name === 'iso') return v == 0 ? 'Auto' : v;
  if (name === 'wb') return v == 0 ? 'Auto' : v + 'K';
  if (name === 'ae_limit_ms') return v == 0 ? 'No limit' : v + 'ms';
  if (name === 'lens_position') return v == 0 ? 'Auto' : v;
  if (name === 'ae_comp') return v > 0 ? '+' + v : v;
  return v;
}

async function postJson(url, body) {
  const r = await fetch(url, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}),
  });
  if (!r.ok) {
    let msg = 'HTTP ' + r.status;
    try { msg = (await r.json()).error || msg; } catch (e) {}
    throw new Error(msg);
  }
  return r.json();
}

function say(text) { if (statusEl) statusEl.textContent = text; }

function showToast(text) {
  const el = document.getElementById('toast');
  if (!el) return;
  el.textContent = text;
  el.hidden = false;
  clearTimeout(showToast.timer);
  showToast.timer = setTimeout(() => { el.hidden = true; }, 3200);
}

function cameraReady(role) {
  const cam = cams[role];
  return !!(cam && cam.online && !cam.restart_pending);
}

function requireCameraReady(role) {
  if (cameraReady(role)) return true;
  const cam = cams[role];
  const rebuilding = cam && ((cam.online && cam.restart_pending)
    || String(cam.error || '').startsWith('restarting'));
  showToast(`${role.toUpperCase()} is ${rebuilding ? 'restarting' : 'offline'}. Wait until it is online.`);
  return false;
}

function hasLiveStream() {
  return camOrder.some((role) => {
    const cam = cams[role];
    return cam && cam.online && cam.stream_on && !cam.restart_pending;
  });
}

function confirmAllOff() {
  const root = document.getElementById('confirmOff');
  root.hidden = false;
  const ok = document.getElementById('confirmOffOk');
  const cancel = document.getElementById('confirmOffCancel');
  return new Promise((resolve) => {
    const finish = (value) => {
      root.hidden = true;
      ok.onclick = null;
      cancel.onclick = null;
      root.onclick = null;
      document.removeEventListener('keydown', onKey);
      resolve(value);
    };
    const onKey = (event) => { if (event.key === 'Escape') finish(false); };
    ok.onclick = () => finish(true);
    cancel.onclick = () => finish(false);
    root.onclick = (event) => { if (event.target === root) finish(false); };
    document.addEventListener('keydown', onKey);
    cancel.focus();
  });
}

// ---- camera settings sliders (exposure, ISO, ...) -------------------------------------

const camTarget = () => document.getElementById('camTarget').value;

function shownParams() {
  const t = camTarget();
  const c = cams[t === 'all' ? refRole : t];
  return c ? c.params : {};
}

function buildCamControls() {
  const box = document.getElementById('camControls');
  const values = shownParams();
  box.innerHTML = '';
  for (const [name, s] of Object.entries(camSpec)) {
    const row = document.createElement('div');
    row.className = 'slider-row';
    const lbl = document.createElement('label');
    lbl.textContent = CAM_LABELS[name] || name;
    row.appendChild(lbl);
    if (s.type === 'enum') {
      const sel = document.createElement('select');
      sel.id = 'cam_' + name;
      for (const v of s.values) {
        const o = document.createElement('option');
        o.value = v; o.textContent = ANTIBANDING_LABELS[v] || v;
        sel.appendChild(o);
      }
      sel.value = values[name];
      sel.onchange = () => postCam(name, sel.value);
      row.appendChild(sel);
      row.appendChild(document.createElement('span'));
    } else {
      const inp = document.createElement('input');
      inp.type = 'range'; inp.id = 'cam_' + name;
      inp.min = s.min; inp.max = s.max; inp.step = CAM_STEP[name] || 1; inp.value = values[name];
      const val = document.createElement('span');
      val.className = 'val'; val.id = 'camval_' + name; val.textContent = fmtCam(name, values[name]);
      inp.oninput = () => {
        val.textContent = fmtCam(name, inp.value);
        postCam(name, inp.value);
      };
      row.appendChild(inp);
      row.appendChild(val);
    }
    box.appendChild(row);
  }
}

function postCam(name, value) {
  const role = camTarget();
  setParamLocal(role, name, isNaN(Number(value)) ? value : Number(value));
  return postJson('/api/cam', { name, value, role }).catch(() => {});
}

function setParamLocal(role, name, value) {
  for (const r of role === 'all' ? camOrder : [role]) {
    if (cams[r]) cams[r].params[name] = value;
  }
}

async function resetCamDefaults() {
  const role = camTarget();
  const names = Object.keys(camSpec);
  if (!names.length) return;
  const btn = document.getElementById('camReset');
  btn.disabled = true;
  try {
    for (const name of names) {
      const value = camSpec[name].default;
      await postJson('/api/cam', { name, value, role });
      setParamLocal(role, name, value);
    }
    refreshCamValues();
    showToast('Camera settings restored to defaults.');
  } catch (e) {
    showToast('Could not reset camera settings.');
    refreshState();
  } finally {
    btn.disabled = false;
  }
}

function refreshCamValues() {
  const values = shownParams();
  for (const name of Object.keys(camSpec)) {
    const el = document.getElementById('cam_' + name);
    if (el && el !== document.activeElement) el.value = values[name];
    const val = document.getElementById('camval_' + name);
    if (val) val.textContent = fmtCam(name, values[name]);
  }
}

// ---- focus -------------------------------------------------------------------------------

function attachFocus(img, wrap, getRole) {
  img.addEventListener('click', async (e) => {
    const role = getRole();
    const box = img.getBoundingClientRect();
    if (!box.width || !box.height) return;
    // the picture inside the element (tiles use object-fit: contain, so a rotated
    // picture is letterboxed inside the tile)
    const scale = img.naturalWidth && img.naturalHeight
      ? Math.min(box.width / img.naturalWidth, box.height / img.naturalHeight) : 0;
    const r = scale ? {
      width: img.naturalWidth * scale, height: img.naturalHeight * scale,
      left: box.left + (box.width - img.naturalWidth * scale) / 2,
      top: box.top + (box.height - img.naturalHeight * scale) / 2,
    } : box;
    const px = e.clientX - r.left, py = e.clientY - r.top;
    if (px < 0 || py < 0 || px > r.width || py > r.height) return;   // clicked the black bars
    const w = wrap.getBoundingClientRect();   // the image may be narrower than its container
    const marker = document.createElement('div');
    marker.className = 'marker';
    marker.style.left = (r.left - w.left + px) + 'px'; marker.style.top = (r.top - w.top + py) + 'px';
    wrap.appendChild(marker);
    setTimeout(() => marker.remove(), 600);
    say(`Focus (${role}): (${(px / r.width * 100).toFixed(0)}%, ${(py / r.height * 100).toFixed(0)}%)`);
    try { await postJson('/api/focus', { x: px / r.width, y: py / r.height, role }); }
    catch (err) { say('Focus error: ' + err.message); }
  });
}

// ---- camera tiles: live picture + the latest captured set, one tile per camera ---------------------

let tilesKey = null;
let featuredRole = 'top';
let maximizedRole = null;
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

function applyFeatured() {
  if (!camOrder.includes(featuredRole)) featuredRole = camOrder[0] || 'top';
  const box = document.getElementById('tiles');
  const featured = document.getElementById('tile_' + featuredRole);
  for (const tile of box.querySelectorAll('.tile')) tile.classList.toggle('featured', tile === featured);
  if (featured) box.prepend(featured);
}

function setMaximized(role) {
  maximizedRole = role;
  const box = document.getElementById('tiles');
  for (const tile of box.querySelectorAll('.tile')) {
    tile.classList.toggle('maximized', role != null && tile.id === 'tile_' + role);
  }
}

// One long-polled JPEG per tile, reference camera included (browsers allow ~6 connections per host,
// and the event stream uses one of them). The server answers as soon as a newer frame exists, so a
// tile runs at the camera's own preview rate.
async function pollSnapshot(role, img) {
  let after = -1;
  let shown = null;
  while (document.getElementById('tile_' + role)) {          // ends when the tiles are rebuilt
    const c = cams[role];
    if (!c || !c.online || !c.stream_on) { after = -1; await sleep(700); continue; }
    try {
      const r = await fetch(`/snapshot?cam=${encodeURIComponent(role)}&after=${after}&wait=700`);
      if (r.status === 200) {
        after = Number(r.headers.get('X-Frame-Seq'));
        const url = URL.createObjectURL(await r.blob());
        img.src = url;
        if (shown) URL.revokeObjectURL(shown);
        shown = url;
      } else if (r.status !== 204) {                          // 204 = nothing newer yet, ask again
        await sleep(1000);
      }
    } catch (e) { await sleep(1500); }
  }
  if (shown) URL.revokeObjectURL(shown);
}

function buildTiles() {
  const key = camOrder.join(',');   // the reference camera comes first, so a new reference reorders it
  if (key === tilesKey) return;
  tilesKey = key;
  const box = document.getElementById('tiles');
  box.innerHTML = '';
  for (const role of camOrder) {
    const tile = document.createElement('div');
    tile.className = 'tile offline';
    tile.id = 'tile_' + role;
    const wrap = document.createElement('div');
    wrap.className = 'tile-img';
    const img = document.createElement('img');
    img.alt = '';
    wrap.appendChild(img);
    const tools = document.createElement('div');
    tools.className = 'tile-tools';
    const focus = document.createElement('button');
    focus.type = 'button';
    focus.className = 'tile-focus';
    focus.title = 'Main focus';
    focus.setAttribute('aria-label', 'Make ' + role + ' the main view');
    focus.innerHTML = '<svg viewBox="0 0 24 24" fill="none" aria-hidden="true"><rect x="3.5" y="5" width="9" height="14" rx="1.5" stroke="currentColor" stroke-width="1.6"/><rect x="14.5" y="5" width="6" height="6" rx="1" stroke="currentColor" stroke-width="1.6"/><rect x="14.5" y="13" width="6" height="6" rx="1" stroke="currentColor" stroke-width="1.6"/></svg>';
    focus.onclick = (event) => {
      event.stopPropagation();
      featuredRole = role;
      applyFeatured();
    };
    const max = document.createElement('button');
    max.type = 'button';
    max.className = 'tile-max';
    max.title = 'Expand';
    max.setAttribute('aria-label', 'Expand ' + role);
    max.innerHTML = '<svg viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="M9 4H4v5M15 4h5v5M9 20H4v-5M15 20h5v-5" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>';
    max.onclick = (event) => {
      event.stopPropagation();
      setMaximized(role);
    };
    const close = document.createElement('button');
    close.type = 'button';
    close.className = 'tile-close';
    close.title = 'Close';
    close.setAttribute('aria-label', 'Close expanded view');
    close.textContent = '✕';
    close.onclick = (event) => {
      event.stopPropagation();
      setMaximized(null);
    };
    tools.append(focus, max, close);
    wrap.appendChild(tools);
    const label = document.createElement('div');
    label.className = 'tile-label';
    label.innerHTML = `<b>${role.toUpperCase()}</b> <span class="reftag" hidden>Ref</span>`
      + ' <span class="state"></span> <span class="fps"></span> <span class="shot-time"></span>';
    const shots = document.createElement('div');
    shots.className = 'shots';
    shots.id = 'shots_' + role;
    tile.append(wrap, label, shots);
    box.appendChild(tile);
    attachFocus(img, wrap, () => role);
    pollSnapshot(role, img);
  }
  applyFeatured();
  if (maximizedRole) setMaximized(maximizedRole);
  renderShots();
}

function renderTiles() {
  buildTiles();
  for (const role of camOrder) {
    const c = cams[role];
    const tile = document.getElementById('tile_' + role);
    if (!tile) continue;
    tile.classList.toggle('ref', role === refRole);
    tile.classList.toggle('offline', !c.online);
    tile.classList.toggle('off', c.online && !c.stream_on);
    tile.querySelector('.reftag').hidden = role !== refRole;
    tile.querySelector('.state').textContent = !c.online ? 'OFFLINE' : (!c.stream_on ? 'STREAM OFF' : '');
    tile.querySelector('.fps').textContent = c.online && c.stream_on ? `${(c.fps || {}).preview ?? 0} fps` : '';
    tile.title = c.online ? (c.device_id || '') : (c.error || 'offline');
  }
}

// ---- the latest captured set, shown under every camera's live picture ------------------------------

const SHOT_ORDER = { before: 0, center: 1, after: 2 };
const SHOT_LABEL = { before: 'Before', center: 'Center', after: 'After' };
const shotName = (m) => (m.burst ? SHOT_LABEL[m.burst.kind] || '' : 'Manual');
let shotSet = [];       // samples of the newest capture, before -> center -> after
let shotKey = null;     // ids of that set: a change means a new capture arrived

// A shoe pass saves up to three sets (before / center / after) that share a burst id.
function pickLatestSet(list) {
  if (!list.length) return [];
  const burst = list[0].burst && list[0].burst.id;
  const group = burst ? list.filter((m) => m.burst && m.burst.id === burst) : [list[0]];
  return group.sort((a, b) => (SHOT_ORDER[a.burst && a.burst.kind] ?? 1) - (SHOT_ORDER[b.burst && b.burst.kind] ?? 1));
}

function hasView(m, role) {
  return m.views ? role in m.views : role === (m.primary_view || refRole);
}

function shotEl(m, role) {
  if (!hasView(m, role)) {
    const miss = document.createElement('div');
    miss.className = 'shot missing';
    miss.textContent = 'None';
    miss.title = ((m.missing_views || {})[role]) || 'No picture from this camera';
    return miss;
  }
  const a = document.createElement('a');
  a.className = 'shot';
  a.href = `/browse?sample=${m.id}`;
  const v = (m.views || {})[role] || {};
  a.title = `${role.toUpperCase()} · ${shotName(m)} · ${m.id}`
    + (v.sharpness != null ? ` · sharpness ${v.sharpness}${v.pick && v.pick.picked ? ' (picked from nearby frames)' : ''}` : '');
  const im = document.createElement('img');
  im.alt = '';
  im.src = `/api/samples/${m.id}/thumb?view=${encodeURIComponent(role)}`;
  const kind = document.createElement('span');
  kind.className = 'kind';
  kind.textContent = shotName(m);
  a.append(im, kind);
  a.onclick = (e) => { e.preventDefault(); openLightbox(m, role); };
  return a;
}

function latestShotStamp(role) {
  let latest = '';
  for (const sample of shotSet) {
    if (!hasView(sample, role)) continue;
    const stamp = sample.timestamp || '';
    if (stamp > latest) latest = stamp;
  }
  return latest ? latest.replace('T', ' ').slice(0, 19) : '';
}

function renderShots() {
  const key = shotSet.map((m) => m.id).join(',');
  const isNew = shotKey !== null && key !== shotKey && key !== '';
  shotKey = key;
  for (const role of camOrder) {
    const box = document.getElementById('shots_' + role);
    if (!box) continue;
    const timeEl = box.parentElement.querySelector('.shot-time');
    if (timeEl) timeEl.textContent = latestShotStamp(role);
    if (!shotSet.length) {
      const none = document.createElement('div');
      none.className = 'none';
      none.textContent = 'No capture yet';
      box.replaceChildren(none);
      continue;
    }
    box.replaceChildren(...shotSet.map((m) => shotEl(m, role)));
    if (isNew) {
      box.classList.remove('fresh');
      void box.offsetWidth;   // restart the highlight animation
      box.classList.add('fresh');
    }
  }
  const bar = document.getElementById('capBar');
  if (!bar) return;
  if (!shotSet.length) { bar.textContent = 'No captures yet'; return; }
  const first = shotSet[0];
  const qr = first.shoe_id || shotSet.map((m) => m.qr && m.qr[0] && m.qr[0].text).find(Boolean);
  const time = (first.timestamp || '').replace('T', ' ').slice(0, 19);
  bar.innerHTML = `<span>Latest set</span> <b>${time}</b>`
    + ` <span>${first.source === 'auto' ? 'Auto' : 'Manual'} · ${shotSet.length} frames</span>`
    + (qr ? ` <span>QR <b class="mono"></b></span>` : '') + ` <a href="/browse?sample=${first.id}">Open in browse →</a>`;
  if (qr) bar.querySelector('.mono').textContent = qr;
}

async function refreshCaptures() {
  try {
    const list = await fetch('/api/samples?limit=6').then((r) => r.json());
    shotSet = pickLatestSet(list);
    renderShots();
  } catch (e) {}
}

// ---- lightbox (a captured picture at full size) -----------------------------------------------------

const lightbox = document.getElementById('lightbox');

function openLightbox(m, role) {
  document.getElementById('lbImg').src = `/api/samples/${m.id}/image?view=${encodeURIComponent(role)}`;
  document.getElementById('lbCaption').textContent = `${role.toUpperCase()} · ${shotName(m)} · ${m.id}`;
  document.getElementById('lbLink').href = `/browse?sample=${m.id}`;
  lightbox.hidden = false;
}

function closeLightbox() {
  lightbox.hidden = true;
  document.getElementById('lbImg').removeAttribute('src');
}

document.getElementById('lbClose').onclick = closeLightbox;
lightbox.addEventListener('click', (e) => { if (e.target === lightbox) closeLightbox(); });
document.addEventListener('keydown', (e) => {
  if (e.key !== 'Escape') return;
  if (!lightbox.hidden) { closeLightbox(); return; }
  const confirmOff = document.getElementById('confirmOff');
  if (confirmOff && !confirmOff.hidden) return;
  if (maximizedRole) setMaximized(null);
});

// ---- control panel (slides in from the right) ---------------------------------------------------------

const panelBtn = document.getElementById('panelToggle');

function setPanel(open) {
  document.body.classList.toggle('panel-open', open);
  panelBtn.setAttribute('aria-expanded', String(open));
  const label = open ? 'Close controls' : 'Open controls';
  panelBtn.setAttribute('aria-label', label);
  panelBtn.title = label;
  try { localStorage.setItem('panelOpen', open ? '1' : '0'); } catch (e) {}
}

function fitPanel() {   // the panel starts right under the top bar, whatever height that has
  const bar = document.querySelector('.topbar');
  if (bar) document.documentElement.style.setProperty('--topbar-h', bar.offsetHeight + 'px');
}

panelBtn.onclick = () => setPanel(!document.body.classList.contains('panel-open'));
document.getElementById('panelClose').onclick = () => setPanel(false);
window.addEventListener('resize', fitPanel);
fitPanel();
setPanel(false);

// ---- camera management list ----------------------------------------------------------------

let camListKey = null;

// USB link and the frame rates that actually arrive: what to look at when the picture is choppy
const USB_NAME = { SUPER_PLUS: 'USB3+', SUPER: 'USB3', HIGH: 'USB2', FULL: 'USB1', LOW: 'USB1' };
const isSlowUsb = (c) => ['HIGH', 'FULL', 'LOW'].includes(c.usb);
function statsText(c) {
  const f = c.fps || {};
  const usb = c.usb ? (USB_NAME[c.usb] || c.usb) + (isSlowUsb(c) ? ' ⚠ slow' : '') : 'USB ?';
  return `${usb} · Stream ${f.preview ?? 0} fps · Capture ${f.full ?? 0} fps`;
}

function sizeSelect(values, current) {
  const sel = document.createElement('select');
  for (const v of values) {
    const o = document.createElement('option');
    o.value = v; o.textContent = SIZE_LABEL(v);
    sel.appendChild(o);
  }
  sel.value = current;
  return sel;
}

function buildCamList() {
  const key = camOrder.join(',');
  if (key === camListKey) return;
  camListKey = key;
  const box = document.getElementById('camList');
  box.innerHTML = '';
  for (const role of camOrder) {
    const row = document.createElement('div');
    row.className = 'camrow';
    row.id = 'camrow_' + role;
    row.innerHTML = `
      <div class="camhead">
        <label class="refpick" title="Use as the reference camera for background removal and capture">
          <input type="radio" name="refcam" value="${role}"> <b>${role.toUpperCase()}</b></label>
        <span class="chip st"></span>
        <label class="switch" title="Stream on/off (capture is unaffected)">
          <input type="checkbox" class="streamSw"><span></span></label>
        <button class="secondary applyBtn" disabled>Apply</button>
      </div>
      <div class="stats"></div>
      <div class="camsel">
        <label>Stream</label><span class="prevHold"></span>
        <label>Capture</label><span class="capHold"></span>
      </div>
      <div class="camrot">
        <label>Rotate</label>
        <button class="secondary rotBtn" data-step="-90" title="Rotate 90° counterclockwise">↺</button>
        <span class="rotVal">0°</span>
        <button class="secondary rotBtn" data-step="90" title="Rotate 90° clockwise">↻</button>
      </div>
      <div class="cambg">
        <span class="chip bgSt"></span>
        <button class="secondary bgShot">Capture background</button>
      </div>`;
    box.appendChild(row);
    const prev = sizeSelect(choices.preview, cams[role].preview);
    const cap = sizeSelect(choices.capture, cams[role].capture);
    row.querySelector('.prevHold').appendChild(prev);
    row.querySelector('.capHold').appendChild(cap);
    const apply = row.querySelector('.applyBtn');
    const dirty = () => {
      const c = cams[role];
      const unchanged = prev.value === c.preview && cap.value === c.capture;
      apply.disabled = cameraReady(role) && unchanged;
      prev.dataset.dirty = cap.dataset.dirty = unchanged ? '' : '1';
    };
    prev.onchange = cap.onchange = dirty;
    apply.onclick = async () => {
      if (!requireCameraReady(role)) return;
      apply.disabled = true;
      try {
        await postJson('/api/camera_config', { role, preview: prev.value, capture: cap.value });
        say(`${role.toUpperCase()} camera is restarting at the new resolution…`);
      } catch (e) { say('Could not change resolution: ' + e.message); }
      refreshState();
    };
    row.querySelector('.streamSw').onchange = async (e) => {
      try { await postJson('/api/stream', { role, on: e.target.checked }); }
      catch (err) { say('Could not switch the stream: ' + err.message); }
      refreshState();
    };
    row.querySelector('input[name=refcam]').onchange = async (e) => {
      if (!e.target.checked || role === refRole) return;
      if (!confirm(`Change the reference camera to ${role.toUpperCase()}?\n${refRole.toUpperCase()} and ${role.toUpperCase()} will restart.`)) {
        e.target.checked = false;
        updateCamList();
        return;
      }
      try {
        await postJson('/api/reference', { role });
        say(`Reference camera → ${role.toUpperCase()} (Restarting)`);
      } catch (err) { say('Could not change the reference camera: ' + err.message); }
      refreshState();
    };
    for (const btn of row.querySelectorAll('.rotBtn')) {
      btn.onclick = async () => {
        const rotation = ((cams[role].rotation || 0) + Number(btn.dataset.step) + 360) % 360;
        try {
          await postJson('/api/rotation', { role, rotation });
          cams[role].rotation = rotation;
          updateCamList();
          say(`${role.toUpperCase()} rotation → ${rotation}°`);
        } catch (e) { say('Rotation failed: ' + e.message); }
      };
    }
    row.querySelector('.bgShot').onclick = async () => {
      if (!requireCameraReady(role)) return;
      try {
        await postJson('/api/background', { role });
        say(`Capturing ${role.toUpperCase()} background… (keep the view empty)`);
      } catch (err) { say('Background capture failed: ' + err.message); }
    };
  }
}

function updateCamList() {
  buildCamList();
  for (const role of camOrder) {
    const row = document.getElementById('camrow_' + role);
    const c = cams[role];
    if (!row || !c) continue;
    row.classList.toggle('is-ref', role === refRole);
    row.classList.toggle('is-offline', !c.online);
    row.querySelector('input[name=refcam]').checked = role === refRole;
    for (const el of row.querySelectorAll('button, input, select')) el.disabled = !c.online;
    const st = row.querySelector('.st');
    const rebuilding = (c.online && c.restart_pending)
      || (!c.online && String(c.error || '').startsWith('restarting'));
    st.textContent = rebuilding ? 'Restarting' : (c.online ? 'ONLINE' : 'OFFLINE');
    st.className = 'chip st ' + (rebuilding ? 'warn' : (c.online ? 'ok' : 'ng'));
    st.title = c.online ? (c.device_id || '') : (c.error || '');
    row.querySelector('.streamSw').checked = c.stream_on;
    row.querySelector('.rotVal').textContent = (c.rotation || 0) + '°';
    const stats = row.querySelector('.stats');
    stats.textContent = c.online ? statsText(c) : '';
    stats.classList.toggle('slow', c.online && isSlowUsb(c));
    const prev = row.querySelector('.prevHold select');
    const cap = row.querySelector('.capHold select');
    if (!prev.dataset.dirty) { prev.value = c.preview; cap.value = c.capture; }
    const unchanged = prev.value === c.preview && cap.value === c.capture;
    row.querySelector('.applyBtn').disabled = !c.online || unchanged;
    const bg = bgs[role] || {};
    const bgSt = row.querySelector('.bgSt');
    bgSt.textContent = bg.exists ? 'Has background' : 'No background';
    bgSt.className = 'chip bgSt ' + (bg.exists ? 'ok' : 'warn');
    bgSt.title = bg.updated ? bg.updated.replace('T', ' ') : '';
  }
  renderStreamAll();
}

function renderCamChip() {
  const online = camOrder.filter((r) => cams[r].online).length;
  const chip = document.getElementById('camChip');
  chip.textContent = `Cameras ${online}/${camOrder.length}`;
  chip.className = 'chip ' + (online === camOrder.length ? 'ok' : 'warn');
}

// ---- state ---------------------------------------------------------------------------------

function setState(st) {
  camSpec = st.cam_spec;
  choices = st.choices;
  bgs = st.backgrounds || {};
  const old = cams;
  cams = {};
  camOrder = st.cams.map((c) => c.role);
  refRole = st.trigger_camera;
  for (const c of st.cams) {
    // slider values are edited locally between refreshes: keep them unless the page has none yet
    cams[c.role] = c;
    if (old[c.role] && document.activeElement && document.activeElement.id &&
        document.activeElement.id.startsWith('cam_')) cams[c.role].params = old[c.role].params;
  }
}

function renderAll(st) {
  renderTiles();
  updateCamList();
  renderCamChip();
  const unlabeledChip = document.getElementById('unlabeledChip');   // the labeling menu is hidden
  if (st.counts && unlabeledChip) unlabeledChip.textContent = st.counts.unlabeled;
  const det = st.detector;
  document.getElementById('detectorChip').textContent =
    det.mode === 'background' ? 'Detect: background'
      : det.mode === 'model' ? `Detect: model ${det.model} · targets ${det.targets.join(', ')}`
      : 'Preparing cameras…';
  const shotsChip = document.getElementById('shotsChip');
  if (shotsChip) shotsChip.textContent = st.shots.count >= 3
    ? `Shots: before, center, and after (spread ${st.shots.spread})` : 'Shots: center only';
  const bg = bgs[refRole] || {};
  const bgChip = document.getElementById('bgChip');
  bgChip.textContent = `${refRole.toUpperCase()} · ` + (bg.exists ? 'Background ' + bg.updated.replace('T', ' ') : 'No background');
  bgChip.className = 'chip ' + (bg.exists ? 'ok' : 'warn');
  if (st.counts) document.getElementById('autoInfo').textContent =
    `This run: ${st.auto_count} auto sets · ${st.counts.passes} saved sets (${st.counts.all} frames)`;
  document.title = 'ON Data Collector';
  renderQr(st.qr && st.qr.latest);
}

let firstState = true;

async function refreshState(light) {
  try {
    const st = await fetch('/api/state' + (light === true ? '?light=1' : '')).then((r) => r.json());
    setState(st);
    if (firstState) {
      firstState = false;
      const sel = document.getElementById('camTarget');
      sel.innerHTML = '<option value="all">All cameras</option>'
        + camOrder.map((r) => `<option value="${r}">${r.toUpperCase()}</option>`).join('');
      sel.onchange = () => buildCamControls();
      document.getElementById('camTargetRow').hidden = camOrder.length < 2;
      buildCamControls();
      renderAuto(st.auto_enabled);
    } else {
      refreshCamValues();
    }
    renderAll(st);
    return st;
  } catch (e) { say('Could not reach the server'); }
}

async function refreshRecent() {
  const recent = document.getElementById('recent');
  if (!recent) return;
  try {
    // light state + only the newest samples: neither may read every sample file
    const st = await fetch('/api/state?light=1').then((r) => r.json());
    const list = await fetch('/api/samples?limit=' + st.ui.recent_count).then((r) => r.json());
    recent.innerHTML = '';
    for (const m of list) {
      const a = document.createElement('a');
      a.href = '/browse?sample=' + m.id;
      const im = document.createElement('img');
      im.src = `/api/samples/${m.id}/thumb`;
      const kind = m.burst ? { before: 'Before', center: 'Center', after: 'After' }[m.burst.kind] : '';
      im.title = m.id + (m.views ? ` (${Object.keys(m.views).length} views)` : '') + (kind ? ` · ${kind}` : '')
        + (m.qr && m.qr.length ? ` · QR ${m.qr[0].text}` : '');
      a.appendChild(im);
      recent.appendChild(a);
    }
  } catch (e) {}
}

// ---- QR ----------------------------------------------------------------------------------------

function ago(iso) {
  const s = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 1000));
  return s < 60 ? `${s}s ago` : s < 3600 ? `${Math.round(s / 60)}m ago` : `${Math.round(s / 3600)}h ago`;
}

let lastQr = null;

function renderQr(entry) {
  if (!document.getElementById('qrText')) return;
  if (entry) lastQr = entry;
  const e = lastQr;
  document.getElementById('qrText').textContent = e ? e.text : '-';
  document.getElementById('qrMeta').textContent = e
    ? `${e.role.toUpperCase()} Camera · ${e.source === 'live' ? 'Live scan' : 'Captured image'} · ${ago(e.time)}`
    : 'No QR read yet';
}
setInterval(() => renderQr(null), 5000);

// ---- auto capture / background ---------------------------------------------------------------

function renderAuto(enabled) {
  autoBtn.checked = !!enabled;
}

autoBtn.onchange = async () => {
  const enabled = autoBtn.checked;
  if (!hasLiveStream()) {
    autoBtn.checked = !enabled;
    showToast('Turn on a camera stream before using auto capture.');
    return;
  }
  try { renderAuto((await postJson('/api/auto', { enabled })).auto_enabled); }
  catch (e) {
    autoBtn.checked = !enabled;
    say('Error: ' + e.message);
  }
};

document.getElementById('camReset').onclick = () => resetCamDefaults();

// document.getElementById('bgBtn').onclick = async () => {
//   try {
//     await postJson('/api/background', {});
//     say(`Capturing ${refRole.toUpperCase()} background… (averaging a few frames)`);
//   } catch (e) { say('Error: ' + e.message); }
// };

document.getElementById('nowBtn').onclick = async () => {
  if (!hasLiveStream()) {
    showToast('Turn on a camera stream before capturing.');
    return;
  }
  try { await postJson('/api/capture_now'); say('Manual capture requested'); }
  catch (e) { say('Error: ' + e.message); }
};

function renderStreamAll() {
  const onBtn = document.getElementById('streamAllOn');
  const offBtn = document.getElementById('streamAllOff');
  const anyOn = camOrder.some((role) => cams[role] && cams[role].stream_on);
  const allOn = camOrder.length > 0 && camOrder.every((role) => cams[role] && cams[role].stream_on);
  onBtn.disabled = allOn;
  onBtn.classList.toggle('secondary', !allOn);
  onBtn.setAttribute('aria-pressed', String(allOn));
  offBtn.disabled = !anyOn;
}

document.getElementById('streamAllOn').onclick = () => {
  if (document.getElementById('streamAllOn').disabled) return;
  postJson('/api/stream', { on: true }).then(refreshState).catch(() => {});
};
document.getElementById('streamAllOff').onclick = async () => {
  if (document.getElementById('streamAllOff').disabled) return;
  if (!await confirmAllOff()) return;
  postJson('/api/stream', { on: false }).then(refreshState).catch(() => {});
};

// ---- events --------------------------------------------------------------------------------------

let stateTimer = null;
const refreshStateSoon = () => { clearTimeout(stateTimer); stateTimer = setTimeout(refreshState, 250); };

refreshState().then(refreshRecent);
refreshCaptures();
setInterval(() => refreshState(true), 2500);   // keeps fps / USB / online status current

let recentTimer = null;
new EventSource('/events').onmessage = (e) => {
  let data;
  try { data = JSON.parse(e.data); } catch (err) { return; }
  switch (data.key) {
    case 'cam':
      setParamLocal(data.role || 'all', data.name, data.value);
      refreshCamValues();
      break;
    case 'camera_status':
      if (cams[data.role]) {
        cams[data.role].online = data.online;
        cams[data.role].error = data.error;
        renderCamChip(); renderTiles(); updateCamList();
      }
      refreshStateSoon();
      break;
    case 'reference': case 'camera_config': case 'stream': case 'rotation':
      refreshStateSoon();
      break;
    case 'auto_enabled': renderAuto(data.value); break;
    case 'auto_capture': say('Auto captured ' + new Date().toLocaleTimeString()); break;
    case 'qr': renderQr(data.entry); break;
    case 'background':
      say((data.role ? data.role.toUpperCase() + ' ' : '') + (data.value ? 'background saved' : 'background save failed'));
      refreshStateSoon();
      break;
    case 'samples_changed':
      clearTimeout(recentTimer);
      recentTimer = setTimeout(() => { refreshRecent(); refreshCaptures(); refreshStateSoon(); }, 300);
      break;
  }
};

