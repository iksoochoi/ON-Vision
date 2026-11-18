// QC Export page: upload the weekly site QC workbook, see what of it is on this Mac (shoes found, sets, pictures,
// size per result / defect type), download the summary workbook (_QR only), make the zip (backend.cli.qc_export)
// with progress and a cancel, download it. The work runs on the Mac (backend/storage/qc_jobs.py); this page polls
// GET /api/qc/status while it runs.

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const RESULTS = ['OK', 'Defect', 'Conflict', 'NoResult'];
const STAGE = { idle: 'no workbook', reading: 'reading…', ready: 'ready', exporting: 'making the zip…',
                done: 'zip made', cancelled: 'cancelled', error: 'error' };

let st = { stage: 'idle' };
let timer = null;
let built = '';   // the preview drawn (its upload file name), so the options are not reset on every poll

function size(n) {
  if (n == null) return '-';
  const u = ['B', 'KB', 'MB', 'GB', 'TB'];
  let i = 0, v = Number(n);
  while (v >= 1024 && i < u.length - 1) { v /= 1024; i++; }
  return i ? `${v.toFixed(1)} ${u[i]}` : `${v} B`;
}
const pct = (a, b) => (b ? `${Math.round((100 * a) / b)}%` : '-');
const day = (d) => (d ? `${d.slice(0, 4)}-${d.slice(4, 6)}-${d.slice(6)}` : '');

async function api(url, body) {
  const opt = body === undefined ? { cache: 'no-store' }
    : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) };
  const r = await fetch(url, opt);
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.error || `HTTP ${r.status}`);
  return data;
}

function showError(msg) {
  $('errLine').hidden = !msg;
  $('errLine').textContent = msg || '';
}

// ---- upload ----

function readFile(file) {
  return new Promise((resolve, reject) => {
    const fr = new FileReader();
    fr.onload = () => resolve(fr.result);
    fr.onerror = () => reject(fr.error || new Error('could not read the file'));
    fr.readAsDataURL(file);
  });
}

async function upload(file) {
  if (!file) return;
  if (!/\.xlsx$/i.test(file.name)) { showError('An .xlsx file is expected (the site QC workbook).'); return; }
  showError('');
  $('dropText').textContent = `Uploading ${file.name} (${size(file.size)})…`;
  $('drop').classList.add('busy');
  try {
    const data = await readFile(file);
    built = '';
    render(await api('/api/qc/upload', { name: file.name, data }));
    poll();
  } catch (e) {
    showError(e.message);
  } finally {
    $('drop').classList.remove('busy');
    $('file').value = '';
  }
}

$('file').onchange = () => upload($('file').files[0]);
const drop = $('drop');
drop.ondragover = (e) => { e.preventDefault(); drop.classList.add('over'); };
drop.ondragleave = () => drop.classList.remove('over');
drop.ondrop = (e) => { e.preventDefault(); drop.classList.remove('over'); upload(e.dataTransfer.files[0]); };

// ---- drawing ----

function tableHtml(head, rows, total) {
  const th = '<tr>' + head.map((h) => `<th>${esc(h)}</th>`).join('') + '</tr>';
  const tr = rows.map((r) => '<tr>' + r.map((c) => `<td${c && c.cls ? ` class="${c.cls}"` : ''}>${c && c.html != null ? c.html : esc(c)}</td>`).join('') + '</tr>').join('');
  const tt = total ? '<tr class="total">' + total.map((c) => `<td>${esc(c)}</td>`).join('') + '</tr>' : '';
  return th + tr + tt;
}

