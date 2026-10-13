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
setInterval(load, 3000);   // scan status + relative times
new EventSource('/events').onmessage = (ev) => {
  let data;
  try { data = JSON.parse(ev.data); } catch (err) { return; }
  if (data.key === 'qr') {
    history.unshift(data.entry);
    history = history.slice(0, 200);
    renderHero(); renderRows();
  }
};
