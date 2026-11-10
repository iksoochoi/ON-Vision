// Live page, Controls panel: the Camera / Light tabs and the Light tab (brightness of the lights, GET / POST /api/light).
// The controller is read when the Light tab is shown (also at page load when it was the last tab), not otherwise.
// A value is sent when the slider is let go (or Enter / leaving the number box); the answer is what the controller
// reads back. The full page with the camera pictures is /light.
(function () {
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const clamp = (v) => Math.max(0, Math.min(255, Math.round(Number(v) || 0)));
  const TAB_KEY = 'liveTab';
  let channels = [];   // [{ch, name, value}] as the controller last answered
  let busy = false;

  async function request(url, body) {
    const opts = body === undefined ? { cache: 'no-store' }
      : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) };
    const r = await fetch(url, opts);
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(data.error || r.status);
    return data;
  }

  function status(text, isError) {
    const el = $('lightStatus');
    el.textContent = text;
    el.classList.toggle('err', !!isError);
  }

  function row(id, label, value) {
    return `<div class="slider-row light-row${id === 'all' ? ' all' : ''}">
      <label for="lr_${id}">${label}</label>
      <input type="range" id="lr_${id}" min="0" max="255" step="1" value="${value}">
      <input type="number" id="ln_${id}" min="0" max="255" step="1" value="${value}" aria-label="${label.replace(/<[^>]+>/g, '')}">
    </div>`;
  }

  function build() {
    if (!channels.length) {
      $('lightRows').innerHTML = '<div class="hint">No lights (ON_LIGHT_CHANNELS).</div>';
      return;
    }
    const values = channels.map((c) => c.value);
    const same = values.every((v) => v === values[0]) ? values[0] : 0;
    $('lightRows').innerHTML = row('all', 'All', same)
      + channels.map((c) => row(c.ch, `${esc(c.name.toUpperCase())}<small>CH${c.ch}</small>`, c.value)).join('');
    const wire = (id, chs) => {
      const range = $('lr_' + id);
      const num = $('ln_' + id);
      range.oninput = () => { num.value = range.value; };
      range.onchange = () => send(chs(), clamp(range.value));
      num.onchange = () => send(chs(), clamp(num.value));
    };
    wire('all', () => channels.map((c) => c.ch));
    channels.forEach((c) => wire(c.ch, () => [c.ch]));
  }

  function render() {
    channels.forEach((c) => {
      if (!$('lr_' + c.ch)) return;
      $('lr_' + c.ch).value = c.value;
      $('ln_' + c.ch).value = c.value;
    });
    const values = channels.map((c) => c.value);
    if ($('lr_all') && values.length && values.every((v) => v === values[0])) {
      $('lr_all').value = values[0];
      $('ln_all').value = values[0];
    }
  }

  function setBusy(on) {
    busy = on;
    document.querySelectorAll('#tabLight input, #lightRead').forEach((el) => { el.disabled = on; });
  }

  function apply(data) {
    const before = channels.map((c) => c.ch).join();
    channels = data.channels || [];
    if (!$('lr_all') || before !== channels.map((c) => c.ch).join()) build();
    render();
    status(`${data.host}:${data.port} connected${data.relay ? ' (relay)' : ''}, read ${new Date().toLocaleTimeString()}`);
  }

  async function load() {
    if (busy) return;
    setBusy(true);
    try {
      const data = await request('/api/light');
      if (!data.enabled) {
        $('lightRows').innerHTML = '';
        status('No lighting controller (ON_LIGHT_HOST is empty in .env).', true);
        return;
      }
      apply(data);
    } catch (e) {
      if (!channels.length) $('lightRows').innerHTML = '';
      render();
      status('Not connected: ' + String(e.message || e), true);
    } finally {
      setBusy(false);
    }
  }

  async function send(chs, value) {
    if (busy) return;
    setBusy(true);
    try {
      apply(await request('/api/light', { ch: chs, value }));
    } catch (e) {
      render();   // back to the values last read
      status('Not set: ' + String(e.message || e), true);
    } finally {
      setBusy(false);
    }
  }

  // ---- the tabs ----------------------------------------------------------------------------------------------------
  function showTab(name) {
    document.querySelectorAll('.panel-tabs button').forEach((b) => {
      const on = b.dataset.tab === name;
      b.classList.toggle('active', on);
      b.setAttribute('aria-selected', String(on));
    });
    $('tabCamera').hidden = name !== 'camera';
    $('tabLight').hidden = name !== 'light';
    try { localStorage.setItem(TAB_KEY, name); } catch (e) {}
    if (name === 'light') load();
  }

  document.querySelectorAll('.panel-tabs button').forEach((b) => { b.onclick = () => showTab(b.dataset.tab); });
  $('lightRead').onclick = load;
  let first = 'camera';
  try { first = localStorage.getItem(TAB_KEY) === 'light' ? 'light' : 'camera'; } catch (e) {}
  showTab(first);
})();
