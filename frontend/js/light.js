// Light Controller page: the brightness (0..255) of each light of the lighting controller (GET / POST /api/light).
// A value is sent when the slider is let go (or Enter / leaving the number box); the answer is what the controller
// reads back, so the page always shows the unit's own values. The camera under each light (same name) is shown
// next to it, refreshed every few seconds and right after a change, to see the effect.

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const SNAP_MS = 3000;

let channels = [];   // [{ch, name, value}] as the controller last answered
let busy = false;

async function getJson(url) {
  const r = await fetch(url, { cache: 'no-store' });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.error || r.status);
  return data;
}

async function post(url, body) {
  const r = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.error || r.status);
  return data;
}

const clamp = (v) => Math.max(0, Math.min(255, Math.round(Number(v) || 0)));

function setStatus(ok, text) {
  $('connChip').textContent = text;
  $('connChip').className = 'chip ' + (ok ? 'ok' : 'warn');
}

function showError(message) {
  $('errLine').hidden = !message;
  $('errLine').textContent = message || '';
}

function build() {
  const grid = $('grid');
  if (!channels.length) {
    grid.innerHTML = '<div class="empty">No lights (ON_LIGHT_CHANNELS).</div>';
    return;
  }
  grid.innerHTML = channels.map((c) => `
    <div class="card light-card" id="card${c.ch}">
      <h3>${esc(c.name.toUpperCase())} <span class="ch">CH${c.ch}</span><span class="val" id="val${c.ch}">-</span></h3>
      <div class="light-view"><img id="snap${c.ch}" alt="${esc(c.name)} camera"><div class="none" id="none${c.ch}" hidden>No camera named ${esc(c.name)}</div></div>
      <div class="light-ctl">
        <input type="range" id="range${c.ch}" min="0" max="255" step="1" aria-label="Brightness of ${esc(c.name)}">
        <input type="number" id="num${c.ch}" min="0" max="255" step="1" aria-label="Brightness of ${esc(c.name)}">
      </div>
      <div class="light-steps">
        <button type="button" class="secondary" data-ch="${c.ch}" data-d="-10">-10</button>
        <button type="button" class="secondary" data-ch="${c.ch}" data-d="-1">-1</button>
        <button type="button" class="secondary" data-ch="${c.ch}" data-d="1">+1</button>
        <button type="button" class="secondary" data-ch="${c.ch}" data-d="10">+10</button>
        <button type="button" class="secondary" data-ch="${c.ch}" data-v="0">Off</button>
      </div>
    </div>`).join('');
  channels.forEach((c) => {
    const range = $('range' + c.ch);
    const num = $('num' + c.ch);
    range.oninput = () => { num.value = range.value; $('val' + c.ch).textContent = range.value; };
    range.onchange = () => send([c.ch], clamp(range.value));
    num.onchange = () => send([c.ch], clamp(num.value));
    const img = $('snap' + c.ch);
    img.onerror = () => { img.hidden = true; $('none' + c.ch).hidden = false; };
    img.onload = () => { img.hidden = false; $('none' + c.ch).hidden = true; };
  });
  grid.querySelectorAll('.light-steps button').forEach((b) => {
    b.onclick = () => {
      const ch = Number(b.dataset.ch);
      const now = (channels.find((c) => c.ch === ch) || {}).value || 0;
      send([ch], b.dataset.v !== undefined ? Number(b.dataset.v) : clamp(now + Number(b.dataset.d)));
    };
  });
  refreshSnaps();
}

function render() {
  channels.forEach((c) => {
    if (!$('card' + c.ch)) return;
    $('range' + c.ch).value = c.value;
    $('num' + c.ch).value = c.value;
    $('val' + c.ch).textContent = c.value;
    $('card' + c.ch).classList.toggle('off', c.value === 0);
  });
  const values = channels.map((c) => c.value);
  if (values.length && values.every((v) => v === values[0])) {
    $('allRange').value = values[0];
    $('allNum').value = values[0];
  }
  $('readAt').textContent = 'read ' + new Date().toLocaleTimeString();
}

function setBusy(on) {
  busy = on;
  document.querySelectorAll('.light-page input, .light-page button').forEach((el) => { el.disabled = on; });
  channels.forEach((c) => { const card = $('card' + c.ch); if (card) card.classList.toggle('busy', on); });
}

function apply(data, rebuild) {
  const names = channels.map((c) => c.ch).join();
  channels = data.channels || [];
  if (rebuild || names !== channels.map((c) => c.ch).join()) build();
  render();
  setStatus(true, `${data.host}:${data.port} connected`);
  $('enabledChip').textContent = `${channels.length} lights`;
}

async function load() {
  if (busy) return;
  setBusy(true);
  try {
    const data = await getJson('/api/light');
    if (!data.enabled) {
      setStatus(false, 'no controller (ON_LIGHT_HOST is empty)');
      $('grid').innerHTML = '<div class="empty">Set ON_LIGHT_HOST in .env to the controller\'s address.</div>';
      return;
    }
    apply(data, !channels.length);
    showError('');
  } catch (e) {
    setStatus(false, 'not connected');
    showError(String(e.message || e));
    render();   // back to the last values read
  } finally {
    setBusy(false);
  }
}

async function send(chs, value) {
  if (busy) return;
  setBusy(true);
  try {
    apply(await post('/api/light', { ch: chs, value }));
    showError('');
    setTimeout(() => refreshSnaps(chs), 600);   // the next preview frames show the new light
  } catch (e) {
    showError('Not set: ' + String(e.message || e));
    render();
  } finally {
    setBusy(false);
  }
}

function refreshSnaps(chs) {
  if (document.hidden) return;
  channels.forEach((c) => {
    if (chs && !chs.includes(c.ch)) return;
    const img = $('snap' + c.ch);
    if (img) img.src = `/snapshot?cam=${encodeURIComponent(c.name)}&t=${Date.now()}`;
  });
}

$('allRange').oninput = () => { $('allNum').value = $('allRange').value; };
$('allRange').onchange = () => send(channels.map((c) => c.ch), clamp($('allRange').value));
$('allNum').onchange = () => send(channels.map((c) => c.ch), clamp($('allNum').value));
$('refreshBtn').onclick = load;
setInterval(() => refreshSnaps(), SNAP_MS);
document.addEventListener('visibilitychange', () => { if (!document.hidden) load(); });
load();
