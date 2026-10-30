// Browse the saved pictures by shoe: the shoe's QR value (shoe_id) on the left, and on the right all the
// pictures of the selected shoe. Words: a "set" is one pass of the shoe over the belt (before / center /
// after of every camera), a "cut" is one moment of it (one picture per camera).

const $ = (id) => document.getElementById(id);
const CUT_NAME = { before: 'Before', center: 'Center', after: 'After' };
const CUT_ORDER = { before: 0, center: 1, after: 2 };
const MAX_LISTED = 400;

let shoes = [];            // /api/shoes: the shoes on the list (the first MAX_LISTED that match the search)
let shoeTotals = null;     // ... and the totals of the whole list
let dates = [];            // the days that have sets (newest first), with their counts
let dateFilter = '';       // the day shown ('' = all days): ?date=2026-10-15
let current;               // the selected shoe_id: a string, null = no QR value was read, undefined = none selected
let metas = [];            // the cuts of the selected shoe, newest first
let sets = [];             // those cuts grouped into sets, newest first
let apiCams = [];          // camera roles in the order the live page uses
let camOrder = [];         // roles to show
const hidden = new Set();  // cameras switched off in the filter
let mode = 'sets';         // 'sets' | 'cams'
let focusSample = null;    // ?sample=<id>: the set that contains it is highlighted
const picked = new Set();  // keys of the sets ticked for the download (of the open shoe)
let shown = [];            // the pictures on screen, in order: what the lightbox steps through
let lbIndex = -1;

const fmtTime = (iso) => (iso || '').replace('T', ' ').slice(5, 19);      // 10-08 10:17:22
const clock = (iso) => (iso || '').slice(11, 19);                          // 10:17:22
const cutName = (m) => (m.burst ? CUT_NAME[m.burst.kind] || '' : 'Manual');
const shoeName = (id) => (id === null ? 'No ID (QR not read)' : id);
const hasView = (m, role) => (m.views ? role in m.views : role === m.primary_view);
const imageUrl = (m, role, kind) => `/api/samples/${m.id}/${kind}?view=${encodeURIComponent(role)}`;
function fmtBytes(n) {
  if (!n) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let i = 0;
  let v = n;
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i++; }
  return (v >= 100 || i === 0 ? v.toFixed(0) : v.toFixed(1)) + ' ' + units[i];
}
const shoeParam = (id) => encodeURIComponent(id === null ? '' : id);

async function getJson(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error('HTTP ' + r.status);
  return r.json();
}

function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined) e.textContent = text;
  return e;
}

// ---- grouping ------------------------------------------------------------------------------------

function groupSets(list) {
  const map = new Map();
  for (const m of list) {
    const key = (m.burst && m.burst.id) || m.id;     // a manual capture is a set of its own
    if (!map.has(key)) map.set(key, { key, cuts: [] });
    map.get(key).cuts.push(m);
  }
  const out = [...map.values()];
  for (const s of out) {
    s.cuts.sort((a, b) => ((CUT_ORDER[a.burst && a.burst.kind] ?? 1) - (CUT_ORDER[b.burst && b.burst.kind] ?? 1))
      || a.timestamp.localeCompare(b.timestamp));
    s.time = s.cuts.map((c) => c.timestamp).sort()[0];
    s.source = s.cuts[0].source;
  }
  out.sort((a, b) => b.time.localeCompare(a.time));
  return out;
}

function mergeCams() {
  const seen = new Set();
  for (const m of metas) for (const r of Object.keys(m.views || { [m.primary_view]: 1 })) seen.add(r);
  const known = apiCams.filter((r) => seen.has(r));
  return known.concat([...seen].filter((r) => !known.includes(r)));
}

// ---- shoe list -------------------------------------------------------------------------------------