function drawPreview(p) {
  const models = Object.entries(p.models || {}).map(([m, n]) => `${m} ${n}`).join(', ');
  $('facts').innerHTML = [
    ['Days', `${day(p.days[0])} ~ ${day(p.days[1])}`], ['Rows with a QR', p.rows], ['Shoes', p.shoes], ['Models', models],
    ['Found on this Mac', `${p.found} (${pct(p.found, p.shoes)})`], ['Sets', p.sets], ['Pictures', p.pictures], ['Size', size(p.bytes)],
    ['Frames of those days without a QR value', p.frames_without_qr],
  ].map(([k, v]) => `<span>${esc(k)} <b>${esc(v)}</b></span>`).join('');

  const pr = p.per_result || [];
  $('tResult').innerHTML = tableHtml(['Result', 'Shoes', 'Found', '%', 'Sets', 'Pictures', 'Size'],
    pr.map((r) => [r.key, r.shoes, r.found, { html: pct(r.found, r.shoes), cls: r.found < r.shoes * 0.5 ? 'low' : '' }, r.sets, r.pictures, size(r.bytes)]),
    ['Total', p.shoes, p.found, pct(p.found, p.shoes), p.sets, p.pictures, size(p.bytes)]);
  const d = p.defect;
  if (d) {
    // share: of the Defect shoes (a shoe with several types counts in each of them, so the shares add up to a bit more than 100%)
    const share = (n) => (d.shoes ? `${((100 * n) / d.shoes).toFixed(1)}%` : '-');
    const top = Math.max(1, ...d.per_type.map((r) => r.shoes));   // the bar: against the most common type
    $('tType').innerHTML = tableHtml(['Defect type (folder)', 'Shoes', 'Share', 'Found', 'Pictures'],
      d.per_type.map((r) => [r.key, r.shoes, { html: `<span class="share"><i style="width:${(100 * r.shoes) / top}%"></i>${share(r.shoes)}</span>` }, r.found, r.pictures]),
      ['Defect shoes', d.shoes, '100%', d.found, d.pictures]);
    $('multiLine').textContent = 'Share: of the Defect shoes.' + (d.multiple
      ? ` ${d.multiple} shoes with several types are counted under each of them, so the shares add up to more than 100% (in the zip: in each folder, ${size(d.copies_bytes)} more).` : '');
  } else {
    $('tType').innerHTML = '<tr><td>No Defect shoes</td></tr>';
    $('multiLine').textContent = '';
  }
  $('probBox').hidden = !p.problems_count;
  $('probSum').textContent = `${p.problems_count} rows of the sheet left out`;
  $('probList').innerHTML = (p.problems || []).map((t) => `<li>${esc(t)}</li>`).join('')
    + (p.problems_count > (p.problems || []).length ? `<li>… ${p.problems_count - p.problems.length} more</li>` : '');
  $('nfBox').hidden = !p.not_found_count;
  $('nfSum').textContent = `${p.not_found_count} shoes not found on this Mac (no set with that QR that day: QR not read, misread or not captured)`;
  $('tNotFound').innerHTML = tableHtml(['QR', 'Date', 'Result', 'Defect type'], (p.not_found || []).map((r) => [r.qr, r.date, r.result, r.type]))
    + (p.not_found_count > (p.not_found || []).length ? `<tr><td colspan="4">… ${p.not_found_count - p.not_found.length} more (all in the summary workbook: Shoes, Found on Mac = no)</td></tr>` : '');

  $('results').innerHTML = RESULTS.filter((k) => pr.some((r) => r.key === k)).map((k) => {
    const r = pr.find((x) => x.key === k);
    return `<label><input type="checkbox" value="${k}" ${k !== 'NoResult' ? 'checked' : ''}> ${k} <span class="mono">(${r.found} shoes, ${size(r.bytes)})</span></label>`;
  }).join('');
  for (const el of document.querySelectorAll('#results input, input[name=mode], #anyDay, #noSet')) el.onchange = drawSize;
  $('okPerDay').oninput = drawSize;
}

function drawSize() {
  const p = st.preview;
  if (!p) return;
  const mode = document.querySelector('input[name=mode]:checked').value;
  $('results').classList.toggle('off', mode !== 'all');
  $('okDayBox').classList.toggle('off', mode !== 'defect');
  let bytes, shoes;
  if (mode === 'defect') {
    const ok = okSample();
    bytes = (p.defect ? p.defect.bytes : 0) + ok * (p.ok_shoe_bytes || 0); shoes = (p.defect ? p.defect.found : 0) + ok;
  } else {
    const want = [...document.querySelectorAll('#results input:checked')].map((i) => i.value);
    const rows = (p.per_result || []).filter((r) => want.includes(r.key));
    bytes = rows.reduce((a, r) => a + r.bytes, 0); shoes = rows.reduce((a, r) => a + r.found, 0);
  }
  const free = st.free_bytes;
  const tight = free != null && free < bytes * 1.02 + 200 * 1024 * 1024;
  $('sizeLine').innerHTML = `About <b>${size(bytes)}</b> (${shoes} shoes found)${$('anyDay').checked ? ' + the sets of other days' : ''}`
    + ` · free on this Mac ${size(free)}${tight ? ' — <b>not enough</b>' : ''}`;
  $('goBtn').disabled = !shoes || tight || st.stage !== 'ready';
  drawTree(mode);
}

