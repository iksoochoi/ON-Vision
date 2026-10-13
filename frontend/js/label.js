const STATUS_TEXT = { ok: 'OK', ng: 'NG', reject: 'Reject', unlabeled: 'Unlabeled' };
const SHOT_TEXT = { before: 'Before center', center: 'Center', after: 'After center' };

let items = [];
let idx = -1;
let shownId = null;
let categories = [];
let draft = { status: null, defects: new Set(), views: new Set(), note: '' };
let curView = null;  // camera view shown in the stage (the primary view = trigger camera)
const params = new URLSearchParams(location.search);
let pendingSelectId = params.get('id');

const $ = (id) => document.getElementById(id);

async function api(method, url, body) {
  const r = await fetch(url, {
    method,
    headers: body ? { 'Content-Type': 'application/json' } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!r.ok) {
    let msg = 'HTTP ' + r.status;
    try { msg = (await r.json()).error || msg; } catch (e) {}
    throw new Error(msg);
  }
  return r.json();
}

const labelStatus = (m) => (m.label ? m.label.status : 'unlabeled');

// A sample is a set of camera views; older single-camera samples have just the primary view.
const primaryOf = (m) => m.primary_view || 'top';
const viewsOf = (m) => (m.views && Object.keys(m.views).length ? Object.keys(m.views) : [primaryOf(m)]);
const viewUrl = (m, role, kind = 'image') =>
  `/api/samples/${m.id}/${kind}` + (role && role !== primaryOf(m) ? `?view=${encodeURIComponent(role)}` : '');

async function loadCategories() {
  categories = await (await fetch('/categories')).json();
  const box = $('defects');
  box.innerHTML = '';
  for (const { group, items: list } of categories) {
    const g = document.createElement('div');
    g.className = 'grp';
    g.textContent = group;
    box.appendChild(g);
    for (const item of list) {
      const value = group + '/' + item;
      const lab = document.createElement('label');
      const cb = document.createElement('input');
      cb.type = 'checkbox';
      cb.value = value;
      cb.onchange = () => {
        if (cb.checked) draft.defects.add(value); else draft.defects.delete(value);
      };
      lab.appendChild(cb);
      lab.appendChild(document.createTextNode(' ' + item));
      box.appendChild(lab);
    }
  }
}

async function loadCounts() {
  const st = await (await fetch('/api/state')).json();
  const c = st.counts;
  $('counts').textContent =
    `Unlabeled ${c.unlabeled} · OK ${c.ok} · NG ${c.ng} · Reject ${c.reject}`
    + ` | boxes ${c.with_bbox} (confirmed ${c.bbox_reviewed})`;
}

async function loadList(keepId) {
  const status = $('filter').value;
  items = await (await fetch('/api/samples?status=' + status)).json();
  renderThumbs();
  loadCounts().catch(() => {});
  if (pendingSelectId) {
    keepId = pendingSelectId;
    pendingSelectId = null;
  }
  const found = keepId ? items.findIndex((m) => m.id === keepId) : -1;
  if (found >= 0) {
    if (items[found].id === shownId) {  // same image still on screen: keep the unsaved draft
      idx = found;
      markSelected();
    } else {
      select(found);
    }
  } else {
    select(items.length ? Math.min(Math.max(idx, 0), items.length - 1) : -1);
  }
}

const currentId = () => (idx >= 0 && idx < items.length ? items[idx].id : null);

function renderThumbs() {
  const box = $('thumbs');
  box.innerHTML = '';
  items.forEach((m, i) => {
    const d = document.createElement('div');
    const s = labelStatus(m);
    d.className = 'thumb s-' + s;
    d.dataset.id = m.id;
    d.onclick = () => select(i);
    const im = document.createElement('img');
    im.src = `/api/samples/${m.id}/thumb`;
    im.loading = 'lazy';
    const badge = document.createElement('span');
    badge.className = 'badge';
    badge.textContent = STATUS_TEXT[s];
    const tid = document.createElement('div');
    tid.className = 'tid';
    tid.textContent = m.id + (viewsOf(m).length > 1 ? ` · ${viewsOf(m).length} views` : '')
      + (m.qr && m.qr.length ? ' · QR' : '');
    d.append(im, badge, tid);
    if (m.burst && m.burst.count > 1) {
      const sh = document.createElement('span');
      sh.className = 'shot';
      sh.textContent = SHOT_TEXT[m.burst.kind] || m.burst.kind;
      d.appendChild(sh);
    }
    box.appendChild(d);
  });
  markSelected();
}

function markSelected() {
  document.querySelectorAll('.thumb').forEach((el, i) =>
    el.classList.toggle('selected', i === idx));
  const sel = document.querySelector('.thumb.selected');
  if (sel) sel.scrollIntoView({ block: 'nearest' });
}

function updateThumb(i) {
  const el = document.querySelectorAll('.thumb')[i];
  if (!el) return;
  const s = labelStatus(items[i]);
  el.className = 'thumb s-' + s + (i === idx ? ' selected' : '');
  el.querySelector('.badge').textContent = STATUS_TEXT[s];
}

function select(i) {
  idx = i;
  markSelected();
  const stage = $('stage');
  stage.classList.remove('zoom100');
  $('err').textContent = '';
  shownId = i >= 0 && i < items.length ? items[i].id : null;
  if (!shownId) {
    idx = -1;
    stage.innerHTML = '<div class="empty">No image to show</div>';
    $('sideBody').hidden = true;
    return;
  }
  const m = items[i];
  curView = primaryOf(m);
  stage.innerHTML = '';
  const wrap = document.createElement('div');
  wrap.className = 'imgwrap';
  const im = document.createElement('img');
  im.id = 'mainImg';
  im.src = viewUrl(m, curView);
  im.draggable = false;
  const box = document.createElement('div');
  box.id = 'bboxEl';
  box.hidden = true;
  wrap.append(im, box);
  stage.appendChild(wrap);
  attachDrawing(wrap, im);
  $('sideBody').hidden = false;

  const lab = m.label;
  draft = {
    status: lab ? lab.status : null,
    defects: new Set(lab ? lab.defects : []),
    views: new Set(lab && lab.views ? lab.views : []),
    note: lab ? lab.note : '',
  };
  $('note').value = draft.note;
  renderViews();
  renderBurst();
  renderQrLine();
  renderQrOverlay();
  renderDraft();
  $('meta').innerHTML = metaHtml(m);
  renderBbox();
  history.replaceState(null, '', '/label?id=' + m.id);
}

// ---- shots of one shoe pass (before / center / after) ------------------------------------

function burstMembers(m) {
  if (!m || !m.burst) return [];
  return items.map((x, i) => [x, i])
    .filter(([x]) => x.burst && x.burst.id === m.burst.id)
    .sort((a, b) => a[0].burst.index - b[0].burst.index);
}

function renderBurst() {
  const m = items[idx];
  const nav = $('burstNav');
  nav.innerHTML = '';
  const multi = !!(m && m.burst && m.burst.count > 1);
  nav.hidden = !multi;
  $('burstRow').hidden = !multi;
  if (!multi) return;
  $('burstN').textContent = m.burst.count;
  nav.append('Same shoe:');
  for (const [x, i] of burstMembers(m)) {
    const b = document.createElement('button');
    b.className = 'secondary' + (x.id === m.id ? ' cur' : '');
    b.textContent = SHOT_TEXT[x.burst.kind] || x.burst.kind;
    b.onclick = () => select(i);
    nav.appendChild(b);
  }
}

// ---- QR on the shoe ------------------------------------------------------------------------

function renderQrLine() {
  const m = items[idx];
  const box = $('qrLine');
  if (!m) return;
  const codes = m.qr;
  let html;
  if (codes === undefined) html = 'QR: not read yet';
  else if (!codes.length) html = 'QR: no code found';
  else {
    const texts = [...new Set(codes.map((c) => c.text))];
    html = 'QR ' + texts.map((t) => `<b>${esc(t)}</b>`).join(', ')
      + ` <span class="hint">(${[...new Set(codes.map((c) => c.view.toUpperCase()))].join(', ')} views)</span>`;
  }
  box.innerHTML = html + '<br><button class="secondary" id="qrRedo">Reread QR</button>';
  $('qrRedo').onclick = async () => {
    $('qrRedo').disabled = true;
    try {
      items[idx] = await api('POST', `/api/samples/${m.id}/qr`, {});
      renderQrLine(); renderQrOverlay(); renderThumbs();
    } catch (e) { $('err').textContent = 'QR reread failed: ' + e.message; }
  };
}

// outline of the QR code found in the view that is on screen
function renderQrOverlay() {
  const wrap = document.querySelector('.imgwrap');
  if (!wrap) return;
  const old = wrap.querySelector('.qrsvg');
  if (old) old.remove();
  const m = items[idx];
  const found = ((m && m.qr) || []).filter((c) => c.view === curView);
  if (!found.length) return;
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.setAttribute('class', 'qrsvg');
  svg.setAttribute('viewBox', '0 0 1 1');
  svg.setAttribute('preserveAspectRatio', 'none');
  for (const c of found) {
    const poly = document.createElementNS('http://www.w3.org/2000/svg', 'polygon');
    poly.setAttribute('points', c.points.map((p) => p.join(',')).join(' '));
    svg.appendChild(poly);
  }
  wrap.appendChild(svg);
}

// ---- camera views ---------------------------------------------------------------

function renderViews() {
  const m = items[idx];
  const box = $('views');
  box.innerHTML = '';
  if (!m) return;
  const shown = viewsOf(m);
  box.hidden = shown.length < 2 && !m.missing_views;
  for (const role of shown) {
    const b = document.createElement('button');
    b.className = 'secondary' + (role === curView ? ' active' : '');
    b.textContent = role.toUpperCase() + (role === primaryOf(m) ? ' ★' : '');
    b.title = role === primaryOf(m) ? 'Trigger camera (box reference)' : '';
    b.onclick = () => showView(role);
    box.appendChild(b);
  }
  for (const [role, why] of Object.entries(m.missing_views || {})) {
    const b = document.createElement('button');
    b.className = 'secondary';
    b.disabled = true;
    b.textContent = role.toUpperCase();
    b.title = 'Missing: ' + why;
    box.appendChild(b);
  }
}

function showView(role) {
  const m = items[idx];
  if (!m || !viewsOf(m).includes(role)) return;
  curView = role;
  $('mainImg').src = viewUrl(m, role);
  renderViews();
  renderBbox();
  renderQrOverlay();
}

function cycleView() {
  const m = items[idx];
  if (!m) return;
  const list = viewsOf(m);
  showView(list[(list.indexOf(curView) + 1) % list.length]);
}

// ---- bbox ---------------------------------------------------------------------

const BBOX_SOURCE = { background: 'Auto (background)', model: 'Auto (model)', manual: 'Manual' };

function renderBbox() {
  const m = items[idx];
  const el = $('bboxEl');
  const info = $('bboxInfo');
  const bb = m && m.bbox;
  const onPrimary = !m || curView === primaryOf(m);  // the bbox belongs to the trigger camera's image
  if (el) {
    el.hidden = !bb || !onPrimary;
    if (bb) {
      el.className = 'bbox' + (bb.reviewed ? ' reviewed' : '');
      el.style.left = bb.x * 100 + '%';
      el.style.top = bb.y * 100 + '%';
      el.style.width = bb.w * 100 + '%';
      el.style.height = bb.h * 100 + '%';
    }
  }
  if (!m) return;
  info.innerHTML = bb
    ? `<b>bbox</b> <span class="tag ${bb.reviewed ? 'ok' : 'warn'}">${bb.reviewed ? 'Confirmed' : 'Unconfirmed'}</span> `
      + `<span class="tag">${BBOX_SOURCE[bb.source] || bb.source}</span>`
    : '<b>bbox</b> <span class="tag">None</span> Drag to draw one, or press Auto';
  if (!onPrimary) info.innerHTML += `<div class="hint">The box belongs to the ${primaryOf(m).toUpperCase()} ★ view. Switch back to that view to edit it.</div>`;
  $('bboxConfirm').disabled = !bb || bb.reviewed || !onPrimary;
  $('bboxClear').disabled = !bb || !onPrimary;
  $('bboxAuto').disabled = !onPrimary;
}

async function bboxCall(body) {
  const m = items[idx];
  if (!m) return;
  try {
    items[idx] = await api('POST', `/api/samples/${m.id}/bbox`, body);
    $('err').textContent = '';
    renderBbox();
    $('meta').innerHTML = metaHtml(items[idx]);
    loadCounts().catch(() => {});
  } catch (e) {
    $('err').textContent = e.message === 'no background'
      ? 'No background image. Capture a background on the Live page first'
      : 'Box error: ' + e.message;
  }
}

function attachDrawing(wrap, im) {
  wrap.addEventListener('mousedown', (e) => {
    if (e.button !== 0) return;
    e.preventDefault();
    const rect = im.getBoundingClientRect();
    const norm = (ev) => [
      Math.min(Math.max((ev.clientX - rect.left) / rect.width, 0), 1),
      Math.min(Math.max((ev.clientY - rect.top) / rect.height, 0), 1),
    ];
    const [sx, sy] = norm(e);
    const startX = e.clientX, startY = e.clientY;
    const canDraw = !!items[idx] && curView === primaryOf(items[idx]);  // other cameras: view only
    let drawing = null;
    const onMove = (ev) => {
      if (!canDraw) return;
      if (!drawing && Math.hypot(ev.clientX - startX, ev.clientY - startY) < 6) return;
      if (!drawing) {
        drawing = document.createElement('div');
        drawing.className = 'bbox drawing';
        wrap.appendChild(drawing);
      }
      const [x, y] = norm(ev);
      drawing.style.left = Math.min(sx, x) * 100 + '%';
      drawing.style.top = Math.min(sy, y) * 100 + '%';
      drawing.style.width = Math.abs(x - sx) * 100 + '%';
      drawing.style.height = Math.abs(y - sy) * 100 + '%';
    };
    const onUp = (ev) => {
      document.removeEventListener('mousemove', onMove);
      document.removeEventListener('mouseup', onUp);
      if (!drawing) {  // plain click: toggle original-size view
        $('stage').classList.toggle('zoom100');
        return;
      }
      drawing.remove();
      const [x, y] = norm(ev);
      bboxCall({ x: Math.min(sx, x), y: Math.min(sy, y), w: Math.abs(x - sx), h: Math.abs(y - sy) });
    };
    document.addEventListener('mousemove', onMove);
    document.addEventListener('mouseup', onUp);
  });
}

function showToast(html, isError, ms) {
  const t = $('toast');
  t.className = 'toast' + (isError ? ' error' : '');
  t.innerHTML = html;
  t.hidden = false;
  clearTimeout(showToast.timer);
  showToast.timer = setTimeout(() => { t.hidden = true; }, ms || 12000);
}

async function exportDataset() {
  $('exportBtn').disabled = true;
  showToast('Exporting… this can take a while when there are many images', false, 120000);
  try {
    const r = await api('POST', '/api/export', { reviewed_only: $('reviewedOnly').checked });
    const sk = r.skipped;
    showToast(
      `Export done: train ${r.train} · val ${r.val} (${r.positives} shoes + ${r.negatives} empty images)<br>`
      + `Skipped: no box ${sk.no_bbox}, unconfirmed ${sk.unreviewed}<br>`
      + `<code>${esc(r.dir)}</code><br><a href="/exports/${encodeURIComponent(r.zip)}">Download zip</a>`,
      false, 30000);
  } catch (e) {
    const msg = e.message.startsWith('no samples') ? 'Nothing to export. Create or draw a box first' : e.message;
    showToast('Export failed: ' + esc(msg), true);
  } finally {
    $('exportBtn').disabled = false;
  }
}

async function autoAll() {
  if (!confirm('Rebuild boxes for every unconfirmed sample from the background.\nConfirmed and manual boxes are kept.')) return;
  $('autoAllBtn').disabled = true;
  try {
    const r = await api('POST', '/api/bbox/auto', { scope: 'unreviewed' });
    showToast(`Auto boxes: processed ${r.processed}, found a shoe in ${r.with_bbox}`, false);
    await loadList(currentId());
  } catch (e) {
    showToast(e.message === 'no background'
      ? 'No background image. Capture a background on the Live page first' : 'Failed: ' + esc(e.message), true);
  } finally {
    $('autoAllBtn').disabled = false;
  }
}

$('bboxConfirm').onclick = () => bboxCall({ confirm: true });
$('bboxAuto').onclick = () => bboxCall({ auto: true });
$('bboxClear').onclick = () => bboxCall({ clear: true });
$('exportBtn').onclick = exportDataset;
$('autoAllBtn').onclick = autoAll;

function renderDraft() {
  for (const s of ['ok', 'ng', 'reject']) {
    $('btn' + s[0].toUpperCase() + s.slice(1)).className = draft.status === s ? 'sel-' + s : '';
  }
  $('defects').hidden = draft.status !== 'ng';
  document.querySelectorAll('#defects input').forEach((cb) => {
    cb.checked = draft.defects.has(cb.value);
  });
  renderViewSel();
}

// NG only: which cameras show the defect (optional, helps when each defect is visible from one side)
function renderViewSel() {
  const m = items[idx];
  const box = $('viewSel');
  const list = m ? viewsOf(m) : [];
  box.hidden = draft.status !== 'ng' || list.length < 2;
  box.innerHTML = '';
  if (box.hidden) return;
  const title = document.createElement('div');
  title.className = 'grp';
  title.textContent = 'Views where the defect is visible (optional)';
  box.appendChild(title);
  for (const role of list) {
    const lab = document.createElement('label');
    const cb = document.createElement('input');
    cb.type = 'checkbox';
    cb.checked = draft.views.has(role);
    cb.onchange = () => { if (cb.checked) draft.views.add(role); else draft.views.delete(role); };
    lab.append(cb, document.createTextNode(' ' + role.toUpperCase()));
    box.appendChild(lab);
  }
}

function setStatus(s) {
  draft.status = s;
  renderDraft();
}

function esc(s) {
  return String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
}

const SHOE_ID_SOURCE = {
  capture: 'Read from the saved picture', capture_zoom: 'Read from an enlarged saved picture',
  live: 'Live scan', live_late: 'Live scan just after capture',
};

function metaHtml(m) {
  const lines = [`<b>${esc(m.id)}</b> (${m.source === 'auto' ? 'Auto' : 'Manual'})`];
  if (m.shoe_id) {
    const other = (m.shoe_id_other || []).length ? ` · also read ${m.shoe_id_other.map(esc).join(', ')}` : '';
    lines.push(`Shoe ID <b>${esc(m.shoe_id)}</b> (${SHOE_ID_SOURCE[m.shoe_id_source] || m.shoe_id_source})${other}`);
  }
  if (m.image) lines.push(`${m.image.width}×${m.image.height}`);
  if (m.detection) {
    const d = m.detection;
    lines.push(`Detection: ${esc(d.label)} ${d.confidence} · center distance ${d.center_dist} · offset ${d.frame_offset_ms}ms`);
  }
  const c = m.camera || {};
  lines.push(`Exposure ${c.exposure_ms || 'auto'} · ISO ${c.iso || 'auto'} · WB ${c.wb || 'auto'}`);
  if (viewsOf(m).length > 1) {
    const skew = viewsOf(m).map((r) => `${esc(r)} ${m.views[r].offset_ms > 0 ? '+' : ''}${m.views[r].offset_ms}ms`);
    lines.push(`View skew: ${skew.join(' · ')}`);
  }
  for (const [role, why] of Object.entries(m.missing_views || {})) {
    lines.push(`<span style="color:var(--warn)">Missing view ${esc(role)}: ${esc(why)}</span>`);
  }
  if (m.label) {
    const seen = m.label.views && m.label.views.length ? ` · views seen: ${m.label.views.map(esc).join(', ')}` : '';
    lines.push(`Label: ${STATUS_TEXT[m.label.status]} (${esc(m.label.labeled_at)})${seen}`);
  }
  return lines.join('<br>');
}

async function save() {
  const m = items[idx];
  if (!m) return;
  if (!draft.status) { $('err').textContent = 'Choose OK, NG, or Reject'; return; }
  if (draft.status === 'ng' && draft.defects.size === 0) {
    $('err').textContent = 'Choose at least one defect type';
    return;
  }
  const applyBurst = !!(m.burst && m.burst.count > 1 && $('applyBurst').checked);
  try {
    const saved = await api('POST', `/api/samples/${m.id}/label`, {
      status: draft.status,
      defects: [...draft.defects],
      views: [...draft.views],
      note: $('note').value,
      apply_burst: applyBurst,
    });
    items[idx] = saved;
    updateThumb(idx);
    if (applyBurst) {   // the server labeled the other shots of this shoe too: mirror it here
      items.forEach((x, j) => {
        if (j !== idx && x.burst && x.burst.id === m.burst.id) {
          items[j] = { ...x, label: saved.label };
          updateThumb(j);
        }
      });
    }
    loadCounts().catch(() => {});
    next(true);
  } catch (e) {
    $('err').textContent = 'Save failed: ' + e.message;
  }
}

function next(preferUnlabeled) {
  if (preferUnlabeled) {
    for (let i = idx + 1; i < items.length; i++) {
      if (labelStatus(items[i]) === 'unlabeled') { select(i); return; }
    }
  }
  if (idx + 1 < items.length) select(idx + 1);
}

async function clearLabel() {
  const m = items[idx];
  if (!m || !m.label) return;
  try {
    await api('DELETE', `/api/samples/${m.id}/label`);
    items[idx] = { ...m, label: null };
    updateThumb(idx);
    select(idx);
    loadCounts().catch(() => {});
  } catch (e) { $('err').textContent = 'Clear failed: ' + e.message; }
}

async function deleteSample() {
  const m = items[idx];
  if (!m || !confirm(`Delete this image?\n${m.id}`)) return;
  try {
    await api('DELETE', `/api/samples/${m.id}`);
    items.splice(idx, 1);
    renderThumbs();
    select(Math.min(idx, items.length - 1));
    loadCounts().catch(() => {});
  } catch (e) { $('err').textContent = 'Delete failed: ' + e.message; }
}

$('btnOk').onclick = () => setStatus('ok');
$('btnNg').onclick = () => setStatus('ng');
$('btnReject').onclick = () => setStatus('reject');
$('saveBtn').onclick = save;
$('clearBtn').onclick = clearLabel;
$('delBtn').onclick = deleteSample;
$('note').oninput = () => { draft.note = $('note').value; };
$('filter').onchange = () => { idx = -1; loadList(); };

document.addEventListener('keydown', (e) => {
  if (e.target.tagName === 'TEXTAREA' || e.target.tagName === 'SELECT') {
    if (e.key === 'Enter' && e.ctrlKey) { e.preventDefault(); save(); }
    return;
  }
  if (e.key === '1') setStatus('ok');
  else if (e.key === '2') setStatus('ng');
  else if (e.key === '3') setStatus('reject');
  else if (e.key === 'v' || e.key === 'V') cycleView();
  else if (e.key === 'b' || e.key === 'B') { if (!$('bboxConfirm').disabled) bboxCall({ confirm: true }); }
  else if (e.key === 'Enter') { e.preventDefault(); save(); }
  else if (e.key === 'ArrowRight') { if (idx + 1 < items.length) select(idx + 1); }
  else if (e.key === 'ArrowLeft') { if (idx > 0) select(idx - 1); }
});

let refreshTimer = null;
new EventSource('/events').onmessage = (e) => {
  let data;
  try { data = JSON.parse(e.data); } catch (err) { return; }
  if (data.key === 'samples_changed') {
    clearTimeout(refreshTimer);
    refreshTimer = setTimeout(() => loadList(currentId()), 400);
  }
};

(async () => {
  await loadCategories();
  try {
    const ui = (await (await fetch('/api/state')).json()).ui;
    document.title = 'ON Data Collector';
    $('filter').value = ui.default_filter;
  } catch (e) {}
  if (pendingSelectId) $('filter').value = 'all';
  await loadList();
})();
