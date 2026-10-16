// The QR page. Nothing is polled: the first load fetches what is there, then the server pushes (SSE):
//   qr        a code was read          qr_scan   what the live scan of each camera sees (every ~2 s)
//   qr_stats  the sets changed: new statistics (at most every ~2 s)
// If the connection drops (or the server says "resync"), everything is fetched again once.

let history = [];   // newest first, at most MAX_HISTORY
let scanning = {};
let clockSkew = 0;  // server time - this browser's time (s): relative times stay right on another computer
const MAX_HISTORY = 200;
const MAX_ROWS = 100;

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const serverNow = () => Date.now() / 1000 + clockSkew;

function ago(epoch) {
  const s = Math.max(0, Math.round(serverNow() - epoch));
  return s < 60 ? `${s}s ago` : s < 3600 ? `${Math.round(s / 60)}m ago` : `${Math.round(s / 3600)}h ago`;
}

function fmtBytes(n) {
  if (!n) return '0';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let i = 0;
  let v = n;
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i++; }
  return (v >= 100 || i === 0 ? v.toFixed(0) : v.toFixed(1)) + ' ' + units[i];
}

function renderHero() {
  const e = history[0];
  const hero = $('hero');
  if (!e) {
    $('bigText').textContent = 'Waiting…';
    $('bigMeta').textContent = 'A QR code on the side of the shoe appears here when a camera can see it.';
    $('copyBtn').disabled = true;
    $('sampleLink').hidden = true;
    hero.classList.remove('fresh');
    return;
  }
  $('bigText').textContent = e.text;
  $('bigMeta').textContent =
    `${e.role.toUpperCase()} Camera · ${e.source === 'live' ? 'Live scan' : 'Captured image'} · ${ago(e.epoch)} (${e.time.replace('T', ' ')})`;
  $('copyBtn').disabled = false;
  const link = $('sampleLink');
  link.hidden = !e.sample;
  if (e.sample) link.href = '/browse?sample=' + encodeURIComponent(e.sample);
  hero.classList.toggle('fresh', serverNow() - e.epoch < 10);
}

function renderChips() {
  const box = $('scanChips');
  const roles = Object.keys(scanning);
  box.innerHTML = roles.length ? '' : '<span class="hint">No camera has been scanned yet</span>';
  for (const role of roles) {
    const s = scanning[role];
    const chip = document.createElement('span');
    const recent = serverNow() - s.epoch < 10;
    const px = s.size_px ? ` ${Math.round(s.size_px)}px` : '';
    const located = !s.found && s.size_px;   // a code was placed in the picture but could not be read
    chip.className = 'chip ' + (s.found ? 'ok' : (located || !recent ? 'warn' : ''));
    chip.title = located ? 'A QR code was located but could not be read: too small or blurred (needs ~100px+ for a 25-29 module code)' : '';
    chip.textContent = `${role.toUpperCase()} · ${s.found ? 'QR seen' + px : (located ? 'QR located, not readable' + px : 'None')} · ${ago(s.epoch)}`;
    box.appendChild(chip);
  }
}

function renderRows() {
  const tbody = $('rows');
  if (!history.length) {
    tbody.innerHTML = '<tr><td colspan="5" class="empty-row">None yet</td></tr>';
    return;
  }
  tbody.innerHTML = '';
  for (const e of history.slice(0, MAX_ROWS)) {   // the table never grows past MAX_ROWS rows
    const tr = document.createElement('tr');
    tr.innerHTML =
      `<td>${esc(e.time.replace('T', ' '))}</td><td>${esc(e.role.toUpperCase())}</td>` +
      `<td class="mono">${esc(e.text)}</td>` +
      `<td><span class="chip ${e.source === 'capture' ? 'ok' : ''}">${e.source === 'live' ? 'Live' : 'Capture'}</span></td>` +
      `<td>${e.sample ? `<a href="/browse?sample=${encodeURIComponent(e.sample)}">Image</a>` : ''}</td>`;
    tbody.appendChild(tr);
  }
}

const openDays = new Set();   // dates whose hours are shown
let dailyData = [];
let sizeData = null, sourceData = {}, methodData = {};

