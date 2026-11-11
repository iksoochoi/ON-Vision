"""The L-light LPW lighting controller over TCP: read and set the brightness (0..255) of its channels.

Manual "LPW-Specification & Protocol" v1.7, packet: 01 | OP (00 write, 01 read) | length | address | data | 04
    read  CSVR  01 01 00 21 04                      -> 01 01 10 21 v1 .. v16 04
    write CSVR  01 00 03 21 [CH16..9] [CH8..1] v 04 -> [CH16..9] [CH8..1] v 'O' 'K'   (bit = channel; NAK 15 = refused)
    save        01 00 01 2C 10 04                   -> 06   (SCR 0x2C bit 4 SAVE_FLG: the current values to the memory)
A value written over the network is NOT kept after a power off by itself (2026-11-11: the unit came back with the
older values after the Mac and the controller were restarted, although its auto-save should be on): save() after a
change. The memory takes a limited number of writes: save after a change, not in a loop.
One request at a time (a lock): the unit is small, and the page and backend.cli.light_probe may ask together."""

import errno
import socket
import sys
import threading
import time

from ..core import config as C

ACK, NAK = 0x06, 0x15
CSVR = 0x21
READ_VALUES = bytes([0x01, 0x01, 0x00, CSVR, 0x04])
SCR, SAVE_FLG = 0x2C, 0x10
SAVE = bytes([0x01, 0x00, 0x01, SCR, SAVE_FLG, 0x04])
TIMEOUT = 2.0

_lock = threading.Lock()


class LightError(Exception):
    pass


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


def complete(data):
    """An answer is whole: a packet end (04), a lone ACK / NAK, or the 'OK' of a value write."""
    return data.endswith(b"\x04") or data.endswith(b"OK") or data in (bytes([ACK]), bytes([NAK]))


def receive(s, timeout):
    """Bytes until the answer is complete, 0.2 s of silence after some bytes, or the timeout."""
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
        if complete(data):
            break
    return data


def write_packet(channels, value):
    """CSVR write: the channels (1..16) to the value (0..255)."""
    mask = 0
    for ch in channels:
        mask |= 1 << (ch - 1)
    return bytes([0x01, 0x00, 0x03, CSVR, (mask >> 8) & 0xFF, mask & 0xFF, value, 0x04])


def _address():
    """Where to connect: the relay on this Mac (ON_LIGHT_RELAY_PORT) or the controller itself."""
    return ("127.0.0.1", C.LIGHT_RELAY_PORT) if C.LIGHT_RELAY_PORT else (C.LIGHT_HOST, C.LIGHT_PORT)


def _ask(request, timeout=TIMEOUT):
    if not C.LIGHT_HOST:
        raise LightError("no lighting controller (ON_LIGHT_HOST is empty)")
    address = _address()
    with _lock:
        try:
            with socket.create_connection(address, timeout=timeout) as s:
                s.sendall(request)
                data = receive(s, timeout)
        except OSError as e:
            if C.LIGHT_RELAY_PORT:
                raise LightError(f"the light relay on 127.0.0.1:{C.LIGHT_RELAY_PORT} does not answer ({type(e).__name__}: {e}). "
                                 "Install / check it: ./bin/install_launchd.sh light-relay-install, light-relay-status") from e
            if sys.platform == "darwin" and e.errno == errno.EHOSTUNREACH:
                raise LightError(f"no route to {C.LIGHT_HOST} (Errno 65). If backend.cli.light_probe reaches it from the Terminal, "
                                 "macOS refuses this app the local network: ./bin/install_launchd.sh light-relay-install") from e
            raise LightError(f"no connection to {C.LIGHT_HOST}:{C.LIGHT_PORT} ({type(e).__name__}: {e}). "
                             "Check the cable and that the Mac has an address in the unit's network (e.g. 192.168.0.225)") from e
    if not data:
        where = f"the controller (through the relay on 127.0.0.1:{C.LIGHT_RELAY_PORT})" if C.LIGHT_RELAY_PORT else f"{C.LIGHT_HOST}:{C.LIGHT_PORT}"
        raise LightError(f"{where} did not answer"
                         + (": see the relay log /Library/Logs/on_light_relay.log" if C.LIGHT_RELAY_PORT else ""))
    if data == bytes([NAK]):
        raise LightError("the controller refused the request (NAK)")
    return data


def read_values():
    """The brightness of the 16 channels (a unit with fewer channels sends 0 for the others)."""
    data = _ask(READ_VALUES)
    for addr, body in parse(data):
        if addr == CSVR and body:
            return list(body)
    raise LightError(f"answer not understood: {hexs(data)}")


def set_value(channels, value):
    """Set the channels (1..16) to the brightness (0..255). Raises LightError when the unit does not confirm."""
    channels = sorted(set(int(c) for c in channels))
    if not channels or not all(1 <= c <= 16 for c in channels):
        raise LightError("channels: 1..16")
    if not 0 <= int(value) <= 255:
        raise LightError("value: 0..255")
    request = write_packet(channels, int(value))
    data = _ask(request)
    if not (data.endswith(b"OK") or data == bytes([ACK])):
        raise LightError(f"the controller did not confirm: sent {hexs(request)}, received {hexs(data)}")


def save():
    """Store the current values of all the channels in the unit's memory (kept after a power off). Raises LightError
    when the unit does not confirm."""
    data = _ask(SAVE)
    if not (data == bytes([ACK]) or data.endswith(b"OK")):
        raise LightError(f"the controller did not confirm the save: sent {hexs(SAVE)}, received {hexs(data)}")
