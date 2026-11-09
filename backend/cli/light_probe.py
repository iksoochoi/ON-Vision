"""Read the brightness of the lighting controller (L-light LPW series) over Ethernet or RS-232C. READ ONLY: nothing is set.

    .venv/bin/python -m backend.cli.light_probe                          # 192.168.0.45:50000, TCP then UDP
    .venv/bin/python -m backend.cli.light_probe --host 192.168.0.45 --proto udp
    .venv/bin/python -m backend.cli.light_probe --serial /dev/cu.usbserial-XXXX    # RS-232C, 19200 bps (needs pyserial)

What it sends (manual "LPW-Specification & Protocol" v1.7, packet: 01 | OP | length | address | data | 04):
    01 01 00 21 04   read CSVR: the value (0..255) of every channel   -> 01 01 10 21 v1 .. v16 04
    01 01 00 32 04   read CSMR: Normal / Trigger mode (8CH firmware 2024+; others may not answer)
Only reads (OP 01) are sent, so the lights and the saved settings stay as they are. Everything received is printed
in hex too: the manual's examples do not agree with each other everywhere, the raw bytes show what this unit does.

The manual leaves open whether the unit answers on TCP or UDP and to which port it sends (it keeps a "server IP /
port", factory 192.168.0.225:50000). --proto both (default) tries TCP, then UDP; for UDP the answer is awaited on the
socket that sent (and, with --listen-port, also on that local port). The Mac needs an address in the unit's network
(factory 192.168.0.x)."""

import argparse
import select
import socket
import sys
import time

READS = {
    "values": bytes([0x01, 0x01, 0x00, 0x21, 0x04]),   # CSVR
    "mode": bytes([0x01, 0x01, 0x00, 0x32, 0x04]),     # CSMR
}
ACK, NAK = 0x06, 0x15


def hexs(data):
    return " ".join(f"{b:02X}" for b in data) or "(nothing)"


def parse(data):
    """Packets 01 OP LEN ADDR data.. 04 in the bytes received: [(address, data bytes)]. Other bytes are skipped."""
    out, i = [], 0
    while i + 4 < len(data):
        if data[i] != 0x01:
            i += 1
            continue
        length, addr = data[i + 2], data[i + 3]
        end = i + 4 + length
        if end < len(data) and data[end] == 0x04:
            out.append((addr, bytes(data[i + 4:end])))
            i = end + 1
        else:
            i += 1
    return out


def describe(name, data):
    """What the answer says, in words."""
    if not data:
        return "no answer"
    if data == bytes([NAK]):
        return "NAK: the unit refused the request"
    packets = parse(data)
    if name == "values":
        for addr, body in packets:
            if addr == 0x21 and body:
                return "brightness " + ", ".join(f"CH{i + 1}={v}" for i, v in enumerate(body)) + \
                       "  (16 values are sent whatever the unit has: only its own channels count)"
    if name == "mode":
        for addr, body in packets:
            if addr == 0x32 and body:
                return {0: "Normal mode (front panel)", 1: "Trigger mode (rear 12-24 V inputs)"}.get(body[0], f"mode {body[0]}")
    return "not understood (see the raw bytes)"


def ask_tcp(host, port, request, timeout):
    with socket.create_connection((host, port), timeout=timeout) as s:
        s.sendall(request)
        return receive(s, timeout)


def ask_udp(host, port, request, timeout, listen_port=None):
    socks = []
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    socks.append(s)
    if listen_port:   # the unit may answer to its configured server port instead of the sender
        extra = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        extra.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        extra.bind(("", listen_port))
        socks.append(extra)
    try:
        s.sendto(request, (host, port))
        data, until = b"", time.monotonic() + timeout
        while time.monotonic() < until:
            ready, _, _ = select.select(socks, [], [], max(0.0, until - time.monotonic()))
            if not ready:
                break
            for r in ready:
                part, _ = r.recvfrom(4096)
                data += part
            if data.endswith(b"\x04") or data in (bytes([ACK]), bytes([NAK])):
                break
        return data
    finally:
        for x in socks:
            x.close()


def receive(s, timeout):
    """Bytes until a packet end (04), a lone ACK/NAK, or the timeout."""
    data, until = b"", time.monotonic() + timeout
    s.settimeout(0.2)
    while time.monotonic() < until:
        try:
            part = s.recv(4096)
        except socket.timeout:
            if data:
                break
            continue
        if not part:
            break
        data += part
        if data.endswith(b"\x04") or data in (bytes([ACK]), bytes([NAK])):
            break
    return data


def ask_serial(device, request, timeout):
    try:
        import serial
    except ImportError:
        sys.exit("RS-232C needs pyserial:  .venv/bin/pip install pyserial")
    with serial.Serial(device, 19200, timeout=0.2) as port:
        port.reset_input_buffer()
        port.write(request)
        data, until = b"", time.monotonic() + timeout
        while time.monotonic() < until:
            part = port.read(256)
            if part:
                data += part
                if data.endswith(b"\x04") or data in (bytes([ACK]), bytes([NAK])):
                    break
            elif data:
                break
        return data


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default="192.168.0.45", help="the unit's IP (factory 192.168.0.45; the label shows it)")
    ap.add_argument("--port", type=int, default=50000, help="the unit's port (factory 50000)")
    ap.add_argument("--proto", choices=("both", "tcp", "udp"), default="both")
    ap.add_argument("--listen-port", type=int, help="UDP: also wait for the answer on this local port (e.g. 50000)")
    ap.add_argument("--serial", help="RS-232C device instead of the network, e.g. /dev/cu.usbserial-XXXX")
    ap.add_argument("--timeout", type=float, default=2.0, help="seconds to wait for each answer")
    args = ap.parse_args()

    if args.serial:
        ways = [("serial", lambda req: ask_serial(args.serial, req, args.timeout))]
        print(f"RS-232C {args.serial} 19200 bps")
    else:
        ways = []
        if args.proto in ("both", "tcp"):
            ways.append(("tcp", lambda req: ask_tcp(args.host, args.port, req, args.timeout)))
        if args.proto in ("both", "udp"):
            ways.append(("udp", lambda req: ask_udp(args.host, args.port, req, args.timeout, args.listen_port)))
        print(f"Lighting controller {args.host}:{args.port} (read only)")

    answered = False
    for way, ask in ways:
        print(f"\n[{way}]")
        for name, request in READS.items():
            try:
                data = ask(request)
            except OSError as e:
                print(f"  {name:6s} sent {hexs(request)}  ->  {type(e).__name__}: {e}")
                if way == "tcp":   # no TCP at all: the other request will fail the same way
                    break
                continue
            print(f"  {name:6s} sent {hexs(request)}  ->  received {hexs(data)}")
            print(f"         {describe(name, data)}")
            answered |= bool(data)
    if not answered:
        print("\nNo answer. Check: the cable and the LAN LINK lamp on the unit, that the Mac has an address in the unit's "
              "network (e.g. 192.168.0.10 for 192.168.0.45), the unit's IP (ALL key 3 s shows it), and try "
              "--proto udp --listen-port 50000.")
        sys.exit(1)


if __name__ == "__main__":
    main()