function renderShoes() {
  const box = $('shoes');
  const needle = $('search').value.trim();
  box.replaceChildren();
  if (!shoes.length) {
    box.appendChild(el('div', 'empty', needle ? 'No matches' : 'No saved images'));
  }
  for (const s of shoes) {
    const item = el('div', 'shoe' + (s.shoe_id === null ? ' noid' : '') + (s.shoe_id === current ? ' sel' : ''));
    item.title = shoeName(s.shoe_id);
    const text = el('div');
    text.append(el('div', 'name', shoeName(s.shoe_id)),
      el('div', 'sub', `${s.sets} sets · ${s.images} images · ${fmtBytes(s.bytes)}`), el('div', 'sub', `last ${fmtTime(s.last)}`));
    item.append(text);
    item.onclick = () => selectShoe(s.shoe_id);
    box.appendChild(item);
  }
  if (shoeTotals && shoeTotals.matches > MAX_LISTED) {
    box.appendChild(el('div', 'hint', `… ${shoeTotals.matches - MAX_LISTED} more. Narrow the search.`));
  }
  if (shoeTotals) {
    $('totals').textContent = `${shoeTotals.shoes} shoes · ${shoeTotals.sets} sets · ${shoeTotals.images} images · ${fmtBytes(shoeTotals.bytes)}`;
  }
}

function renderDates() {
  const box = $('dates');
  box.replaceChildren();
  const all = el('div', 'dateitem' + (dateFilter === '' ? ' sel' : ''));
  all.append(el('div', 'day', 'All dates'),
    el('div', 'sub', `${dates.reduce((n, d) => n + d.sets, 0)} sets`));
  all.onclick = () => selectDate('');
  box.appendChild(all);
  for (const d of dates) {
    const item = el('div', 'dateitem' + (d.date === dateFilter ? ' sel' : ''));
    item.title = `${d.date}: ${d.sets} sets, ${d.shoes} shoes, ${d.images} images, ${fmtBytes(d.bytes)}`;
    item.append(el('div', 'day', d.date.slice(5)), el('div', 'sub', `${d.sets} sets · ${fmtBytes(d.bytes)}`));
    item.onclick = () => selectDate(d.date);
    box.appendChild(item);
  }
}

// A day was picked: the shoe list and the pictures of the open shoe show that day only.
async function selectDate(date) {
  if (date === dateFilter) return;
  dateFilter = date;
  try {
    await refreshShoes();
    if (current !== undefined && shoes.some((s) => s.shoe_id === current)) await selectShoe(current);
    else if (shoes.length) await selectShoe(shoes[0].shoe_id);
    else { current = undefined; metas = []; sets = []; renderDetail(false); setUrl(); }
  } catch (e) { /* the next event tries again */ }
}

// The server sends the first MAX_LISTED shoes that match the search (and the one on screen), and the totals of the
// whole list: the page does not grow with the number of shoes.
async function refreshShoes() {
  const query = encodeURIComponent($('search').value.trim());
  const include = current === undefined ? '' : `&include=${shoeParam(current)}`;
  const day = dateFilter ? `&date=${encodeURIComponent(dateFilter)}` : '';
  const reply = await getJson(`/api/shoes?limit=${MAX_LISTED}&q=${query}${include}${day}`);
  shoes = reply.shoes;
  shoeTotals = reply.totals;
  dates = reply.dates || [];
  if (dateFilter && !dates.some((d) => d.date === dateFilter)) {   // that day has no sets (any more): show all days
    dateFilter = '';
    return refreshShoes();
  }
  renderDates();
  renderShoes();
}

// ---- the selected shoe ---------------------------------------------------------------------------

function setUrl() {
  if (current === undefined) return;
  history.replaceState(null, '', `/browse?shoe=${shoeParam(current)}&mode=${mode}` + (dateFilter ? `&date=${dateFilter}` : ''));
}

async function selectShoe(id, keepScroll) {
  if (id !== current) picked.clear();
  current = id;
  metas = await getJson('/api/samples?shoe=' + shoeParam(id) + (dateFilter ? `&date=${encodeURIComponent(dateFilter)}` : ''));
  sets = groupSets(metas);
  for (const key of [...picked]) if (!sets.some((s) => s.key === key)) picked.delete(key);   // deleted meanwhile
  camOrder = mergeCams();
  renderShoes();
  renderDetail(keepScroll);
  setUrl();
}

