// Live page, Controls > Preset: product presets (GET / POST /api/presets...). A preset = every camera's settings + the
// lights under a name (one per product: ON1GR, ON1WH ...). Apply sets them all at once; Save keeps what runs now.
// The chip in the status bar shows the preset applied last ("changed" when a value was changed by hand after it).
(function () {
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  let data = null;   // the last GET /api/presets
  let busy = false;

  async function request(url, body) {
    const opts = body === undefined ? { cache: 'no-store' }
      : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) };
    const r = await fetch(url, opts);
    const d = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(d.error || r.status);
    return d;
  }

  function status(text, isError) {
    const el = $('presetStatus');
    el.textContent = text || '';
    el.classList.toggle('err', !!isError);
  }

  const when = (iso) => (iso || '').replace('T', ' ').slice(0, 16);

  function renderChip() {
    const chip = $('presetChip');
    const a = data && data.active;
    chip.hidden = !a;
    if (!a) return;
    chip.textContent = `Preset ${a.name}${a.modified ? ' (changed)' : ''}`;
    chip.classList.toggle('warn', !!a.modified);
    chip.title = `Applied ${when(a.applied_at)}` + (a.modified ? ' - a camera value or a light was changed by hand after it' : '');
  }

  // what a preset holds, one line per camera (the values that matter most) and the lights
  function detail(p) {
    if (!p) return '';
    const cams = Object.entries(p.cameras || {}).map(([role, v]) => {
      const exp = v.exposure_ms ? `${v.exposure_ms} ms` : `auto${v.ae_limit_ms ? ' ≤' + v.ae_limit_ms + ' ms' : ''}`;
      return `<tr><td>${esc(role)}</td><td>${exp}</td><td>${v.iso || 'auto'}</td><td>${v.lens_position || 'auto'}</td>` +
        `<td>${v.sharpness}</td><td>${v.luma_denoise}</td><td>${v.wb ? v.wb + 'K' : 'auto'}</td></tr>`;
    }).join('');
    const names = (data && data.light_names) || {};
    const lights = p.lights
      ? Object.entries(p.lights).map(([ch, v]) => `${esc(names[ch] || 'CH' + ch)} ${v}`).join(' · ')
      : 'not in this preset (the lights stay as they are)';
    return `<table class="preset-table"><thead><tr><th>Camera</th><th>Exposure</th><th>ISO</th><th>Focus</th>` +
      `<th title="Sharpness">Sharp</th><th title="Luma denoise">Denoise</th><th>WB</th></tr></thead><tbody>${cams}</tbody></table>` +
      `<div class="hint">Lights: ${lights}</div><div class="hint">Saved ${when(p.saved_at)}</div>`;
  }

  function render() {
    renderChip();
    const list = (data && data.presets) || [];
    const a = data && data.active;
    const ln = data && data.lights_now;
    $('presetActive').innerHTML = (a
      ? `Running: <b>${esc(a.name)}</b>, applied ${esc(when(a.applied_at))}` +
        (a.modified ? ' <span class="warn-text">- changed by hand since</span>' : '')
      : 'No preset applied yet.') +
      (ln && ln.values ? `<br>Lights recorded with the sets: ${Object.entries(ln.values).map(([n, v]) => `${esc(n)} ${v}`).join(' · ')}` +
        ` <span title="The app reads the lights on the Light tab / page, when a preset is applied or saved, and at its start. A change made on the controller itself is seen at the next read.">(read ${esc(when(ln.read_at))})</span>` : '');
    const pick = $('presetPick');
    const keep = pick.value || (a && a.name) || '';
    pick.innerHTML = list.length
      ? list.map((p) => `<option value="${esc(p.name)}">${esc(p.name)}</option>`).join('')
      : '<option value="">(none saved yet)</option>';
    if (list.some((p) => p.name === keep)) pick.value = keep;
    $('presetNames').innerHTML = list.map((p) => `<option value="${esc(p.name)}">`).join('');
    const p = list.find((x) => x.name === pick.value);
    $('presetDetail').innerHTML = detail(p);
    $('presetApply').disabled = busy || !p;
    $('presetDelete').disabled = busy || !p;
    $('presetSave').disabled = busy;
    $('presetLights').disabled = !(data && data.lights_enabled);
    if (!(data && data.lights_enabled)) $('presetLights').checked = false;
  }

  async function load() {
    try {
      data = await request('/api/presets');
      render();
    } catch (e) {
      status('Could not load the presets: ' + String(e.message || e), true);
    }
  }

  async function run(fn) {
    if (busy) return;
    busy = true;
    render();
    try {
      await fn();
    } catch (e) {
      status(String(e.message || e), true);
    } finally {
      busy = false;
      render();
    }
  }

  $('presetPick').onchange = render;

  $('presetApply').onclick = () => run(async () => {
    const name = $('presetPick').value;
    const p = data.presets.find((x) => x.name === name);
    if (!p || !window.confirm(`Apply the preset "${name}"?\n\nEvery camera` + (p.lights ? ' and the lights' : '') +
      ' take its values now (the pictures need a few seconds to follow).')) return;
    data = await request('/api/presets/apply', { name });
    const msg = [`"${name}" applied to ${data.cameras.length ? data.cameras.join(', ') : 'no camera'}`];
    if (data.skipped && data.skipped.length) msg.push(`not on this station: ${data.skipped.join(', ')}`);
    if (data.light_error) msg.push('LIGHTS NOT SET: ' + data.light_error);
    else if (Object.keys(data.lights || {}).length) msg.push('lights set and saved');
    status(msg.join('; ') + '.', !!data.light_error);
    if (typeof showToast === 'function') showToast(`Preset ${name} applied`);
  });

  $('presetSave').onclick = () => run(async () => {
    const name = $('presetName').value.trim();
    if (!name) { status('Type a name (e.g. ON1WH).', true); return; }
    const exists = data.presets.some((p) => p.name === name);
    const lights = $('presetLights').checked;
    if (!window.confirm(`${exists ? 'Overwrite' : 'Save'} the preset "${name}" with the values every camera` +
      (lights ? ' and the lights' : '') + ' have now?')) return;
    data = await request('/api/presets/save', { name, lights });
    $('presetPick').value = name;
    status(`Saved "${name}".`);
  });

  $('presetDelete').onclick = () => run(async () => {
    const name = $('presetPick').value;
    if (!name || !window.confirm(`Delete the preset "${name}"? The cameras and lights keep their values.`)) return;
    data = await request('/api/presets/delete', { name });
    $('presetPick').value = '';
    status(`Deleted "${name}".`);
  });

  // the server says something changed (a preset applied / saved elsewhere, a camera value changed by hand)
  let timer = null;
  window.addEventListener('presetchanged', () => { clearTimeout(timer); timer = setTimeout(load, 300); });
  window.addEventListener('livetab', (e) => { if (e.detail === 'preset') load(); });
  load();
})();
