// The Image Lab page: one OpenCV technique applied to a saved picture (or one uploaded here) and shown next to the original.
// The work is done by the server (backend/imagelab.py); nothing is saved. /imagelab?id=<set id>&view=left opens a picture.

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s == null ? '' : s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const CAMERAS = ['top', 'back', 'left', 'front', 'right'];
const PAGE = 40;

let methods = [];
let byId = {};
let qrRoi = null;
let sets = [];            // the sets listed on the left (newest first)
let setsAll = false;      // everything was loaded
let loadingSets = false;
let source = null;        // {id, view} of a saved picture, or {token, camera} of an uploaded one
let method = 'original';
let seq = { orig: 0, res: 0 };
let timer = null;

async function getJson(url) {
  const r = await fetch(url, { cache: 'no-store' });
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).error || r.status);
  return r.json();
}

async function post(url, body) {
  const r = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.error || r.status);
  return data;
}

// ---- the picture --------------------------------------------------------------------------------------------------

function setChip() {
  if (!source) { $('srcChip').textContent = 'no picture'; return; }
  $('srcChip').textContent = source.token ? `uploaded · ${source.name || ''}` : `${source.view || 'primary'} · ${source.id}`;
}

function renderSets() {
  const q = $('setQ').value.trim().toLowerCase();
  const rows = sets.filter((m) => !q || m.id.toLowerCase().includes(q) || (m.shoe_id || '').toLowerCase().includes(q) || (m.timestamp || '').includes(q));
  $('setList').innerHTML = rows.length ? rows.map((m) =>
    `<div class="lab-set${source && source.id === m.id ? ' sel' : ''}" data-id="${esc(m.id)}">
       <div class="t">${esc((m.timestamp || '').replace('T', ' ').slice(0, 19))}</div><div class="s">${esc(m.shoe_id || 'no QR value')}</div></div>`).join('')
    : '<div class="empty">No sets</div>';
  $('setMore').hidden = setsAll;
}

async function loadSets() {
  if (loadingSets || setsAll) return;
  loadingSets = true;
  try {
    const part = await getJson(`/api/samples?status=all&limit=${PAGE}&offset=${sets.length}`);
    sets = sets.concat(part);
    if (part.length < PAGE) setsAll = true;
    renderSets();
  } catch (e) {
    $('setList').innerHTML = `<div class="empty">Could not load: ${esc(e.message)}</div>`;
  } finally {
    loadingSets = false;
  }
}

function pickSet(id, view) {
  const m = sets.find((s) => s.id === id);
  const views = m ? Object.keys(m.views || {}) : CAMERAS;
  const list = views.length ? views : CAMERAS;
  $('viewSel').innerHTML = list.map((v) => `<option>${esc(v)}</option>`).join('');
  const want = view && list.includes(view) ? view : (list.includes('left') ? 'left' : list[0]);
  $('viewSel').value = want;
  source = { id, view: want };
  setChip();
  renderSets();
  refresh(true);
}

// ---- the controls ------------------------------------------------------------------------------------------------

function buildMethods() {
  const groups = [];
  for (const m of methods) {
    let g = groups.find((x) => x.name === m.group);
    if (!g) { g = { name: m.group, items: [] }; groups.push(g); }
    g.items.push(m);
  }
  $('methodSel').innerHTML = groups.map((g) => `<optgroup label="${esc(g.name)}">${g.items.map((m) => `<option value="${esc(m.id)}">${esc(m.label)}</option>`).join('')}</optgroup>`).join('');
  $('methodSel').value = method;
  buildParams();
}

