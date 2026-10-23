// The Predict page: the defect detection results of the worker (backend/predict/worker.py). Nothing is polled: the
// first load fetches the list, then the server says "predict_changed" (SSE, group "predict") and the list is fetched
// again. The boxes are drawn here over the pictures of the set from the fractions in the result.

const PAGE = 60;
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s == null ? '' : s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const ORDER = ['top', 'back', 'left', 'front', 'right'];

let flt = 'all';
let corrected = null;   // null until the server says what it starts with
let rows = [];
let totals = null;
let okClasses = [];
let selected = null;
let zones = {};   // the learned shoe zone of each camera (outline polygons), see storage/zones.py
let zoneStatus = null;
let zoneStamp = null;
let current = null;   // the result shown on the right
let currentCtx = null;   // and what helps to judge it (data.context)
let loading = false;
let again = false;

const imageUrl = (id, role, kind) => `/api/samples/${encodeURIComponent(id)}/${kind}?view=${encodeURIComponent(role)}`;

async function getJson(url) {
  const r = await fetch(url, { cache: 'no-store' });
  if (!r.ok) throw new Error(url + ' ' + r.status);
  return r.json();
}

function query(offset) {
  const q = new URLSearchParams({ filter: flt, limit: String(PAGE), offset: String(offset) });
  if (corrected !== null) q.set('corrected', corrected ? '1' : '0');
  if ($('dateSel').value) q.set('date', $('dateSel').value);
  if ($('clsSel').value) q.set('cls', $('clsSel').value);
  return '/api/predict?' + q;
}

function badge(r) {
  if (r.error || r.defect == null) return '<span class="badge error">ERROR</span>';
  return r.defect ? '<span class="badge defect">DEFECT</span>' : '<span class="badge ok">OK</span>';
}

// the raw verdict of the model next to a corrected one that differs
function rawNote(r) {
  if (!corrected || r.error || r.raw_defect == null || !!r.raw_defect === !!r.defect) return '';
  return `<span class="rawnote" title="The model alone said DEFECT (${r.raw_count} detections); after the corrections nothing counts">raw: ${r.raw_defect ? 'DEFECT' : 'OK'}</span>`;
}

// Save: the pictures with the boxes drawn + result.json per set go into exports/predict_<time>/ on the computer that
// runs the app, and a zip of it can be downloaded here.
async function saveSets(body, button) {
  const msg = $('saveMsg');
  const label = button.textContent;
  button.disabled = true;
  button.textContent = 'Saving…';
  msg.hidden = false;
  msg.className = 'hint';
  msg.textContent = 'Saving the pictures and result.json (about a second per set)…';
  try {
    const r = await fetch('/api/predict/export', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ...body, corrected: !!corrected, originals: $('saveOrig').checked }),
    });
    const data = await r.json();
    if (!r.ok) throw new Error(data.error || r.status);
    const skipped = Object.keys(data.skipped || {}).length;
    msg.className = 'hint savedok';
    msg.innerHTML = `Saved ${data.saved} set${data.saved === 1 ? '' : 's'} (${data.corrected ? 'with corrections' : 'raw'}) to <span class="mono">${esc(data.dir)}</span> · ` +
      `<a href="/exports/${encodeURIComponent(data.zip)}">Download zip</a>` +
      (skipped ? ` · ${skipped} skipped` : '') + (data.capped ? ` · ${data.capped} more were left out (the most that goes into one save)` : '');
  } catch (e) {
    msg.className = 'hint saveerr';
    msg.textContent = 'Could not save: ' + e.message;
  } finally {
    button.disabled = false;
    button.textContent = label;
  }
}

function renderWorker(status) {
  const chip = $('workerChip');
  if (!status || !status.alive) {
    chip.className = 'chip warn';
    chip.textContent = 'worker: not running';
    chip.title = 'Start it with ./bin/install_launchd.sh predict-install (docs/predict.md)';
    return;
  }
  chip.className = 'chip ok';
  chip.textContent = `worker: ${status.device} · ${status.avg_ms == null ? '-' : status.avg_ms} ms/set · ${status.pending} waiting`;
  chip.title = `model ${status.model}, ${status.imgsz}px, ${status.processed} predicted, ${status.failed} failed`;
}

function fillSelect(sel, items, all) {
  const cur = sel.value;
  sel.innerHTML = `<option value="">${all}</option>` +
    items.map(([v, n]) => `<option value="${esc(v)}">${esc(v)} (${n})</option>`).join('');
  sel.value = items.some(([v]) => v === cur) ? cur : '';
}