const shoesN = (n) => `${n} shoe${n === 1 ? '' : 's'}`;
const okPerDay = () => Math.max(0, Math.min(100, Math.floor(+$('okPerDay').value || 0)));
// the OK shoes of the defect zip's OK/ folder: okPerDay of each QC day, at most the ones found that day
const okSample = () => Object.values((st.preview && st.preview.ok_days) || {}).reduce((a, n) => a + Math.min(n, okPerDay()), 0);

// the folders of the zip with these options (as backend.cli.qc_export writes it); the first folder opened with one
// real set of this sheet as the example
function drawTree(mode) {
  const p = st.preview;
  const e = p.example || { shoe: '<shoe QR>', set: '<set>', id: '<id>', cameras: ['top', 'back', 'left', 'front', 'right'] };
  const D = (t) => `<span class="d">${esc(t)}</span>`;
  const N = (t) => `<span class="n">${esc(t)}</span>`;
  let folders;
  if (mode === 'defect') {
    folders = ((p.defect && p.defect.per_type) || []).filter((r) => r.found).map((r) => [r.key, r.found]);
    if (okSample()) folders.push(['OK', okSample(), `${okPerDay()} per day`]);
  } else {
    const want = [...document.querySelectorAll('#results input:checked')].map((i) => i.value);
    folders = (p.per_result || []).filter((r) => want.includes(r.key) && r.found).map((r) => [r.key, r.found]);
  }
  const zip = `qc_${mode === 'defect' ? 'defect_' : ''}${p.days[0]}_${p.days[1]}_<time>.zip`;
  const lines = [D(zip),
    `├── README.txt`,
    `├── labels.csv        ${N('every shoe of the sheet: result, type, found, path')}`,
    `├── manifest.json     ${N('options, counts, shoes not found')}`];
  if (!folders.length) lines.push(`└── ${N('(no shoes found with these options)')}`);
  folders.forEach(([name, n, what], k) => {
    const last = k === folders.length - 1;
    const a = last ? '    ' : '│   ';
    lines.push(`${last ? '└──' : '├──'} ${D(name + '/')}  ${N(shoesN(n) + (what ? ` (${what})` : ''))}`);
    if (k === 0) {
      const more = n > 1;
      const b = more ? '│   ' : '    ';
      lines.push(`${a}${more ? '├──' : '└──'} ${D(e.shoe + '/')}  ${N('a shoe, e.g. (its QR, # → -)')}`);
      let c2 = b;   // the files: in the pass folder, or straight in the shoe's folder
      if (!$('noSet').checked) {
        lines.push(`${a}${b}└── ${D(e.set + '/')}  ${N('a pass of the shoe')}`);
        c2 = b + '    ';
      }
      lines.push(`${a}${c2}├── &lt;id&gt;.json  ${N('the data of the frame')}`);
      e.cameras.forEach((c, i) => lines.push(`${a}${c2}${i === e.cameras.length - 1 ? '└──' : '├──'} &lt;id&gt;_${esc(c)}.jpg`));
      if (more) lines.push(`${a}└── ${N(`… ${n - 1} more shoe${n - 1 === 1 ? '' : 's'}`)}`);
    }
  });
  const notes = [`<id>: the frame, e.g. ${e.id}`];
  if (mode === 'defect' && p.defect && p.defect.multiple) notes.push(`a shoe with several types is in each of its type folders (${p.defect.multiple} shoes)`);
  if (mode === 'defect' && okSample()) notes.push(`OK/: ${okPerDay()} OK shoes of each QC day (of the ones found), spread over the day`);
  if ($('anyDay').checked) notes.push("a shoe's sets of other days are in its folder too");
  if ($('noSet').checked) notes.push("several passes of a shoe: all their frames in the shoe's folder (<id> differs by the time)");
  $('tree').innerHTML = lines.join('\n') + (notes.length ? '\n\n' + notes.map((t) => N('* ' + t)).join('\n') : '');
}

