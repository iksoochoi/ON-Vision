// Zoom and pan of a picture shown large (the lightboxes of Gallery / Live / Predict / Image Lab, the Label picture).
//   mouse wheel / trackpad scroll or pinch   zoom in and out where the pointer is (1x .. 10x)
//   drag (left button; Label page: right or middle button, the left one draws boxes)   move the enlarged picture
//   double-click   2.5x at that spot, or back to the whole picture
//   keys + / - / 0   zoom in / out / whole picture (while the view is on screen)
//   two fingers on a touch screen   pinch to zoom, drag to move
// Only a CSS transform of the target is changed: the picture keeps its layout size, and boxes drawn inside the target
// (Predict, Label) follow it. getBoundingClientRect of anything inside gives the zoomed place on screen.
//
// const z = ImageZoom(view, target, { panButtons: [0], onBackground: close, resetOnLoad: img });
//   view: the element that clips (gets the class zoom-view), target: the element moved (an element or a function
//   returning it: the Label page builds a new one per picture). z.reset(), z.toggleAt(clientX, clientY).
(function () {
  const MAX = 10;
  const STEP = 2.5;

  window.ImageZoom = function (view, target, opts) {
    opts = opts || {};
    const panButtons = opts.panButtons || [0];
    const getTarget = typeof target === 'function' ? target : () => target;
    let s = 1, tx = 0, ty = 0;
    let lastEl = null;
    view.classList.add('zoom-view');
    const badge = document.createElement('div');
    badge.className = 'zoom-badge';
    view.appendChild(badge);

    function el() {
      const t = getTarget();
      if (t !== lastEl) {   // a new picture element (Label page): starts at 1x
        lastEl = t;
        s = 1; tx = 0; ty = 0;
        if (t) t.classList.add('zoom-target');
      }
      return t;
    }

    function apply() {
      const t = el();
      if (!t) return;
      t.style.transform = s === 1 && !tx && !ty ? '' : `translate(${tx}px, ${ty}px) scale(${s})`;
      view.classList.toggle('zoomed', s > 1);
      badge.textContent = s > 1 ? `${Math.round(s * 100)}% · drag to move · double-click: whole picture`
        : 'Scroll to zoom · double-click to enlarge';
    }

    // the target's place without the transform (transform-origin 0 0: translate moves it, scale grows from its corner)
    function base(t) {
      const r = t.getBoundingClientRect();
      return { left: r.left - tx, top: r.top - ty, w: r.width / s, h: r.height / s };
    }

    function clamp(t, b) {
      if (s <= 1.0001) { s = 1; tx = 0; ty = 0; return; }
      const v = view.getBoundingClientRect();
      const fit = (pos, size, vStart, vSize, bStart) => {
        if (size <= vSize) return vStart + (vSize - size) / 2 - bStart;           // smaller than the view: centred
        return Math.min(vStart - bStart, Math.max(vStart + vSize - size - bStart, pos));   // no empty edge
      };
      tx = fit(tx, b.w * s, v.left, v.width, b.left);
      ty = fit(ty, b.h * s, v.top, v.height, b.top);
    }

    function zoomAt(cx, cy, next) {
      const t = el();
      if (!t || !t.getBoundingClientRect().width) return;
      const b = base(t);
      next = Math.min(MAX, Math.max(1, next));
      const px = (cx - b.left - tx) / s;   // the point of the picture under the pointer stays under it
      const py = (cy - b.top - ty) / s;
      tx = cx - b.left - px * next;
      ty = cy - b.top - py * next;
      s = next;
      clamp(t, b);
      apply();
    }

    function reset() { s = 1; tx = 0; ty = 0; apply(); }

    function center() {
      const v = view.getBoundingClientRect();
      return [v.left + v.width / 2, v.top + v.height / 2];
    }

    function toggleAt(cx, cy) {
      if (s > 1) reset();
      else zoomAt(cx, cy, STEP);
    }

    view.addEventListener('wheel', (e) => {
      if (!el()) return;
      e.preventDefault();
      const dy = e.deltaY * (e.deltaMode === 1 ? 16 : e.deltaMode === 2 ? 400 : 1);
      zoomAt(e.clientX, e.clientY, s * Math.exp(-dy * (e.ctrlKey ? 0.01 : 0.002)));   // ctrlKey: a trackpad pinch
    }, { passive: false });

    // ---- drag to move, pinch on a touch screen ----
    const pointers = new Map();
    let drag = null, pinch = null, moved = false;
    view.addEventListener('pointerdown', (e) => {
      const t = el();
      if (!t) return;
      if (pointers.size === 0) moved = false;   // a new press: the click after it is a click again
      if (e.pointerType === 'touch') {
        pointers.set(e.pointerId, [e.clientX, e.clientY]);
        view.setPointerCapture(e.pointerId);
        if (pointers.size === 2) {
          const [a, b] = [...pointers.values()];
          pinch = { d: Math.hypot(a[0] - b[0], a[1] - b[1]), s };
          drag = null;
        } else if (pointers.size === 1) {
          drag = { x: e.clientX, y: e.clientY, tx, ty };
          moved = false;
        }
        return;
      }
      if (!panButtons.includes(e.button) || s <= 1) return;
      e.preventDefault();
      view.setPointerCapture(e.pointerId);
      drag = { x: e.clientX, y: e.clientY, tx, ty, id: e.pointerId };
      moved = false;
      view.classList.add('panning');
    });
    view.addEventListener('pointermove', (e) => {
      if (pointers.has(e.pointerId)) pointers.set(e.pointerId, [e.clientX, e.clientY]);
      if (pinch && pointers.size === 2) {
        const [a, b] = [...pointers.values()];
        zoomAt((a[0] + b[0]) / 2, (a[1] + b[1]) / 2, pinch.s * Math.hypot(a[0] - b[0], a[1] - b[1]) / pinch.d);
        moved = true;
        return;
      }
      if (!drag || s <= 1) return;
      const dx = e.clientX - drag.x, dy = e.clientY - drag.y;
      if (!moved && Math.hypot(dx, dy) < 4) return;
      moved = true;
      const t = el();
      const b = base(t);
      tx = drag.tx + dx;
      ty = drag.ty + dy;
      clamp(t, b);
      apply();
    });
    const end = (e) => {
      pointers.delete(e.pointerId);
      if (pointers.size < 2) pinch = null;
      if (pointers.size === 0) drag = null;
      if (drag && drag.id === e.pointerId) drag = null;
      view.classList.remove('panning');
    };
    view.addEventListener('pointerup', end);
    view.addEventListener('pointercancel', end);
    // a drag is not a click (the page's click: close the lightbox, open a picture ...)
    view.addEventListener('click', (e) => {
      if (moved) { moved = false; e.stopPropagation(); e.preventDefault(); return; }
      const t = el();
      if (opts.onBackground && (!t || !t.contains(e.target)) && !badge.contains(e.target)) opts.onBackground(e);
    }, true);
    if (panButtons.includes(2)) {
      view.addEventListener('contextmenu', (e) => { if (s > 1 || moved) e.preventDefault(); });
    }
    if (!opts.noDoubleClick) {
      view.addEventListener('dblclick', (e) => {
        const t = el();
        if (!t || !t.contains(e.target)) return;
        e.preventDefault();
        toggleAt(e.clientX, e.clientY);
      });
    }

    document.addEventListener('keydown', (e) => {
      if (!view.offsetParent || !el()) return;   // not on screen
      const tag = (document.activeElement && document.activeElement.tagName) || '';
      if (tag === 'INPUT' || tag === 'SELECT' || tag === 'TEXTAREA') return;
      if (e.key === '+' || e.key === '=') zoomAt(...center(), s * 1.5);
      else if (e.key === '-' || e.key === '_') zoomAt(...center(), s / 1.5);
      else if (e.key === '0') reset();
      else return;
      e.preventDefault();
    });
    window.addEventListener('resize', () => { const t = el(); if (t && s > 1) { clamp(t, base(t)); apply(); } });
    if (opts.resetOnLoad) opts.resetOnLoad.addEventListener('load', reset);

    apply();
    return { reset, toggleAt, zoomAt, get scale() { return s; } };
  };
})();
