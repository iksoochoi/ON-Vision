"""Alerts for failures of the data collection, sent to a Google Chat webhook.

collect.emit() hands every WARNING / ERROR event (and every recovery) to notify(). To keep the chat usable:
  * the same problem (its key: kind + camera) is sent once per ON_ALERT_COOLDOWN_SEC; the repeats are only
    counted and reported in the next message ("+12 similar since the last message"),
  * problems that come within ON_ALERT_DIGEST_SEC of each other share one message,
  * a recovery ("camera back") is sent only for a problem that was sent before,
  * sending happens on its own thread: a slow or broken webhook never delays the collection, and a failed
    send is retried a few times, then logged (ALERT_FAILED, which is never sent itself).

Settings (.env): ON_ALERT=1, ON_ALERT_WEBHOOK_URL (a secret: never printed or logged), ON_ALERT_PREFIX,
ON_ALERT_COOLDOWN_SEC, ON_ALERT_DIGEST_SEC. `python -m backend.cli.test_alert` sends a test message.

The message is the JSON {"text": ...} that Google Chat webhooks take (*bold* and ```code blocks``` work in it);
a message is kept under MESSAGE_LIMIT characters (Google Chat refuses longer ones), a long digest is split."""

import json
import socket
import ssl
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime
from urllib.parse import parse_qs, urlsplit

from ..core import config as C

RETRY_DELAYS_SEC = (0, 30, 120, 600)   # the delays before each attempt of one message
MAX_PENDING = 200                       # events kept while nothing can be sent
MESSAGE_LIMIT = 3500                    # characters of one chat message (the limit is 4096)
ICONS = {"ERROR": "\U0001F534", "WARNING": "⚠️", "INFO": "✅"}

_lock = threading.Lock()
_wake = threading.Event()
_pending = []                  # events waiting for the next message
_last_sent = {}                # key -> monotonic time it was last sent
_suppressed = {}               # key -> repeats since then
_sent_keys = set()             # problems that were sent and not yet reported as recovered
_thread = None
_status_provider = None        # () -> text of the cameras right now, added to every message
_disabled_reason = None


def set_status_provider(fn):
    """fn() returns text describing the cameras now; it is appended to every message."""
    global _status_provider
    _status_provider = fn


def configured():
    """(ok, why not): are the alerts switched on and complete?"""
    if not C.ALERT:
        return False, "ON_ALERT is 0"
    if not C.ALERT_WEBHOOK_URL:
        return False, "ON_ALERT_WEBHOOK_URL is empty"
    if not C.ALERT_WEBHOOK_URL.startswith(("https://", "http://")):
        return False, "ON_ALERT_WEBHOOK_URL must start with https://"
    return True, ""


def _key(value):
    return tuple(value) if isinstance(value, (list, tuple)) else (value,)


def notify(event, now=None):
    """Hand one event (a dict from collect.emit) to the alerts. Decides whether it is sent; never raises."""
    global _disabled_reason
    ok, why = configured()
    if not ok:
        if C.ALERT and _disabled_reason != why:   # switched on but incomplete: say it once
            _disabled_reason = why
            print(f"[alerts] alerts are on but not usable: {why}")
        return False
    now = time.monotonic() if now is None else now
    key = _key(event["key"])
    with _lock:
        if event.get("recovers"):
            keys = [_key(k) for k in event["recovers"]]
            was = [k for k in keys if k in _sent_keys]
            for k in keys:
                _sent_keys.discard(k)
            if not was and not event.get("alert"):
                return False   # nothing was sent about the problem: nobody needs to hear that it is over
        if event.get("alert"):
            last = _last_sent.get(key)
            if last is not None and now - last < C.ALERT_COOLDOWN_SEC:
                _suppressed[key] = _suppressed.get(key, 0) + 1
                return False
            _last_sent[key] = now
            event = dict(event, repeats=_suppressed.pop(key, 0))
            _sent_keys.add(key)
        _pending.append(event)
        del _pending[:-MAX_PENDING]
    _ensure_thread()
    _wake.set()
    return True


def _ensure_thread():
    global _thread
    with _lock:
        if _thread is None or not _thread.is_alive():
            _thread = threading.Thread(target=_run, name="alert-sender", daemon=True)
            _thread.start()


def _run():
    while True:
        _wake.wait()
        _wake.clear()
        time.sleep(C.ALERT_DIGEST_SEC)   # more problems usually follow the first one: they share the message
        flush()