function buildParams() {
  const m = byId[method];
  const a = m.about || {};
  const fmt = (t) => esc(t || '').replace(/`([^`]+)`/g, '<code>$1</code>').replace(/\*\*([^*]+)\*\*/g, '<b>$1</b>');
  $('aboutTitle').textContent = m.label;
  $('aboutWhat').innerHTML = fmt(a.what);
  $('aboutRead').innerHTML = fmt(a.read);
  $('aboutUse').innerHTML = fmt(a.use);
  const hinted = m.params.filter((p) => p.hint);
  $('aboutParams').innerHTML = hinted.length ? '<ul>' + hinted.map((p) => `<li><b>${esc(p.label)}</b>: ${esc(p.hint)}</li>`).join('') + '</ul>' : '';
  $('aboutPDt').hidden = $('aboutParams').hidden = !hinted.length;
  $('params').innerHTML = m.params.map((p) => {
    if (p.type === 'select') {
      return `<div class="prm" title="${esc(p.hint || '')}"><label>${esc(p.label)}</label><select data-k="${esc(p.key)}">${p.options.map((o) => `<option${o === p.default ? ' selected' : ''}>${esc(o)}</option>`).join('')}</select></div>`;
    }
    return `<div class="prm" title="${esc(p.hint || '')}"><label>${esc(p.label)}</label><div class="two"><input type="range" data-k="${esc(p.key)}" min="${p.min}" max="${p.max}" step="${p.step}" value="${p.default}">` +
      `<input type="number" data-n="${esc(p.key)}" min="${p.min}" max="${p.max}" step="${p.step}" value="${p.default}"></div></div>`;
  }).join('');
  if (camParam()) {   // the zone technique starts with the camera of the picture
    const sel = $('params').querySelector('select[data-k="camera"]');
    if (sel && source) sel.value = (source.view || source.camera || 'left');
  }
}

const camParam = () => byId[method].params.some((p) => p.key === 'camera');

function params() {
  const out = {};
  $('params').querySelectorAll('[data-k]').forEach((el) => { out[el.dataset.k] = el.type === 'range' ? Number(el.value) : el.value; });
  return out;
}

function crop() {
  const v = (id) => Number($(id).value) / 100;
  const c = [v('cx'), v('cy'), v('cw'), v('ch')];
  return c[0] === 0 && c[1] === 0 && c[2] >= 1 && c[3] >= 1 ? null : c;
}

// ---- asking the server -------------------------------------------------------------------------------------------

function body(m, p) {
  const b = { method: m, params: p, max_side: Number($('sizeSel').value), crop: crop() };
  if (source.token) { b.token = source.token; b.camera = source.camera; } else { b.id = source.id; b.view = source.view; }
  return b;
}

function show(which, data, err) {
  const img = $(which === 'orig' ? 'origImg' : 'resImg');
  const info = $(which === 'orig' ? 'origInfo' : 'resInfo');
  if (err) { info.textContent = err; img.hidden = true; return; }
  img.src = data.image;
  img.hidden = false;
  info.textContent = `${data.width}×${data.height}` + (data.source_size && (data.source_size[0] !== data.width) ? ` (of ${data.source_size[0]}×${data.source_size[1]})` : '') + ` · ${data.ms} ms`;
  if (which === 'res') {
    $('dl').href = data.image;
    $('dl').download = `${method}.jpg`;
    $('dl').hidden = false;
    $('info').textContent = data.info || '';
    $('infoCard').hidden = !data.info;
  }
}

async function run(which, m, p) {
  const n = ++seq[which];
  if (which === 'res') $('resInfo').textContent = 'working…';
  try {
    const data = await post('/api/imagelab/process', body(m, p));
    if (n === seq[which]) show(which, data);
  } catch (e) {
    if (n === seq[which]) show(which, null, e.message);
    if (which === 'res' && n === seq[which]) { $('info').textContent = ''; $('infoCard').hidden = true; }
  }
}

function refresh(withOriginal) {
  if (!source) return;
  if (withOriginal) run('orig', 'original', {});
  run('res', method, params());
}

function schedule() {
  clearTimeout(timer);
  timer = setTimeout(() => refresh(false), 280);
}

// ---- events -------------------------------------------------------------------------------------------------------

$('srcTabs').onclick = (e) => {
  const b = e.target.closest('button[data-t]');
  if (!b) return;
  document.querySelectorAll('#srcTabs button').forEach((x) => x.classList.toggle('active', x === b));
  $('setBox').hidden = b.dataset.t !== 'set';
  $('upBox').hidden = b.dataset.t !== 'upload';
};
$('setQ').oninput = renderSets;
$('setMore').onclick = loadSets;
$('setList').onclick = (e) => {
  const row = e.target.closest('.lab-set');
  if (row) pickSet(row.dataset.id, $('viewSel').value);
};
$('viewSel').onchange = () => { if (source && source.id) { source.view = $('viewSel').value; setChip(); refresh(true); } };
$('upFile').onchange = async () => {
  const f = $('upFile').files[0];
  if (!f) return;
  $('upInfo').textContent = 'Uploading…';
  try {
    const dataUrl = await new Promise((ok, bad) => { const r = new FileReader(); r.onload = () => ok(r.result); r.onerror = bad; r.readAsDataURL(f); });
    const u = await post('/api/imagelab/upload', { data: dataUrl });
    source = { token: u.token, camera: $('upCam').value, name: f.name };
    $('upInfo').textContent = `${f.name}: ${u.width}×${u.height}`;
    setChip();
    refresh(true);
  } catch (e) {
    $('upInfo').textContent = 'Could not use this picture: ' + e.message;
  }
};
$('upCam').onchange = () => { if (source && source.token) { source.camera = $('upCam').value; refresh(false); } };
$('methodSel').onchange = () => { method = $('methodSel').value; buildParams(); if ($('auto').checked) refresh(false); };
$('params').addEventListener('input', (e) => {
  const el = e.target;
  if (el.dataset.k && el.type === 'range') {
    const n = $('params').querySelector(`[data-n="${el.dataset.k}"]`);
    if (n) n.value = el.value;
  } else if (el.dataset.n) {
    const r = $('params').querySelector(`[data-k="${el.dataset.n}"]`);
    if (r) r.value = el.value;
  }
  if ($('auto').checked) schedule();
});
$('params').addEventListener('change', (e) => { if (e.target.tagName === 'SELECT' && $('auto').checked) schedule(); });
$('applyBtn').onclick = () => refresh(false);
$('sizeSel').onchange = () => refresh(true);
['cx', 'cy', 'cw', 'ch'].forEach((id) => { $(id).onchange = () => refresh(true); });
$('cropQr').onclick = () => {
  if (!qrRoi) { $('info').textContent = 'ON_QR_ROI is not set: the whole picture is searched'; $('infoCard').hidden = false; return; }
  [$('cx'), $('cy'), $('cw'), $('ch')].forEach((el, i) => { el.value = Math.round(qrRoi[i] * 1000) / 10; });
  refresh(true);
};
$('cropReset').onclick = () => { $('cx').value = 0; $('cy').value = 0; $('cw').value = 100; $('ch').value = 100; refresh(true); };

function openLightbox(img, caption) {
  if (img.hidden || !img.src) return;
  $('lbImg').src = img.src;
  $('lbImg').classList.remove('full');
  $('lbCaption').textContent = caption;
  $('lb').hidden = false;
}
$('origImg').onclick = () => openLightbox($('origImg'), 'Original');
$('resImg').onclick = () => openLightbox($('resImg'), 'Result: ' + byId[method].label);
$('lbImg').onclick = () => $('lbImg').classList.toggle('full');
$('lbClose').onclick = () => { $('lb').hidden = true; };
$('lb').onclick = (e) => { if (e.target === $('lb') || e.target.id === 'lbWrap') $('lb').hidden = true; };
document.addEventListener('keydown', (e) => { if (e.key === 'Escape') $('lb').hidden = true; });

// ---- start --------------------------------------------------------------------------------------------------------

(async function start() {
  $('upCam').innerHTML = CAMERAS.map((c) => `<option${c === 'left' ? ' selected' : ''}>${c}</option>`).join('');
  try {
    const d = await getJson('/api/imagelab/methods');
    methods = d.methods;
    qrRoi = d.qr_roi;
    byId = Object.fromEntries(methods.map((m) => [m.id, m]));
    buildMethods();
  } catch (e) {
    $('aboutTitle').textContent = 'Could not load the techniques';
    $('aboutWhat').textContent = e.message;
    return;
  }
  await loadSets();
  const q = new URLSearchParams(location.search);
  if (q.get('id')) pickSet(q.get('id'), q.get('view'));
  else if (sets.length) pickSet(sets[0].id, 'left');
})();