function renderFilters() {
  $('nAll').textContent = totals.all;
  $('nDefect').textContent = totals.defect;
  $('nOk').textContent = totals.ok;
  $('nError').textContent = totals.error;
  fillSelect($('dateSel'), Object.entries(totals.dates), 'All dates');
  fillSelect($('clsSel'), Object.entries(totals.classes), 'All classes');
  const list = Object.entries(totals.classes);
  $('statCard').hidden = !list.length;
  $('classRows').innerHTML = list.map(([c, n]) =>
    `<tr><td>${esc(c)}${okClasses.includes(c) ? ' <span class="chip ok">fine</span>' : ''}</td><td class="num">${n}</td></tr>`).join('');
  $('statHint').textContent = 'A detection of a class marked "fine" does not make a set a defect.';
}

function renderRows() {
  const box = $('rows');
  if (!rows.length) {
    box.innerHTML = `<div class="empty">${totals && totals.all ? 'Nothing matches the filter' : 'No results yet. Is the worker running?'}</div>`;
  } else {
    box.innerHTML = rows.map((r) => `
      <div class="pred-row${r.id === selected ? ' sel' : ''}" data-id="${esc(r.id)}">
        <img loading="lazy" alt="" src="${imageUrl(r.id, r.defect_views[0] || 'top', 'thumb')}" onerror="this.style.visibility='hidden'">
        <div><div class="t">${esc(r.time.replace('T', ' ').slice(0, 19))}</div>
          <div class="s">${esc(r.shoe_id || 'no QR value')}${r.count ? ' · ' + esc(Object.keys(r.classes).join(', ')) : ''}</div></div>
        <div class="rb">${badge(r)}${rawNote(r)}</div>
      </div>`).join('');
  }
  $('moreBtn').hidden = !totals || rows.length >= totals.matching;
}

async function load(append) {
  if (loading) { again = true; return; }
  loading = true;
  try {
    const data = await getJson(query(append ? rows.length : 0));
    totals = data.totals;
    okClasses = totals.ok_classes || [];
    if (corrected === null) corrected = !!totals.corrected;
    $('corr').checked = corrected;
    $('saveAll').textContent = totals.matching ? `Save shown (${totals.matching})` : 'Save shown';
    $('saveAll').disabled = !totals.matching;
    $('corrInfo').textContent = corrected ? `${totals.raw_defect} → ${totals.defect} defect sets` : '';
    rows = append ? rows.concat(data.rows) : data.rows;
    renderWorker(data.status);
    loadZones();
    renderFilters();
    renderRows();
    if (!selected && !append && rows.length) showSet(rows[0].id);   // the newest set is shown at first
  } catch (e) {
    $('rows').innerHTML = `<div class="empty">Could not load: ${esc(e.message)}</div>`;
  } finally {
    loading = false;
    if (again) { again = false; load(false); }
  }
}

// ctx: the context of the same detections (where they lie, whether they count), see storage/predictions.py.
// With the corrections on, a detection that does not count is drawn dashed and dim with the reason.
function boxes(dets, ctx) {
  return dets.map((d, i) => {
    const [x0, y0, x1, y1] = d.box;
    const c = (ctx && ctx[i]) || {};
    const left = corrected && c.counted === false;
    const fine = okClasses.includes(d.class);
    const why = left && c.reasons && c.reasons.length ? ' · ' + c.reasons.join(', ') : '';
    return `<div class="det${fine ? ' fine' : ''}${left ? ' off' : ''}" style="left:${x0 * 100}%;top:${y0 * 100}%;width:${(x1 - x0) * 100}%;height:${(y1 - y0) * 100}%">` +
      `<i>${esc(d.class)} ${d.conf.toFixed(2)}${esc(why)}</i></div>`;
  }).join('');
}

const REASON_TEXT = { fine: 'fine class', recurring: 'recurring', 'off shoe': 'off shoe' };
const statusChip = (c) => {
  if (c.counted == null) return '-';
  if (c.counted) return '<span class="chip ng">counts</span>';
  return `<span class="chip warn" title="Left out of the verdict">${c.reasons.map((r) => REASON_TEXT[r] || r).join(' + ')}</span>`;
};

const SHOE_TEXT = { on: 'on shoe', off: 'off shoe', unknown: 'unknown' };

// The learned shoe zone of a camera drawn over its picture (the outline, in fractions of the picture)
function zoneSvg(role) {
  const z = zones[role];
  if (!z || !$('showZone').checked) return '';
  const polys = z.polygons.map((p) => `<polygon points="${p.map((q) => q.join(',')).join(' ')}"/>`).join('');
  return `<svg class="zone" viewBox="0 0 1 1" preserveAspectRatio="none">${polys}</svg>`;
}