def _event_text(e):
    """The lines of one event."""
    when = e["time"].strftime("%H:%M:%S")
    head = f"{ICONS.get(e['level'], '')} *{e['kind']}*" + (f" ({e['camera']})" if e.get("camera") else "") + f"  {when}"
    lines = [head, e["message"]]
    if e.get("fields"):
        lines.append(", ".join(f"{k}={v}" for k, v in e["fields"].items()))
    if e.get("repeats"):
        lines.append(f"+{e['repeats']} similar since the last message")
    return lines


def compose(events):
    """The messages (strings, each within MESSAGE_LIMIT) for these events, each with the events it holds:
    [(text, [events])]. The first message starts with the title; the state of the cameras is at the end."""
    host = socket.gethostname()
    title = f"*{C.ALERT_PREFIX} {host}*  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    blocks, shown = [], set()
    for e in events:
        lines = _event_text(e)
        if e.get("snapshot") and e["snapshot"] not in shown:   # cameras that stall together share one table
            shown.add(e["snapshot"])
            lines += ["```", *str(e["snapshot"]).splitlines(), "```"]
        blocks.append(("\n".join(lines), e))
    footer = ""
    if _status_provider is not None:
        try:
            footer = "State now:\n```\n" + str(_status_provider()) + "\n```"
        except Exception as ex:  # the message matters more than the snapshot
            footer = f"(the state could not be read: {ex})"
    messages, current, held = [], title, []
    for text, e in blocks:
        text = text[:MESSAGE_LIMIT - 200]
        if len(current) + len(text) + 2 > MESSAGE_LIMIT and held:
            messages.append((current, held))
            current, held = f"*{C.ALERT_PREFIX} {host}* (continued)", []
        current += "\n\n" + text
        held.append(e)
    if footer and len(current) + len(footer) + 2 <= MESSAGE_LIMIT:
        current += "\n\n" + footer
    messages.append((current, held))
    return messages


def _context():
    """The TLS settings: certifi's certificates when it is installed (python.org builds of Python on a Mac come
    without any), else the system's."""
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


def mask(text):
    """A text without the webhook URL (it carries a key and a token). A server may echo only a part of it (the
    path with the query), so the URL, its path and every value of its query are each taken out."""
    text = str(text)
    url = C.ALERT_WEBHOOK_URL
    if not url:
        return text
    parts = urlsplit(url)
    text = text.replace(url, "<webhook>")
    if parts.path and parts.query:
        text = text.replace(f"{parts.path}?{parts.query}", "<webhook-path>")
    for values in parse_qs(parts.query).values():
        for value in values:
            if len(value) >= 6:
                text = text.replace(value, "<secret>")
    return text


def send_message(text):
    """POST one text to the webhook as {"text": ...}. Raises on any failure (network, HTTP status)."""
    ok, why = configured()
    if not ok:
        raise RuntimeError(f"alerts are not configured: {why}")
    request = urllib.request.Request(
        C.ALERT_WEBHOOK_URL, data=json.dumps({"text": text}).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=UTF-8"}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=15, context=_context()) as response:
            response.read()
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")[:300]
        raise RuntimeError(f"HTTP {e.code} {e.reason}: {mask(body)}") from None


def _clean(error):
    """An error text without the webhook URL, in case a library echoes it."""
    return mask(f"{type(error).__name__}: {error}")


def flush(sender=None, sleep=time.sleep):
    """Send everything that is waiting (retrying a few times per message). Returns True when all went out (or
    nothing was waiting). sender / sleep: replaceable, for tests."""
    from . import collect
    sender = sender or send_message
    with _lock:
        events = list(_pending)
        _pending.clear()
    if not events:
        return True
    messages = compose(events)
    for index, (text, held) in enumerate(messages):
        last_error = None
        for attempt, delay in enumerate(RETRY_DELAYS_SEC, 1):
            if delay:
                sleep(delay)
            try:
                sender(text)
                collect.emit("ALERT_SENT", "INFO", f"message sent ({len(held)} event(s))", alert=False)
                break
            except Exception as e:
                last_error = _clean(e)
                collect.emit("ALERT_FAILED", "ERROR",
                             f"attempt {attempt}/{len(RETRY_DELAYS_SEC)} failed: {last_error}", alert=False)
        else:
            unsent = [e for _, part in messages[index:] for e in part]
            with _lock:   # keep them for the next message
                _pending[:0] = unsent
                del _pending[:-MAX_PENDING]
            return False
    return True
