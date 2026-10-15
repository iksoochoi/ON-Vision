"""The shoe's QR value for one capture: voting over the codes read in the saved pictures, the live scanners'
reads and the enlarged-tile search."""

import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed

from ..core import config as C
from ..qr.decode import decode_qr_tiles, qr_roles
from ..qr.hub import hub as qr_hub

ZOOM_THREADS = 4   # pictures searched at the same time in the zoom step


def weight(cx, band):
    """How much a code at horizontal place cx (0..1 of the picture) counts for the shoe in the middle of it:
    1 in the middle, falling to 0 at `band` (a fraction of the width) from it. band 0 / unknown place = 1."""
    if not band or cx is None:
        return 1.0
    return max(0.0, 1.0 - abs(cx - 0.5) / band)


def code_x(code):
    """The horizontal place (0..1) of a read code in its picture, None when it is not known."""
    xs = [p[0] for p in code.get("points") or []]
    return sum(xs) / len(xs) if xs else None


def vote(codes, band=0.0):
    """(text, others): the value with the most weight among `codes` (a code in the middle of the picture counts
    more than one further out, see weight), and the other values. With `band`, codes outside it do not count:
    if there is nothing else, (None, [the values that were left out])."""
    score, order = Counter(), []
    for c in codes:
        text = c["text"].strip()
        if not text:
            continue
        if text not in order:
            order.append(text)
        score[text] += weight(code_x(c), band)
    winners = [t for t in order if score[t] > 0]
    if not winners:
        return None, order
    top = max(winners, key=lambda t: (score[t], -order.index(t)))
    return top, [t for t in order if t != top]


def qr_sizes(meta):
    """{role: (px, read)} for the cameras that saw a QR code in this set: its side in pixels, and whether it
    was read (False = located but not readable). Only the best per camera. {} when no code was placed."""
    out = {}
    for code in meta.get("qr") or []:
        px = code.get("size_px")
        if px and px > out.get(code["view"], (0, True))[0]:
            out[code["view"]] = (round(px), True)
    for role, info in (meta.get("views") or {}).items():
        if role not in out and info.get("qr_seen_px"):
            out[role] = (info["qr_seen_px"], False)
    return out


def live_hit(since, until, enqueued, center_ts=None, band=0.0):
    """The live scanners' code for this shoe pass: (text, source, others) or None. With a band and the time of
    the center shot (automatic captures) it is the code in the middle of the picture around that moment, else
    the code read most often in [since, until] (epoch seconds)."""
    if band and center_ts is not None:
        hit, others = qr_hub.live_code_at(center_ts, C.QR_MATCH_SEC, band)
    else:
        hit, others = qr_hub.live_code(since, until), []
    if hit and hit["text"].strip():
        return hit["text"].strip(), "live_late" if hit["epoch"] > enqueued else "live", others
    return None, None, others


def zoom_search(prepared):
    """Search the saved pictures of the QR cameras in enlarged tiles, side by side (the decoder
    releases the GIL), the center shots first. Stops at the first picture that gives a code.
    Returns the codes found (also added to the sets' own code lists), [] if none."""
    def centre_first(item):
        kind = (item[1].get("burst") or {}).get("kind")
        return 0 if kind == "center" else 1
    jobs = []
    for _, meta, images, codes in sorted(prepared, key=centre_first):
        jobs += [(codes, role, images[role]) for role in qr_roles(list(images))]
    if not jobs:
        return []
    pool = ThreadPoolExecutor(max_workers=min(ZOOM_THREADS, len(jobs)), thread_name_prefix="qr-zoom")
    try:
        futures = {pool.submit(decode_qr_tiles, img): (codes, role) for codes, role, img in jobs}
        for future in as_completed(futures):
            codes, role = futures[future]
            try:
                found = future.result()
            except Exception as e:  # best effort, like the first search
                print(f"QR zoom search failed: {e}")
                continue
            if found:
                found = [{"view": role, **c} for c in found]
                codes.extend(found)   # they end up in the sample JSON like any other code
                return found
        return []
    finally:
        pool.shutdown(wait=False, cancel_futures=True)


def resolve_shoe(prepared, enqueued, window, center_ts=None):
    """The shoe's QR value for the whole capture: (text, source, others) or (None, None, ignored).
    Sources, in the order they are tried:
      capture       read in the saved pictures of the shoe pass (all shots, the QR cameras)
      live          the live scanner read it while the shoe passed
      capture_zoom  read in the saved pictures after searching them in enlarged tiles
      live_late     the live scanner read it after the capture, within the wait
    center_ts: the time of the center shot (automatic captures). With ON_QR_CENTER_BAND the codes are then
    weighted by how close to the middle of the picture they are (the shoe being saved is in the middle, its
    neighbours are further out) and the live reads count only around that moment. `others`: the other values
    that were read (a neighbour's), also when nothing was chosen.
    Live reads are looked up again and again until window[1]: a slow search must not make the writer miss a
    code that was read while it was busy."""
    since, until = window
    if not C.QR_ENABLED:
        return None, None, []
    band = C.QR_CENTER_BAND if center_ts is not None else 0.0
    ignored = []

    def note(texts):
        ignored.extend(t for t in texts if t not in ignored)

    text, others = vote([c for _, _, _, codes in prepared for c in codes], band)
    if text:
        return text, "capture", others
    note(others)
    text, source, others = live_hit(since, until, enqueued, center_ts, band)
    if text:
        return text, source, others
    note(others)
    if C.QR_ZOOM:
        text, others = vote(zoom_search(prepared), band)
        if text:
            return text, "capture_zoom", others
        note(others)
    while True:   # nothing yet: the code may be read a moment after the capture
        text, source, others = live_hit(since, until, enqueued, center_ts, band)
        if text:
            return text, source, others
        note(others)
        if time.time() >= until:
            return None, None, ignored
        time.sleep(0.2)
