"""SSE pub/sub bus. Any module pushes; the HTTP /events handler drains per-client queues.

A client asks for the groups of events it wants (/events?topics=live,qr), so a page is not sent what it does not
draw. Events of no known group go to everybody. When a client is too slow and its queue overflows, it is
told to fetch everything again ({"key": "resync"}) instead of silently missing events."""

import json
import queue
import threading

# event key -> the group a client subscribes to
GROUPS = {
    "camera_status": "live", "cam": "live", "reference": "live", "camera_config": "live", "stream": "live",
    "rotation": "live", "zoom": "live", "auto_enabled": "live", "auto_capture": "live", "background": "live",
    "state": "live", "live_data": "live",
    "qr": "qr",
    "qr_scan": "qrpage", "qr_stats": "qrpage",
    "samples_changed": "data",
    "predict_changed": "predict",
}
ALWAYS = ("resync",)

_lock = threading.Lock()
_clients = []          # [{"q": Queue, "topics": set | None}]
_listeners = []        # fn(payload) called in the publisher's thread for every broadcast


class _Subscription:
    def __init__(self, maxsize, topics):
        self.q = queue.Queue(maxsize=maxsize)
        self.topics = topics   # None = everything


def subscribe(maxsize=100, topics=None):
    """Returns the subscription handle (its .q is drained by the HTTP handler). topics: a set of group names."""
    sub = _Subscription(maxsize, set(topics) if topics else None)
    with _lock:
        _clients.append(sub)
    return sub


def unsubscribe(sub):
    with _lock:
        try:
            _clients.remove(sub)
        except ValueError:
            pass


def has_subscribers(group):
    """Is anybody listening to this group? (Used to skip building a payload nobody reads.)"""
    with _lock:
        return any(c.topics is None or group in c.topics for c in _clients)


def add_listener(fn):
    """fn(payload) is called for every broadcast, before it is queued; it must be quick and must not raise."""
    _listeners.append(fn)


def _wants(sub, key):
    group = GROUPS.get(key)
    return sub.topics is None or group is None or group in sub.topics or key in ALWAYS


def broadcast(payload):
    """JSON-encode payload and put it into the queue of every client that wants its group (non-blocking)."""
    for fn in list(_listeners):
        try:
            fn(payload)
        except Exception as e:  # a listener must never stop the event
            print(f"[events] listener failed: {e}")
    key = payload.get("key") if isinstance(payload, dict) else None
    msg = json.dumps(payload, ensure_ascii=False)
    with _lock:
        clients = [c for c in _clients if _wants(c, key)]
    for c in clients:
        try:
            c.q.put_nowait(msg)
        except queue.Full:
            _overflow(c)


def _overflow(sub):
    """The client cannot keep up: drop what it has not read and tell it to fetch everything again."""
    try:
        while True:
            sub.q.get_nowait()
    except queue.Empty:
        pass
    try:
        sub.q.put_nowait(json.dumps({"key": "resync"}))
    except queue.Full:
        pass