function renderDetail(keepScroll) {
  const view = document.querySelector('.shoeview');
  const top = view.scrollTop;
  $('empty').hidden = current !== undefined && metas.length > 0;
  $('detail').hidden = !(current !== undefined && metas.length > 0);
  if ($('detail').hidden) {
    $('empty').textContent = current === undefined ? 'No saved images' : 'No images for this shoe';
    return;
  }
  $('shoeTitle').textContent = shoeName(current);
  const images = metas.reduce((n, m) => n + Object.keys(m.views || { x: 1 }).length, 0);
  $('shoeStats').textContent = `${sets.length} sets · ${metas.length} frames · ${images} images · `
    + `${fmtTime(sets[sets.length - 1].time)} ~ ${fmtTime(sets[0].time)}`;
  $('modeSets').classList.toggle('active', mode === 'sets');
  $('modeCams').classList.toggle('active', mode === 'cams');
  $('dlBar').hidden = mode !== 'sets';
  updateDownload();

  const chips = $('camFilter');
  chips.replaceChildren();
  for (const role of camOrder) {
    const b = el('button', hidden.has(role) ? 'off' : '', role.toUpperCase());
    b.title = 'Show or hide this camera';
    b.onclick = () => { hidden.has(role) ? hidden.delete(role) : hidden.add(role); renderDetail(true); };
    chips.appendChild(b);
  }

  shown = [];
  const box = $('content');
  box.replaceChildren();
  (mode === 'sets' ? renderSets : renderCams)(box);
  if (keepScroll) view.scrollTop = top;
  else if (focusSample) {
    const card = box.querySelector('.setcard.focus');
    if (card) card.scrollIntoView({ block: 'start' });
    focusSample = null;
  } else view.scrollTop = 0;
}

function cell(m, role, label) {
  if (!hasView(m, role)) {
    const miss = el('div', 'cell missing', 'None');
    miss.title = (m.missing_views || {})[role] || 'No picture from this camera';
    return miss;
  }
  const a = el('a', 'cell');
  a.href = imageUrl(m, role, 'image');
  a.title = `${role.toUpperCase()} · ${label} · ${m.id}`;
  const im = document.createElement('img');
  im.loading = 'lazy';
  im.alt = '';
  im.src = imageUrl(m, role, 'thumb');
  im.onerror = () => {                       // one more try, then say so instead of a broken-picture icon
    if (!im.dataset.retried) { im.dataset.retried = '1'; im.src = imageUrl(m, role, 'thumb') + '&r=' + Date.now(); return; }
    im.remove();
    a.appendChild(document.createTextNode('Failed to load'));
    a.classList.add('failed');
  };
  a.appendChild(im);
  shown.push({ m, role });
  const index = shown.length - 1;
  a.onclick = (e) => { e.preventDefault(); openLightbox(index); };
  return a;
}

// one card per set: rows = cuts (before / center / after), columns = cameras
function renderSets(box) {
  const roles = camOrder.filter((r) => !hidden.has(r));
  sets.forEach((s, i) => {
    const card = el('div', 'setcard' + (picked.has(s.key) ? ' picked' : '') + (focusSample && s.cuts.some((c) => c.id === focusSample) ? ' focus' : ''));
    const head = el('div', 'sethead');
    const pick = el('label', 'pick');
    const box1 = document.createElement('input');
    box1.type = 'checkbox';
    box1.checked = picked.has(s.key);
    box1.title = 'Tick to download this set';
    box1.onchange = () => {
      box1.checked ? picked.add(s.key) : picked.delete(s.key);
      card.classList.toggle('picked', box1.checked);
      updateDownload();
    };
    pick.append(box1, el('b', '', `Set ${sets.length - i}`));
    head.append(pick, el('span', '', fmtTime(s.time)),
      el('span', '', s.source === 'auto' ? 'Auto' : 'Manual'), el('span', '', `${s.cuts.length} frames`));
    const del = el('button', 'secondary delBtn', 'Delete set');
    del.title = 'Delete every picture of this set (all cuts, all cameras)';
    del.onclick = () => deleteSet(s, sets.length - i);
    head.appendChild(del);
    const grid = el('div', 'setgrid');
    grid.style.gridTemplateColumns = `64px repeat(${roles.length}, minmax(0, 1fr))`;
    grid.appendChild(el('div'));
    for (const r of roles) grid.appendChild(el('div', 'colhead', r.toUpperCase()));
    for (const m of s.cuts) {
      const row = el('div', 'rowhead', cutName(m));
      row.appendChild(el('small', '', clock(m.timestamp)));
      grid.appendChild(row);
      for (const r of roles) grid.appendChild(cell(m, r, cutName(m)));
    }
    card.append(head, grid);
    box.appendChild(card);
  });
}