async function loadZones() {
  try {
    zoneStatus = await getJson('/api/predict/zones');
  } catch (e) {
    return;
  }
  const stamp = zoneStatus.built_at || '';
  zones = zoneStatus.cameras || {};
  if (stamp !== zoneStamp) {   // the zones were made (again): the open set shows them
    zoneStamp = stamp;
    if (selected && current) showSet(selected);
  }
  const info = $('zoneInfo');
  if (!zoneStatus.enabled) info.textContent = 'Shoe zone: off (ON_PREDICT_ZONE=0)';
  else if (zoneStatus.building) info.textContent = 'Shoe zone: being made…';
  else if (!zoneStatus.built_at) info.textContent = `Shoe zone: not made yet (it needs ${zoneStatus.min_sets} results)`;
  else info.textContent = `Shoe zone: from ${Math.max(...Object.values(zones).map((c) => c.sets), 0)} sets, made ${zoneStatus.built_at.replace('T', ' ')}`;
  $('zoneBtn').disabled = !!zoneStatus.building || !zoneStatus.enabled;
}
const mapChip = (m) => (m ? `<span class="chip ${m.map50 >= 0.5 ? 'ok' : (m.map50 < 0.2 ? 'warn' : '')}" title="Validation mAP50 of the model for this class (${m.instances} objects in the validation pictures)">${m.map50.toFixed(2)}</span>` : '-');

const rank = (role) => (ORDER.indexOf(role) < 0 ? 9 : ORDER.indexOf(role));

async function showSet(id) {
  selected = id;
  renderRows();
  let data;
  try {
    data = await getJson('/api/predict/' + encodeURIComponent(id));
  } catch (e) {
    $('detail').innerHTML = `<div class="empty">${esc(e.message)}</div>`;
    return;
  }
  const res = data.result;
  const ctx = data.context || { views: {}, classes: {} };
  current = res;
  currentCtx = ctx;
  const roles = (data.views.length ? data.views : Object.keys(res.views || {})).slice().sort((a, b) => rank(a) - rank(b));
  const all = [];
  const cells = roles.map((role) => {
    const v = (res.views || {})[role];
    const err = (res.errors || {})[role];
    const dets = v ? v.detections : [];
    const vc = (ctx.views || {})[role] || {};
    dets.forEach((d, i) => all.push({ role, class: d.class, conf: d.conf, c: (vc.detections || [])[i] || {}, sharp: vc.sharpness }));
    const sharp = vc.sharpness == null ? '' : ` · sharp ${Math.round(vc.sharpness)}`;
    const info = v ? `${dets.length} found${sharp} · ${Math.round(v.ms)} ms` : esc(err || 'not predicted');
    return `<div class="pred-cell"><div class="cap"><b>${esc(role)}</b><span>${info}</span></div>
      <div class="pred-box" data-role="${esc(role)}"><img loading="lazy" alt="" src="${imageUrl(id, role, 'thumb')}">${zoneSvg(role)}${boxes(dets, vc.detections)}</div></div>`;
  }).join('');
  const table = all.length
    ? '<div class="card"><h3>Detections</h3><table class="qr-table"><thead><tr><th>Camera</th><th>Class</th><th class="num">Score</th>' +
      '<th title="Does the box lie on the shoe? (the shoe region found when the set was saved)">On shoe</th>' +
      '<th title="Recurring = the same camera has this class at the same place in many other sets: not a defect of this shoe. Counts = it decides the verdict.">Verdict</th>' +
      '<th class="num" title="In how many detections of other sets the same camera has this class at the same place">Recurs</th>' +
      '<th class="num" title="Validation mAP50 of the model for this class: how well it found this class in its own test pictures">Class mAP50</th>' +
      '<th class="num" title="Sharpness of the picture of this camera (higher = sharper; compare pictures of the same camera)">Sharp</th></tr></thead><tbody>' +
      all.sort((a, b) => b.conf - a.conf).map((d) => `<tr><td>${esc(d.role)}</td><td>${esc(d.class)}${okClasses.includes(d.class) ? ' <span class="chip ok">fine</span>' : ''}</td><td class="num">${d.conf.toFixed(2)}</td>` +
        `<td><span class="chip ${d.c.on_shoe === 'off' ? 'warn' : (d.c.on_shoe === 'on' ? 'ok' : '')}"${d.c.inside != null ? ` title="${Math.round(d.c.inside * 100)}% of the box is inside the ${d.c.by === 'zone' ? 'learned shoe zone of this camera' : 'shoe region of this picture'}"` : ''}>${SHOE_TEXT[d.c.on_shoe] || 'unknown'}${d.c.by === 'zone' && d.c.on_shoe !== 'unknown' ? ' (zone)' : ''}</span></td>` +
        `<td>${statusChip(d.c)}</td><td class="num">${d.c.recurring == null ? '-' : d.c.recurring}</td>` +
        `<td class="num">${mapChip((ctx.classes || {})[d.class])}</td><td class="num">${d.sharp == null ? '-' : Math.round(d.sharp)}</td></tr>`).join('') +
      '</tbody></table><div class="hint">With the corrections on, a box that does not count (recurring, off the shoe or a fine class) is drawn dashed and dim, and only the others decide DEFECT / OK; "raw" is what the model alone would say. A recurring box can also be a real defect that every shoe has: look at those once. "unknown": no shoe region was stored for that picture (sets saved with ON_PICK_MODE=classic) or it covers most of the picture and is not trusted. Class mAP50 is a property of the model, not of this detection: a low value means the model finds that class badly even in its own tests.</div></div>'
    : '<div class="hint">Nothing was detected in any picture.</div>';
  $('detail').dataset.id = id;
  $('detail').innerHTML = `<div class="pred-head"><h2>${esc(res.shoe_id || 'no QR value')} ${badge(verdictRow(ctx, res))}</h2>
    <div class="meta">${esc((res.timestamp || '').replace('T', ' ').slice(0, 19))} · predicted ${esc((res.predicted_at || '').replace('T', ' '))} ·
      ${esc(res.model)} on ${esc(res.device)}, ${res.imgsz}px, score ≥ ${res.conf} · ${res.ms} ms ·
      <a class="linkbtn" style="padding:2px 10px" href="/browse?sample=${encodeURIComponent(id)}">Open in browse</a>
      · <button type="button" id="saveOne" class="secondary" style="padding:2px 10px" title="Save this set: the pictures with the boxes and a result.json">Save this set</button></div>
    ${res.error ? `<div class="card">Failed: ${esc(res.error)}</div>` : ''}</div>
    <div class="pred-grid">${cells}</div>${table}`;
}

