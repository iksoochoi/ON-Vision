"""The shoe's QR value for one capture: voting over the codes read in the saved pictures, the live scanners'
reads and the enlarged-tile search."""

import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed

from ..core import config as C
from ..qr.decode import decode_qr_tiles, qr_roles
from ..qr.hub import hub as qr_hub

ZOOM_THREADS = 4   # pictures searched at the same time in the zoom step


def vote(codes):
    """(text, others): the value read most often among `codes`, and the other values, or (None, [])."""
    votes = Counter(c["text"].strip() for c in codes if c["text"].strip())
    ranked = [text for text, _ in votes.most_common()]
    return (ranked[0], ranked[1:]) if ranked else (None, [])


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


def live_hit(since, until, enqueued):
    """The live scanners' code in [since, until], as (text, source) or None."""
    hit = qr_hub.live_code(since, until)
    if hit and hit["text"].strip():
        return hit["text"].strip(), "live_late" if hit["epoch"] > enqueued else "live"
    return None


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


def resolve_shoe(prepared, enqueued, window):
    """The shoe's QR value for the whole capture: (text, source, others) or (None, None, []).
    Sources, in the order they are tried:
      capture       read in the saved pictures of the shoe pass (all shots, the QR cameras)
      live          the live scanner read it while the shoe passed (window[0] .. window[1])
      capture_zoom  read in the saved pictures after searching them in enlarged tiles
      live_late     the live scanner read it after the capture, within the wait (up to window[1])
    Live reads are always looked up in the whole window [since, until]: a slow search must not make
    the writer miss a code that was read while it was busy."""
    since, until = window
    if not C.QR_ENABLED:
        return None, None, []
    text, others = vote([c for _, _, _, codes in prepared for c in codes])
    if text:
        return text, "capture", others
    hit = live_hit(since, until, enqueued)
    if hit:
        return hit[0], hit[1], []
    if C.QR_ZOOM:
        text, others = vote(zoom_search(prepared))
        if text:
            return text, "capture_zoom", others
    while True:   # nothing yet: the code may be read a moment after the capture
        hit = live_hit(since, until, enqueued)
        if hit:
            return hit[0], hit[1], []
        if time.time() >= until:
            return None, None, []
        time.sleep(0.2)
