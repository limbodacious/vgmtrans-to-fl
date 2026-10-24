"""Turn a split MIDI into FL Studio pattern data, the same way FL's own MIDI import does.

Each MIDI track becomes one pattern on its own channel (what FL gives you after
"Import MIDI" + "Split by channel"). Checked byte for byte against FL Studio 21's import.

Note record (24 bytes):
    position, flags (0x4000), channel index, length, key, group, fine pitch (120),
    0, release (64), MIDI channel, pan (64), velocity, mod X (128), mod Y (128)
Controller record (12 bytes):
    position, kind, flags, channel index, value
        program change   kind 0, flags 0x80, value = program + 1
        volume (CC7)     kind 0, flags 0,    value = CC value * 100
        pan (CC10)       kind 1, flags 0,    value = CC value * 100
        modulation (CC1) kind 1, flags 0x80, value = CC value
        pitch bend       kind 4, flags 0,    value = bend in cents (uses the RPN bend range)
        tempo            kind 5, flags 0,    channel 0x4000, value = BPM * 1000
"""
import struct

FL_PPQ = 96
NOTE = struct.Struct("<IHHIHHBBBBBBBB")
CTRL = struct.Struct("<IBBHi")
TEMPO_CHANNEL = 0x4000


def track_notes(events, scale):
    """[(start, length, key, velocity, midi channel)] in FL ticks, in note-on order."""
    sounding, notes = {}, []
    for e in events:
        if e["kind"] != "chan":
            continue
        st, a = e["status"], e["args"]
        if st == 0x90 and a[1] > 0:
            sounding.setdefault((e["ch"], a[0]), []).append((len(notes), e["tick"]))
            notes.append([e["tick"] * scale, 0, a[0], a[1], e["ch"]])
        elif st == 0x80 or (st == 0x90 and a[1] == 0):
            stack = sounding.get((e["ch"], a[0]))
            if stack:
                i, t0 = stack.pop(0)
                notes[i][1] = (e["tick"] - t0) * scale
    return [tuple(n) for n in notes]


def encode_notes(notes, rack):
    out = b""
    for pos, length, key, vel, mch in sorted(notes, key=lambda n: n[0]):
        out += NOTE.pack(pos, 0x4000, rack, length, key, 0, 120, 0, 64, mch, 64, vel, 128, 128)
    return out


def track_controllers(events, scale, rack):
    """Controller records in FL's order, from one MIDI track."""
    recs = []
    rpn = {}            # midi channel -> [msb, lsb]
    bend_range = {}     # midi channel -> semitones
    last_tempo = None
    for e in events:
        pos = e["tick"] * scale
        if e["kind"] == "meta" and e["type"] == 0x51:
            bpm1000 = round(60_000_000 / int.from_bytes(e["data"], "big") * 1000)
            if bpm1000 != last_tempo:
                recs.append((pos, 5, 0, TEMPO_CHANNEL, bpm1000))
                last_tempo = bpm1000
            continue
        if e["kind"] != "chan":
            continue
        st, a, ch = e["status"], e["args"], e["ch"]
        if st == 0xC0:
            recs.append((pos, 0, 0x80, rack, a[0] + 1))
        elif st == 0xB0:
            cc, val = a
            if cc == 7:
                recs.append((pos, 0, 0, rack, val * 100))
            elif cc == 10:
                recs.append((pos, 1, 0, rack, val * 100))
            elif cc == 1:
                recs.append((pos, 1, 0x80, rack, val))
            elif cc == 101:
                rpn.setdefault(ch, [127, 127])[0] = val
            elif cc == 100:
                rpn.setdefault(ch, [127, 127])[1] = val
            elif cc == 6 and rpn.get(ch) == [0, 0]:
                bend_range[ch] = val
        elif st == 0xE0:
            bend = (a[0] | (a[1] << 7)) - 8192
            cents = bend / 8192 * bend_range.get(ch, 2) * 100
            recs.append((pos, 4, 0, rack, round(cents)))
    # FL keeps tempo changes ahead of other controllers at the same moment...
    recs.sort(key=lambda r: (r[0], r[1] != 5))
    # ...and drops events that repeat the value already in effect.
    current, kept = {}, []
    for r in recs:
        kind = (r[1], r[2], r[3])
        if current.get(kind) != r[4]:
            kept.append(r)
            current[kind] = r[4]
    return kept


def encode_controllers(recs):
    return b"".join(CTRL.pack(*r) for r in recs)


# ---------------------------------------------------------------- whole project

def _u16(n):
    return struct.pack("<H", n)


def _u32(n):
    return struct.pack("<I", n)


def _text(s):
    return (s + chr(0)).encode("utf-16-le")


def channel_ranges(events):
    """{channel index: (start, end)} of each channel's own events (without the pattern
    names FL stores after the first channel, and without the arrangement after the last)."""
    ids = [e for e, _ in events]
    starts = [i for i, e in enumerate(ids) if e == 64]
    arrangement = ids.index(99) if 99 in ids else len(ids)
    ranges = {}
    for k, s in enumerate(starts):
        end = starts[k + 1] if k + 1 < len(starts) else arrangement
        end = next((i for i in range(s + 1, end) if ids[i] == 65), end)
        ranges[int.from_bytes(events[s][1], "little")] = (s, end)
    return ranges