// the verdict of a set as the page shows it: corrected or raw (from the context of the detail)
function verdictRow(ctx, res) {
  const v = ctx.verdict || {};
  return { error: res.error, defect: corrected && 'corrected' in v ? v.corrected : res.defect };
}

function openLightbox(id, role) {
  const v = current && current.views ? current.views[role] : null;
  $('lbImg').src = imageUrl(id, role, 'image');
  $('lbBox').querySelectorAll('.det').forEach((n) => n.remove());
  const vc = currentCtx && currentCtx.views ? currentCtx.views[role] : null;
  $('lbBox').insertAdjacentHTML('beforeend', boxes(v ? v.detections : [], vc ? vc.detections : null));
  $('lbCaption').textContent = `${role.toUpperCase()} · ${id}`;
  $('lbOpen').href = imageUrl(id, role, 'image');
  $('lb').hidden = false;
}

function connect() {
  const es = new EventSource('/events?topics=predict');
  es.onmessage = (ev) => {
    let m;
    try { m = JSON.parse(ev.data); } catch (e) { return; }
    if (m.key === 'predict_changed' || m.key === 'resync') load(false);
  };
}

$('flt').onclick = (e) => {
  const b = e.target.closest('button[data-f]');
  if (!b) return;
  flt = b.dataset.f;
  document.querySelectorAll('#flt button').forEach((x) => x.classList.toggle('active', x === b));
  load(false);
};
$('corr').onchange = () => { corrected = $('corr').checked; load(false); if (selected) showSet(selected); };
$('showZone').onchange = () => { if (selected) showSet(selected); };
$('zoneBtn').onclick = async () => {
  if (!window.confirm('Make the shoe zones again from the latest sets? Do it after a camera was moved, when the latest sets are from the new position.')) return;
  $('zoneBtn').disabled = true;
  $('zoneInfo').textContent = 'Shoe zone: being made…';
  try {
    await fetch('/api/predict/zones/rebuild', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' });
  } catch (e) { /* the status line below says what is going on */ }
  loadZones();
};
$('saveAll').onclick = () => {
  const n = totals ? totals.matching : 0;
  if (n > 50 && !window.confirm(`Save ${n} sets? Every set takes about a second and some disk space.`)) return;
  saveSets({ filter: flt, cls: $('clsSel').value || null, date: $('dateSel').value || null }, $('saveAll'));
};
$('detail').addEventListener('click', (e) => {
  const b = e.target.closest('#saveOne');
  if (b && selected) saveSets({ ids: [selected] }, b);
});
$('dateSel').onchange = () => load(false);
$('clsSel').onchange = () => load(false);
$('moreBtn').onclick = () => load(true);
$('rows').onclick = (e) => {
  const row = e.target.closest('.pred-row');
  if (row) showSet(row.dataset.id);
};
$('detail').onclick = (e) => {
  const box = e.target.closest('.pred-box[data-role]');
  if (box) openLightbox($('detail').dataset.id, box.dataset.role);
};
$('lbClose').onclick = () => { $('lb').hidden = true; };
$('lb').onclick = (e) => { if (e.target === $('lb') || e.target.classList.contains('pred-lbwrap')) $('lb').hidden = true; };
document.addEventListener('keydown', (e) => { if (e.key === 'Escape') $('lb').hidden = true; });

load(false);
connect();
