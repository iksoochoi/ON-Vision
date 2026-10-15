let history = [];   // newest first
let scanning = {};

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

function ago(epoch) {
  const s = Math.max(0, Math.round(Date.now() / 1000 - epoch));
  return s < 60 ? `${s}s ago` : s < 3600 ? `${Math.round(s / 60)}m ago` : `${Math.round(s / 3600)}h ago`;
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
  hero.classList.toggle('fresh', Date.now() / 1000 - e.epoch < 10);
}

function renderChips() {
  const box = $('scanChips');
  const roles = Object.keys(scanning);
  box.innerHTML = roles.length ? '' : '<span class="hint">No camera has been scanned yet</span>';
  for (const role of roles) {
    const s = scanning[role];
    const chip = document.createElement('span');
    const recent = Date.now() / 1000 - s.epoch < 10;
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
  for (const e of history.slice(0, 100)) {
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

function renderDaily(days) {
  if (days) dailyData = days;
  const tbody = $('dailyRows'), foot = $('dailyTotal');
  if (!dailyData.length) {
    tbody.innerHTML = '<tr><td colspan="5" class="empty-row">None yet</td></tr>';
    foot.innerHTML = '';
    return;
  }
  const rate = (ok, n) => (n ? (100 * ok / n).toFixed(1) + '%' : '-');
  const cells = (r) => `<td class="num">${r.sets}</td><td class="num">${r.ok}</td>` +
    `<td class="num${r.fail ? ' bad' : ''}">${r.fail}</td><td class="num">${rate(r.ok, r.sets)}</td>`;
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
  const sum = dailyData.reduce((a, d) => ({ sets: a.sets + d.sets, ok: a.ok + d.ok, fail: a.fail + d.fail }), { sets: 0, ok: 0, fail: 0 });
  foot.innerHTML = `<tr><td>Total</td>${cells(sum)}</tr>`;
}

$('dailyRows').onclick = (ev) => {
  const row = ev.target.closest('tr.day');
  if (!row) return;
  const date = row.dataset.date;
  if (openDays.has(date)) openDays.delete(date); else openDays.add(date);
  renderDaily();
};

async function loadDaily() {
  try { renderDaily(await fetch('/api/qr/daily').then((r) => r.json())); } catch (e) {}
}

async function load() {
  try {
    const q = await fetch('/api/qr').then((r) => r.json());
    history = q.history;
    scanning = q.scanning;
    $('enabledChip').textContent = q.enabled ? 'QR reading on' : 'QR reading off (.env ON_QR=0)';
    $('enabledChip').className = 'chip ' + (q.enabled ? 'ok' : 'warn');
  } catch (e) {}
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

fetch('/api/state').then((r) => r.json()).then((st) => {
  document.title = 'ON Data Collector';
}).catch(() => {});

load();
loadDaily();
setInterval(load, 3000);   // scan status + relative times
setInterval(loadDaily, 15000);
new EventSource('/events').onmessage = (ev) => {
  let data;
  try { data = JSON.parse(ev.data); } catch (err) { return; }
  if (data.key === 'qr') {
    history.unshift(data.entry);
    history = history.slice(0, 200);
    renderHero(); renderRows();
    if (data.entry.source === 'capture') loadDaily();   // a set was just saved
  }
};