// one section per camera: every picture that camera took of this shoe, set after set
function renderCams(box) {
  for (const role of camOrder.filter((r) => !hidden.has(r))) {
    const sec = el('div', 'camsec');
    const grid = el('div', 'camgrid');
    let n = 0;
    sets.forEach((s, i) => {
      for (const m of s.cuts) {
        if (!hasView(m, role)) continue;
        n += 1;
        const tile = el('div');
        tile.append(cell(m, role, cutName(m)), el('div', 'cap', `Set ${sets.length - i} · ${cutName(m)} · ${fmtTime(m.timestamp)}`));
        grid.appendChild(tile);
      }
    });
    const h = el('h3', '', role.toUpperCase());
    h.appendChild(el('span', '', `${n} images`));
    sec.append(h, grid);
    box.appendChild(sec);
  }
}

// ---- download the ticked sets as one zip (pictures + the data saved with them) ---------------------------

const MAX_DOWNLOAD_FRAMES = 600;   // the server's limit (the ids travel in the address)
const pickedIds = () => sets.filter((s) => picked.has(s.key)).flatMap((s) => s.cuts.map((m) => m.id));

function updateDownload() {
  const n = sets.filter((s) => picked.has(s.key)).length;
  $('dlBtn').textContent = `Download selected (${n})`;
  $('dlBtn').disabled = n === 0;
  $('selAll').disabled = sets.length === 0 || n === sets.length;
  $('selNone').disabled = n === 0;
}

function setAllPicked(on) {
  for (const s of sets) on ? picked.add(s.key) : picked.delete(s.key);
  renderDetail(true);
}

$('selAll').onclick = () => setAllPicked(true);
$('selNone').onclick = () => setAllPicked(false);
$('dlBtn').onclick = () => {
  const ids = pickedIds();
  if (!ids.length) return;
  if (ids.length > MAX_DOWNLOAD_FRAMES) {
    alert(`Too many frames (${ids.length}). Download at most ${MAX_DOWNLOAD_FRAMES} frames at a time: untick some sets.`);
    return;
  }
  const a = document.createElement('a');        // the browser downloads the zip itself (nothing is held in the page)
  a.href = '/api/samples/download?ids=' + ids.map(encodeURIComponent).join(',');
  a.download = '';
  document.body.appendChild(a);
  a.click();
  a.remove();
};

// ---- delete a whole set (all its cuts, every camera) ---------------------------------------------------

async function deleteSet(s, number) {
  const images = s.cuts.reduce((n, m) => n + Object.keys(m.views || { x: 1 }).length, 0);
  if (!confirm(`Delete Set ${number}?
${fmtTime(s.time)} · ${s.cuts.length} frames · ${images} images
`
    + 'The pictures cannot be restored.')) return;
  try {
    const r = await fetch('/api/samples/delete', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ids: s.cuts.map((m) => m.id) }),
    });
    if (!r.ok) throw new Error('HTTP ' + r.status);
  } catch (e) {
    alert('Could not delete the set: ' + e.message);
  }
  clearTimeout(refreshTimer);
  await refreshAll();
}

$('modeSets').onclick = () => { mode = 'sets'; renderDetail(false); setUrl(); };
$('modeCams').onclick = () => { mode = 'cams'; renderDetail(false); setUrl(); };
let searchTimer = null;
$('search').addEventListener('input', () => {   // the server searches: wait until the typing pauses
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => refreshShoes().catch(() => {}), 250);
});

