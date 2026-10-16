"""Routes that hold the connection: server-sent events, preview snapshots and the MJPEG stream."""

import queue
import select
import socket
import time

from ..core.events import subscribe, unsubscribe

PING_SEC = 15   # a comment line now and then keeps proxies and the browser from closing an idle stream


class StreamRoutes:
    def _client_gone(self):
        """Has the browser closed the connection? (Readable and nothing to read = the end of the stream.)"""
        try:
            readable, _, _ = select.select([self.connection], [], [], 0)
            return bool(readable) and self.connection.recv(1, socket.MSG_PEEK) == b""
        except (OSError, ValueError):
            return True

    def _stream_events(self, topics=None):
        """Server-sent events. topics: the groups the page wants (/events?topics=live,qr; see core/events.py)."""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        sub = subscribe(topics=topics)
        try:
            self.wfile.write(b"retry: 3000\n: connected\n\n")
            self.wfile.flush()
            idle = 0.0
            while True:
                try:
                    payload = sub.q.get(timeout=1.0)
                    self.wfile.write(f"data: {payload}\n\n".encode("utf-8"))
                    idle = 0.0
                except queue.Empty:
                    if self._client_gone():   # a page that was closed stops being served (and counted) at once
                        break
                    idle += 1.0
                    if idle < PING_SEC:
                        continue
                    idle = 0.0
                    self.wfile.write(b": ping\n\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
            pass
        finally:
            unsubscribe(sub)

    def _send_snapshot(self, slot, query):
        """Latest preview frame as a single JPEG (the page polls these for the side cameras:
        browsers allow only ~6 connections per host, so one open stream per camera does not fit).

        ?after=<seq>&wait=<ms>: long poll - hold the request until a frame newer than <seq> exists
        (at most <ms>, max 2 s), so the page gets every frame without hammering the server.
        204 = nothing newer yet. The frame's sequence number is returned in X-Frame-Seq."""
        try:
            after = int(query.get("after", ["-1"])[0])
            wait = min(max(float(query.get("wait", ["0"])[0]), 0.0), 2000.0) / 1000.0
        except ValueError:
            self.send_error(400, "bad after/wait")
            return
        deadline = time.monotonic() + wait
        while True:
            jpg, seq = slot.get_preview()
            if jpg is not None and seq != after:
                break
            if time.monotonic() >= deadline:
                break
            time.sleep(0.01)
        if jpg is None:
            self.send_error(404, "no frame yet")
        elif seq == after:
            self.send_response(204)
            self.send_header("X-Frame-Seq", str(seq))
            self.end_headers()
        else:
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", str(len(jpg)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Frame-Seq", str(seq))
            self.end_headers()
            self.wfile.write(jpg)

    def _stream_mjpeg(self, slot):
        self.send_response(200)
        self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
        self.end_headers()
        last_seq = -1
        try:
            while True:
                jpg, seq = slot.get_preview()
                if jpg is not None and seq != last_seq:
                    last_seq = seq
                    self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\n\r\n")
                    self.wfile.write(jpg)
                    self.wfile.write(b"\r\n")
                time.sleep(0.03)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass
