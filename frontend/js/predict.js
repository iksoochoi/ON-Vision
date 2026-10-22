// The Predict page: the defect detection results of the worker (backend/predict/worker.py). Nothing is polled: the
// first load fetches the list, then the server says "predict_changed" (SSE, group "predict") and the list is fetched
// again. The boxes are drawn here over the pictures of the set from the fractions in the result.

const PAGE = 60;
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s == null ? '' : s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const ORDER = ['top', 'back', 'left', 'front', 'right'];

let flt = 'all';
let rows = [];
let totals = null;
let okClasses = [];
let selected = null;
let current = null;   // the result shown on the right
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
  if ($('dateSel').value) q.set('date', $('dateSel').value);
  if ($('clsSel').value) q.set('cls', $('clsSel').value);
  return '/api/predict?' + q;
}

function badge(r) {
  if (r.error || r.defect == null) return '<span class="badge error">ERROR</span>';
  return r.defect ? '<span class="badge defect">DEFECT</span>' : '<span class="badge ok">OK</span>';
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
        ${badge(r)}
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
    rows = append ? rows.concat(data.rows) : data.rows;
    renderWorker(data.status);
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

function boxes(dets) {
  return dets.map((d) => {
    const [x0, y0, x1, y1] = d.box;
    const fine = okClasses.includes(d.class);
    return `<div class="det${fine ? ' fine' : ''}" style="left:${x0 * 100}%;top:${y0 * 100}%;width:${(x1 - x0) * 100}%;height:${(y1 - y0) * 100}%">` +
      `<i>${esc(d.class)} ${d.conf.toFixed(2)}</i></div>`;
  }).join('');
}

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
  current = res;
  const roles = (data.views.length ? data.views : Object.keys(res.views || {})).slice().sort((a, b) => rank(a) - rank(b));
  const all = [];
  const cells = roles.map((role) => {
    const v = (res.views || {})[role];
    const err = (res.errors || {})[role];
    const dets = v ? v.detections : [];
    dets.forEach((d) => all.push({ role, class: d.class, conf: d.conf }));
    const info = v ? `${dets.length} found · ${Math.round(v.ms)} ms` : esc(err || 'not predicted');
    return `<div class="pred-cell"><div class="cap"><b>${esc(role)}</b><span>${info}</span></div>
      <div class="pred-box" data-role="${esc(role)}"><img loading="lazy" alt="" src="${imageUrl(id, role, 'thumb')}">${boxes(dets)}</div></div>`;
  }).join('');
  const table = all.length
    ? '<div class="card"><h3>Detections</h3><table class="qr-table"><thead><tr><th>Camera</th><th>Class</th><th class="num">Score</th></tr></thead><tbody>' +
      all.sort((a, b) => b.conf - a.conf).map((d) => `<tr><td>${esc(d.role)}</td><td>${esc(d.class)}${okClasses.includes(d.class) ? ' <span class="chip ok">fine</span>' : ''}</td><td class="num">${d.conf.toFixed(2)}</td></tr>`).join('') +
      '</tbody></table></div>'
    : '<div class="hint">Nothing was detected in any picture.</div>';
  $('detail').dataset.id = id;
  $('detail').innerHTML = `<div class="pred-head"><h2>${esc(res.shoe_id || 'no QR value')} ${badge(res)}</h2>
    <div class="meta">${esc((res.timestamp || '').replace('T', ' ').slice(0, 19))} · predicted ${esc((res.predicted_at || '').replace('T', ' '))} ·
      ${esc(res.model)} on ${esc(res.device)}, ${res.imgsz}px, score ≥ ${res.conf} · ${res.ms} ms ·
      <a class="linkbtn" style="padding:2px 10px" href="/browse?sample=${encodeURIComponent(id)}">Open in browse</a></div>
    ${res.error ? `<div class="card">Failed: ${esc(res.error)}</div>` : ''}</div>
    <div class="pred-grid">${cells}</div>${table}`;
}

function openLightbox(id, role) {
  const v = current && current.views ? current.views[role] : null;
  $('lbImg').src = imageUrl(id, role, 'image');
  $('lbBox').querySelectorAll('.det').forEach((n) => n.remove());
  $('lbBox').insertAdjacentHTML('beforeend', boxes(v ? v.detections : []));
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