function render(s) {
  st = s;
  const stage = s.stage;
  $('stageChip').textContent = STAGE[stage] || stage;
  $('stageChip').className = 'chip ' + (stage === 'error' ? 'warn' : stage === 'done' || stage === 'ready' ? 'ok' : '');
  $('stepLine').textContent = s.name ? `${s.name}${s.step ? ' — ' + s.step : ''}` : '';
  $('dropText').textContent = stage === 'reading' ? 'Reading…'
    : 'Drop the weekly QC workbook here, or click to choose it (.xlsx with the sheet \'Master data_cell 1\', as it comes)';
  $('drop').classList.toggle('busy', stage === 'reading' || stage === 'exporting');
  const readErr = stage === 'error' && !s.preview;
  if (readErr) showError(s.error);

  const p = s.preview;
  $('previewCard').hidden = !p;
  $('exportCard').hidden = !p;
  if (p && built !== s.file) { built = s.file; drawPreview(p); }
  $('wbLink').innerHTML = s.workbook ? `<a href="/api/qc/file?name=${encodeURIComponent(s.workbook)}">Download the summary workbook (_QR only)</a>` : '';
  $('wbErr').hidden = !s.workbook_error;
  $('wbErr').textContent = s.workbook_error ? `No summary workbook: ${s.workbook_error}` : '';
  $('autoLine').hidden = !s.auto_enabled;

  // the export part
  const ex = s.export || {};
  const running = stage === 'exporting';
  const ended = ['done', 'cancelled', 'error'].includes(stage) && p;
  $('goBtn').hidden = running || ended;
  $('cancelBtn').hidden = !running;
  $('backBtn').hidden = !ended;
  for (const el of document.querySelectorAll('#exportCard input')) el.disabled = running || ended;
  $('bar').hidden = !running;
  if (running) {
    $('barFill').style.width = ex.total ? `${(100 * ex.done) / ex.total}%` : '0';
    $('progLine').textContent = ex.total ? `${s.step}: ${ex.done} / ${ex.total} frames, ${size(ex.bytes)} of ${size(ex.total_bytes)}` : (s.step || '');
  } else {
    $('progLine').textContent = stage === 'cancelled' ? 'Cancelled (nothing was kept).' : stage === 'error' && p ? `Error: ${s.error}` : '';
  }
  $('doneLine').hidden = stage !== 'done';
  if (stage === 'done' && ex.zip) {
    $('doneLine').innerHTML = `Done: ${ex.shoes} shoes${ex.ok_shoes ? ` (${ex.ok_shoes} of them OK in OK/)` : ''}, ${ex.sets} sets — <a href="/exports/${encodeURIComponent(ex.zip)}">${esc(ex.zip)}</a> (${size(ex.zip_bytes)})`;
  }
  if (p) drawSize();

  const z = s.zips || [];
  $('tZips').innerHTML = z.length
    ? tableHtml(['Zip', 'Made', 'Size'], z.map((r) => [{ html: `<a href="/exports/${encodeURIComponent(r.name)}">${esc(r.name)}</a>` }, r.time.replace('T', ' '), size(r.bytes)]))
    : '<tr><td>None yet</td></tr>';
}

// ---- actions ----

$('goBtn').onclick = async () => {
  const mode = document.querySelector('input[name=mode]:checked').value;
  const results = [...document.querySelectorAll('#results input:checked')].map((i) => i.value);
  if (mode === 'all' && !results.length) { $('progLine').textContent = 'Choose at least one result.'; return; }
  try {
    render(await api('/api/qc/export', { mode, results, any_day: $('anyDay').checked, set_folder: !$('noSet').checked,
                                         ok_per_day: mode === 'defect' ? okPerDay() : 0 }));
    poll();
  } catch (e) {
    $('progLine').textContent = `Error: ${e.message}`;
  }
};
$('cancelBtn').onclick = async () => { try { render(await api('/api/qc/cancel', {})); } catch (e) { /* the next poll shows it */ } };
$('backBtn').onclick = async () => { try { render(await api('/api/qc/reset', {})); } catch (e) { /* idem */ } };

async function poll() {
  clearTimeout(timer);
  try {
    render(await api('/api/qc/status'));
  } catch (e) {
    $('stageChip').textContent = 'no answer';
    $('stageChip').className = 'chip warn';
  }
  if (['reading', 'exporting'].includes(st.stage)) timer = setTimeout(poll, 1000);
}

poll();