def split_skeleton(events):
    """Cut an FL-saved MIDI-import project into the parts the generator reuses."""
    ids = [e for e, _ in events]
    first_pat = ids.index(65)
    first_chan = ids.index(64)
    meta_start = next(i for i in range(first_chan, len(ids)) if ids[i] == 65)
    meta_end = next(i for i in range(meta_start + 1, len(ids)) if ids[i] in (65, 64, 99))
    arrangement = ids.index(99)
    # Use a channel FL created itself (still "MIDI out"): a channel whose plugin was
    # replaced by hand has some settings reset by FL.
    template = None
    for s, e in sorted(channel_ranges(events).values()):
        if any(ev[0] == 201 and ev[1].decode("utf-16-le", "ignore").rstrip(chr(0)) == "MIDI out"
               for ev in events[s:e]):
            template = events[s:e]
            break
    return {
        "header": events[:first_pat],
        # events between the pattern data and the first channel that aren't pattern data
        "after_patterns": [ev for ev in events[first_pat:first_chan] if ev[0] not in (65, 223, 224)],
        "channel": template or events[first_chan:meta_start],
        "pattern_meta": events[meta_start:meta_end],
        "tail": events[arrangement:],
    }


# Program change and modulation only mean something to a MIDI Out channel. When a
# channel is switched to a plugin (sforzando), FL deletes them from its patterns.
MIDI_ONLY = {(0, 0x80), (1, 0x80)}


def drop_midi_only(recs):
    return [r for r in recs if (r[1], r[2]) not in MIDI_ONLY]


def _channel_block(template, idx, name, plugin, ctrl):
    """One channel: the template channel with this channel's number, name, plugin
    and starting pan/volume/pitch (FL takes those from the first values at bar 1)."""
    start = {}
    for pos, kind, flags, _, value in ctrl:
        if pos == 0 and (kind, flags) not in start:
            start[(kind, flags)] = value
    block = []
    for eid, val in template:
        if eid == 64:
            val = _u16(idx)
        elif eid == 203:
            val = _text(name)
        elif eid in plugin:
            val = plugin[eid]
        elif eid == 132:
            val = _u16(idx + 1) + _u16(idx + 1)
        elif eid == 215:
            val = val[:156] + bytes([0 if idx == 0 else 1]) + val[157:]
        elif eid == 219:
            val = (_u32(start.get((1, 0), 6400)) + _u32(start.get((0, 0), 10000))
                   + struct.pack("<i", start.get((4, 0), 0)) + val[12:])
        block.append([eid, val])
    return block


def build_project(skeleton_path, midi, channels, title, data_folder, plugin_for):
    """Write a whole FL project.

    midi:        parsed split MIDI (analyze.parse_midi)
    channels:    [(midi track index, FL channel name, .sfz path)] in channel order
    plugin_for:  function(sfz path) -> {event id: bytes} for the channel's plugin events
    Returns (header, events)."""
    header, events = flp_read(skeleton_path)
    sk = split_skeleton(events)
    scale = FL_PPQ // midi["division"]
    if FL_PPQ % midi["division"]:
        raise ValueError(f"MIDI resolution {midi['division']} doesn't divide FL's {FL_PPQ}")

    # Tempo/meta-only tracks (no notes) don't get a channel; their tempo goes to pattern 1.
    note_tracks = {t for t, _, _ in channels}
    extra_tempo = []
    for i, tr in enumerate(midi["tracks"]):
        if i not in note_tracks:
            extra_tempo += [r for r in track_controllers(tr, scale, 0) if r[1] == 5]

    patterns, ctrl_by_chan = [], []
    for idx, (t, _, _) in enumerate(channels):
        ctrl = drop_midi_only(track_controllers(midi["tracks"][t], scale, idx))
        if idx == 0 and extra_tempo:
            ctrl = sorted(extra_tempo + ctrl, key=lambda r: (r[0], r[1] != 5))
        ctrl_by_chan.append(ctrl)
        patterns.append([65, _u16(idx + 1)])
        if ctrl:
            patterns.append([223, encode_controllers(ctrl)])
        notes = encode_notes(track_notes(midi["tracks"][t], scale), idx)
        if notes:
            patterns.append([224, notes])

    blocks = [_channel_block(sk["channel"], idx, name, plugin_for(sfz), ctrl_by_chan[idx])
              for idx, (_, name, sfz) in enumerate(channels)]
    metas = []
    for idx, (_, name, _) in enumerate(channels):
        for eid, val in sk["pattern_meta"]:
            if eid == 65:
                val = _u16(idx + 1)
            elif eid == 193:
                val = _text(f"{title} - {name}")
            metas.append([eid, val])

    first_tempo = next((r[4] for c in ctrl_by_chan for r in c if r[1] == 5 and r[0] == 0), None)
    num, den = 4, 4
    for tr in midi["tracks"]:
        for e in tr:
            if e["kind"] == "meta" and e["type"] == 0x58 and e["tick"] == 0:
                num, den = e["data"][0], 2 ** e["data"][1]
    head = []
    for eid, val in sk["header"]:
        if eid == 156 and first_tempo:
            val = _u32(first_tempo)
        elif eid == 17:
            val = bytes([num])
        elif eid == 18:
            val = bytes([den])
        elif eid == 231:
            val = _text(title)
        elif eid == 202:
            val = _text(data_folder.rstrip("/" + chr(92)) + chr(92))
        head.append([eid, val])

    tail = [[e, (b"" if e == 233 else v)] for e, v in sk["tail"]]
    out = head + patterns + [list(x) for x in sk["after_patterns"]]
    out += blocks[0] + metas
    for b in blocks[1:]:
        out += b
    out += tail
    return header, out


def flp_read(path):
    import flp
    return flp.read(path)
