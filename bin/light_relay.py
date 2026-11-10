"""TCP relay to the lighting controller, run by a macOS LaunchDaemon (./bin/install_launchd.sh light-relay-install).

    python3 light_relay.py --listen-port 50001 --target 192.168.0.45:50000

Why: macOS (15 and later) asks every app for "Local Network" access. The app, started by a LaunchAgent, is refused
(Errno 65 "No route to host") even with Python allowed in System Settings, while the same code works from the
Terminal. LaunchDaemons are not subject to that check: this relay runs as one, listens on 127.0.0.1 only and passes
the bytes of each connection to the controller and back, unchanged. The app connects to it when ON_LIGHT_RELAY_PORT
is set. Standard library only, no project code: the installer copies this file to a folder only root can change."""

import argparse
import socket
import sys
import threading
import time


def log(message):
    print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {message}", flush=True)


def pipe(src, dst):
    """Copy until src closes, then close dst's sending side (the other direction finishes by itself)."""
    try:
        while True:
            data = src.recv(4096)
            if not data:
                break
            dst.sendall(data)
    except OSError:
        pass
    finally:
        try:
            dst.shutdown(socket.SHUT_WR)
        except OSError:
            pass


def handle(client, target, timeout):
    try:
        upstream = socket.create_connection(target, timeout=timeout)
    except OSError as e:
        log(f"cannot reach {target[0]}:{target[1]}: {e}")
        client.close()
        return
    upstream.settimeout(30)
    client.settimeout(30)
    back = threading.Thread(target=pipe, args=(upstream, client), daemon=True)
    back.start()
    pipe(client, upstream)
    back.join(30)
    upstream.close()
    client.close()


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--listen-port", type=int, default=50001, help="port on 127.0.0.1 (default 50001)")
    ap.add_argument("--target", default="192.168.0.45:50000", help="the controller, host:port")
    ap.add_argument("--timeout", type=float, default=3.0, help="seconds to connect to the controller")
    args = ap.parse_args()
    host, _, port = args.target.rpartition(":")
    target = (host, int(port))

    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        server.bind(("127.0.0.1", args.listen_port))
    except OSError as e:
        sys.exit(f"cannot listen on 127.0.0.1:{args.listen_port}: {e}")
    server.listen(8)
    log(f"light relay: 127.0.0.1:{args.listen_port} -> {target[0]}:{target[1]}")
    while True:
        client, _ = server.accept()
        threading.Thread(target=handle, args=(client, target, args.timeout), daemon=True).start()


if __name__ == "__main__":
    main()