// ---- lightbox: the picture at full size, ◀ ▶ step through the pictures on screen ---------------------

const lightbox = $('lightbox');

function showLightbox() {
  const { m, role } = shown[lbIndex];
  $('lbImg').src = imageUrl(m, role, 'image');
  $('lbCaption').textContent = `${shoeName(current)} · ${role.toUpperCase()} · ${cutName(m)} · ${fmtTime(m.timestamp)} `
    + `· ${lbIndex + 1} / ${shown.length}`;
  $('lbOpen').href = imageUrl(m, role, 'image');
  $('lbPrev').disabled = lbIndex <= 0;
  $('lbNext').disabled = lbIndex >= shown.length - 1;
}

function openLightbox(index) {
  lbIndex = index;
  lightbox.hidden = false;
  showLightbox();
}

function closeLightbox() {
  lightbox.hidden = true;
  $('lbImg').removeAttribute('src');
  if (staleWhileOpen) { staleWhileOpen = false; refreshAll(); }
}

const step = (d) => { if (!lightbox.hidden && shown[lbIndex + d]) { lbIndex += d; showLightbox(); } };
$('lbPrev').onclick = () => step(-1);
$('lbNext').onclick = () => step(1);
$('lbClose').onclick = closeLightbox;
lightbox.addEventListener('click', (e) => { if (e.target === lightbox) closeLightbox(); });
document.addEventListener('keydown', (e) => {
  if (lightbox.hidden) return;
  if (e.key === 'Escape') closeLightbox();
  else if (e.key === 'ArrowLeft') step(-1);
  else if (e.key === 'ArrowRight') step(1);
});

// ---- live updates: a new capture shows up without reloading --------------------------------------------

let staleWhileOpen = false;
let refreshTimer = null;

async function refreshAll() {
  try {
    await refreshShoes();
    if (current === undefined) return;
    if (shoes.some((s) => s.shoe_id === current)) await selectShoe(current, true);
    else if (shoes.length) await selectShoe(shoes[0].shoe_id);   // the last set of this shoe was deleted
    else { current = undefined; metas = []; sets = []; renderDetail(false); }
  } catch (e) { /* the next event tries again */ }
}

const source = new EventSource('/events?topics=data');
let connected = false;
source.onopen = () => {
  if (connected) refreshAll();   // reconnected: catch up on what was missed meanwhile
  connected = true;
};
source.onmessage = (e) => {
  let data;
  try { data = JSON.parse(e.data); } catch (err) { return; }
  if (data.key === 'resync') { refreshAll(); return; }
  if (data.key !== 'samples_changed') return;
  clearTimeout(refreshTimer);
  refreshTimer = setTimeout(() => (lightbox.hidden ? refreshAll() : (staleWhileOpen = true)), 700);
};

// ---- start ----------------------------------------------------------------------------------------------

async function init() {
  const params = new URLSearchParams(location.search);
  if (params.get('mode') === 'cams') mode = 'cams';
  dateFilter = params.get('date') || '';
  try {
    const st = await getJson('/api/state?light=1');
    apiCams = st.cams.map((c) => c.role);
    document.title = 'ON Data Collector';
  } catch (e) { /* the page works without it */ }
  let want;                                           // which shoe to open first
  if (params.get('sample')) {
    try {
      const m = await getJson('/api/samples/' + encodeURIComponent(params.get('sample')));
      want = m.shoe_id || null;
      focusSample = m.id;
    } catch (e) { /* unknown sample: fall through to the newest shoe */ }
  } else if (params.has('shoe')) {
    want = params.get('shoe') || null;
  }
  current = want;                                     // the list then includes it, whatever the search says
  await refreshShoes();
  if (want === undefined || !shoes.some((s) => s.shoe_id === want)) want = shoes.length ? shoes[0].shoe_id : undefined;
  if (want === undefined) { renderDetail(false); return; }
  await selectShoe(want);
}

init();
