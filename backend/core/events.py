"""SSE pub/sub bus. Any module pushes; HTTP /events handler drains per-client queues."""

import json
import queue
import threading

_lock = threading.Lock()
_clients = []


def subscribe(maxsize=100):
    q = queue.Queue(maxsize=maxsize)
    with _lock:
        _clients.append(q)
    return q


def unsubscribe(q):
    with _lock:
        try:
            _clients.remove(q)
        except ValueError:
            pass


def broadcast(payload):
    """JSON-encode payload and put it into every active subscriber's queue (non-blocking)."""
    msg = json.dumps(payload, ensure_ascii=False)
    with _lock:
        clients = list(_clients)
    for q in clients:
        try:
            q.put_nowait(msg)
        except queue.Full:
            pass