function renderDaily(data) {
  if (data) {
    dailyData = data.days || [];
    sizeData = data.sizes || null;
    sourceData = data.sources || {};
    methodData = data.methods || {};
  }
  renderSizes();
  const tbody = $('dailyRows'), foot = $('dailyTotal');
  if (!dailyData.length) {
    tbody.innerHTML = '<tr><td colspan="6" class="empty-row">None yet</td></tr>';
    foot.innerHTML = '';
    return;
  }
  const rate = (ok, n) => (n ? (100 * ok / n).toFixed(1) + '%' : '-');
  // Size: what the sets' pictures and thumbnails take on the disk; the title gives the average per set
  const cells = (r) => `<td class="num">${r.sets}</td><td class="num">${r.ok}</td>` +
    `<td class="num${r.fail ? ' bad' : ''}">${r.fail}${r.fail ? ` <small class="dim">(${r.seen} seen)</small>` : ''}</td>` +
    `<td class="num">${rate(r.ok, r.sets)}</td>` +
    `<td class="num" title="${r.sets ? fmtBytes(r.size / r.sets) + ' per set' : ''}">${fmtBytes(r.size)}</td>`;
  let html = '';
  for (const d of dailyData) {
    const open = openDays.has(d.date);
    html += `<tr class="day" data-date="${esc(d.date)}" title="Click to ${open ? 'hide' : 'show'} the hours">` +
      `<td><span class="caret">${open ? '▾' : '▸'}</span> ${esc(d.date)}</td>${cells(d)}</tr>`;
    if (open) {
      for (const h of d.hours || []) {
        const label = h.hour < 0 ? 'unknown' : String(h.hour).padStart(2, '0') + ':00';
        html += `<tr class="hour"><td>${label}</td>${cells(h)}</tr>`;
      }
    }
  }
  tbody.innerHTML = html;
  const sum = dailyData.reduce((a, d) => ({ sets: a.sets + d.sets, ok: a.ok + d.ok, fail: a.fail + d.fail, seen: a.seen + d.seen, size: a.size + (d.size || 0) }),
    { sets: 0, ok: 0, fail: 0, seen: 0, size: 0 });
  foot.innerHTML = `<tr><td>Total</td>${cells(sum)}</tr>`;
}

function renderSizes() {
  const tbody = $('sizeRows');
  if (!sizeData) return;
  const sum = (list) => list.reduce((a, b) => a + b, 0);
  if (!(sum(sizeData.read) + sizeData.read_no_size + sum(sizeData.unread))) {
    tbody.innerHTML = '<tr><td colspan="3" class="empty-row">None yet</td></tr>';
  } else {
    let html = sizeData.labels.map((label, i) =>
      `<tr><td>${esc(label)}</td><td class="num">${sizeData.read[i] || '-'}</td>` +
      `<td class="num${sizeData.unread[i] ? ' bad' : ''}">${sizeData.unread[i] || '-'}</td></tr>`).join('');
    if (sizeData.read_no_size) {
      html += `<tr class="hour"><td>read by the live scan (no size)</td><td class="num">${sizeData.read_no_size}</td><td class="num">-</td></tr>`;
    }
    tbody.innerHTML = html;
  }
  const parts = Object.entries(sourceData).sort((a, b) => b[1] - a[1]).map(([k, v]) => `${k} ${v}`);
  $('sourceLine').textContent = parts.length ? 'Where the value was read: ' + parts.join(', ') : '';
  const methods = Object.entries(methodData).sort((a, b) => b[1] - a[1]);
  const shown = methods.slice(0, 6).map(([k, v]) => `${k} ${v}`);
  const rest = methods.slice(6).reduce((a, [, v]) => a + v, 0);
  $('methodLine').textContent = methods.length
    ? 'How the saved pictures were read (cv = the plain way, the others are the extra attempts): ' + shown.join(', ') + (rest ? `, others ${rest}` : '')
    : '';
}

$('dailyRows').onclick = (ev) => {
  const row = ev.target.closest('tr.day');
  if (!row) return;
  const date = row.dataset.date;
  if (openDays.has(date)) openDays.delete(date); else openDays.add(date);
  renderDaily();
};

function setEnabled(enabled) {
  $('enabledChip').textContent = enabled ? 'QR reading on' : 'QR reading off (.env ON_QR=0)';
  $('enabledChip').className = 'chip ' + (enabled ? 'ok' : 'warn');
}

// The first load (and a catch-up after the connection dropped): what the server has now.
async function loadAll() {
  try {
    const [q, daily] = await Promise.all([
      fetch('/api/qr').then((r) => r.json()),
      fetch('/api/qr/daily').then((r) => r.json()),
    ]);
    history = q.history.slice(0, MAX_HISTORY);
    scanning = q.scanning;
    setEnabled(q.enabled);
    renderDaily(daily);
  } catch (e) { /* the next push or reconnect tries again */ }
  renderHero(); renderChips(); renderRows();
}

$('copyBtn').onclick = async () => {
  if (!history[0]) return;
  try {
    await navigator.clipboard.writeText(history[0].text);
    $('copyBtn').textContent = 'Copied';
  } catch (e) { $('copyBtn').textContent = 'Copy failed'; }
  setTimeout(() => { $('copyBtn').textContent = 'Copy'; }, 1200);
};

// ---- the server pushes -------------------------------------------------------------------------------------

const source = new EventSource('/events?topics=qr,qrpage');
let connected = false;
source.onopen = () => {
  if (connected) loadAll();   // reconnected: catch up on what was missed meanwhile
  connected = true;
};
source.onmessage = (ev) => {
  let data;
  try { data = JSON.parse(ev.data); } catch (err) { return; }
  switch (data.key) {
    case 'qr':
      history.unshift(data.entry);
      history.length = Math.min(history.length, MAX_HISTORY);
      renderHero(); renderRows();
      break;
    case 'qr_scan':
      scanning = data.scanning;
      clockSkew = data.now - Date.now() / 1000;
      setEnabled(data.enabled);
      renderChips(); renderHero();
      break;
    case 'qr_stats':
      renderDaily(data.stats);
      break;
    case 'resync':
      loadAll();
      break;
  }
};

// "3s ago" keeps counting between the pushes, without asking the server
setInterval(() => { if (!document.hidden) { renderHero(); renderChips(); } }, 1000);

loadAll();
