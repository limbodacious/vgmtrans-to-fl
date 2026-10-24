"""Minimal FL Studio project (.flp) reader/writer.

An .flp is a header chunk (FLhd) and a data chunk (FLdt) holding a flat list of
events. Each event is (id, value):
    id   0..63   1-byte value
    id  64..127  2-byte value
    id 128..191  4-byte value
    id 192..255  variable-length data (length is a 7-bit varint)
"""
import struct

# Event ids we care about (names follow PyFLP's conventions)
CHAN_NEW = 64          # word: starts a channel's block, value = channel index
CHAN_TYPE = 21         # byte
PATTERN_NEW = 65       # word
PLUGIN_INTERNAL = 201  # text: internal plugin name ("Fruity Wrapper", "MIDI Out", ...)
PLUGIN_NAME = 203      # text: channel / plugin display name
PLUGIN_DATA = 212      # data: plugin state (for VSTs: the wrapper blob)
PATTERN_NOTES = 224    # data: a pattern's notes, 24 bytes each
DATA_PATH = 202        # text: absolute path of the project's data folder
PLAYLIST = 233         # data: playlist clips of the current arrangement
TIME_SIG_NUM = 17      # byte
TIME_SIG_BEAT = 18     # byte
TEXT_IDS = {192, 193, 194, 195, 196, 197, 199, 200, 201, 202, 203, 204, 206, 231, 239, 241}


def _varint(n):
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        out.append(b | (0x80 if n else 0))
        if not n:
            return bytes(out)


def read(path):
    data = open(path, "rb").read()
    if data[:4] != b"FLhd":
        raise ValueError("not an FL Studio project")
    hlen = struct.unpack("<I", data[4:8])[0]
    header = data[8:8 + hlen]
    pos = 8 + hlen
    if data[pos:pos + 4] != b"FLdt":
        raise ValueError("missing FLdt chunk")
    end = pos + 8 + struct.unpack("<I", data[pos + 4:pos + 8])[0]
    pos += 8
    events = []
    while pos < end:
        eid = data[pos]
        pos += 1
        if eid < 64:
            val, pos = data[pos:pos + 1], pos + 1
        elif eid < 128:
            val, pos = data[pos:pos + 2], pos + 2
        elif eid < 192:
            val, pos = data[pos:pos + 4], pos + 4
        else:
            n, shift = 0, 0
            while True:
                b = data[pos]
                pos += 1
                n |= (b & 0x7F) << shift
                shift += 7
                if not b & 0x80:
                    break
            val, pos = data[pos:pos + n], pos + n
        events.append([eid, val])
    return header, events


def write(path, header, events):
    body = bytearray()
    for eid, val in events:
        body.append(eid)
        if eid >= 192:
            body += _varint(len(val))
        body += val
    with open(path, "wb") as f:
        f.write(b"FLhd" + struct.pack("<I", len(header)) + header)
        f.write(b"FLdt" + struct.pack("<I", len(body)) + body)


def text(val):
    """Decode an FL text event (UTF-16LE in FL 11.5+, older ones are ASCII)."""
    try:
        if len(val) >= 2 and val[1:2] == b"\0":
            return val.decode("utf-16-le").rstrip("\0")
        return val.decode("latin-1").rstrip("\0")
    except UnicodeDecodeError:
        return repr(val[:40])


def as_int(val):
    return int.from_bytes(val, "little")


def channel_blocks(events):
    """{channel index: (first event index, end event index)}. A channel's block runs
    from its CHAN_NEW event up to the next CHAN_NEW / PATTERN_NEW / non-channel section."""
    starts = [(i, as_int(v)) for i, (e, v) in enumerate(events) if e == CHAN_NEW]
    blocks = {}
    for k, (i, idx) in enumerate(starts):
        end = starts[k + 1][0] if k + 1 < len(starts) else len(events)
        blocks[idx] = (i, end)
    return blocks
