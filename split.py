"""Split every MIDI channel into one track per instrument.

After this, each track uses exactly one SF2 preset for the whole song, so in
FL Studio every channel needs one fixed patch and nothing changes mid-song.

Usage:
    python split.py SONG.mid BANK.sf2 [-o SONG_split.mid]

What happens to each kind of event:
  - Notes go to the track of the instrument that was active when the note
    started (its note-off follows it, even if the instrument changes before
    the note ends).
  - Volume/pan/expression/pitch bend/other controllers are copied to every
    track of that channel, so each track keeps the right mix state.
  - Each track gets its own bank select + program change at the very start.
    Mid-song program changes are removed (they are now the track splits).
  - Tempo, time signature, markers and other meta events stay on the first
    track made from the original track. Track names are replaced with
    "Ch4 Instr 48"-style names.
"""
import argparse
import os
import re
import struct
from collections import OrderedDict

from analyze import parse_midi, parse_sf2_presets


def _varlen(n):
    out = [n & 0x7F]
    n >>= 7
    while n:
        out.append((n & 0x7F) | 0x80)
        n >>= 7
    return bytes(reversed(out))


def _encode_track(events):
    """events: list of (tick, order, bytes). Writes deltas, no running status."""
    out, last = bytearray(), 0
    for tick, _, raw in sorted(events, key=lambda e: (e[0], e[1])):
        out += _varlen(tick - last) + raw
        last = tick
    out += _varlen(0) + b"\xFF\x2F\x00"
    return b"MTrk" + struct.pack(">I", len(out)) + bytes(out)


def _meta(mtype, data):
    return bytes([0xFF, mtype]) + _varlen(len(data)) + data


def _chan_bytes(e):
    return bytes([e["status"] | e["ch"]]) + bytes(e["args"])


def split(midi, presets):
    out_tracks = []
    for t_idx, events in enumerate(midi["tracks"]):
        # Pass 1: find which instruments each channel uses, in order of first use.
        state = {}  # ch -> {"msb", "lsb", "prog"}
        groups = OrderedDict()  # (ch, bank, prog) -> list of events
        for e in events:
            if e["kind"] != "chan":
                continue
            s = state.setdefault(e["ch"], {"msb": 0, "lsb": 0, "prog": None})
            if e["status"] == 0xB0 and e["args"][0] == 0:
                s["msb"] = e["args"][1]
            elif e["status"] == 0xB0 and e["args"][0] == 32:
                s["lsb"] = e["args"][1]
            elif e["status"] == 0xC0:
                s["prog"] = e["args"][0]
            elif e["status"] == 0x90 and e["args"][1] > 0:
                key = (e["ch"], s["msb"] * 128 + s["lsb"], s["prog"])
                groups.setdefault(key, [])

        if not groups:
            # No notes: keep as-is (e.g. a pure tempo/conductor track).
            evs = [(e["tick"], i, _raw(e)) for i, e in enumerate(events) if _raw(e)]
            out_tracks.append((f"Track {t_idx}", evs))
            continue

        # Pass 2: route events.
        state = {}
        sounding = {}  # (ch, note) -> list of group keys (stack, for overlaps)
        first_key = next(iter(groups))
        for i, e in enumerate(events):
            order = i + 10  # leave room for the setup events at order 0..9
            if e["kind"] == "meta":
                if e["type"] in (0x03, 0x2F):  # old name / end of track
                    continue
                groups[first_key].append((e["tick"], order, _meta(e["type"], e["data"])))
                continue
            if e["kind"] == "sysex":
                for g in groups.values():
                    g.append((e["tick"], order, _raw(e)))
                continue
            ch, st, a = e["ch"], e["status"], e["args"]
            s = state.setdefault(ch, {"msb": 0, "lsb": 0, "prog": None})
            if st == 0xC0:
                s["prog"] = a[0]
                continue  # replaced by per-track program at tick 0
            if st == 0xB0 and a[0] in (0, 32):
                s["msb" if a[0] == 0 else "lsb"] = a[1]
                continue  # replaced by per-track bank select at tick 0
            if st == 0x90 and a[1] > 0:
                key = (ch, s["msb"] * 128 + s["lsb"], s["prog"])
                sounding.setdefault((ch, a[0]), []).append(key)
                groups[key].append((e["tick"], order, _chan_bytes(e)))
            elif st == 0x80 or (st == 0x90 and a[1] == 0):
                stack = sounding.get((ch, a[0]))
                key = stack.pop(0) if stack else (ch, s["msb"] * 128 + s["lsb"], s["prog"])
                if key in groups:
                    groups[key].append((e["tick"], order, _chan_bytes(e)))
            elif st == 0xA0:  # poly aftertouch follows the note
                stack = sounding.get((ch, a[0]))
                if stack:
                    groups[stack[0]].append((e["tick"], order, _chan_bytes(e)))
            else:  # controllers, pitch bend, channel pressure -> every split of this channel
                for key, g in groups.items():
                    if key[0] == ch:
                        g.append((e["tick"], order, _chan_bytes(e)))

        for (ch, bank, prog), evs in groups.items():
            name = f"Ch{ch + 1} " + (f"Instr {prog}" if prog is not None else "default")
            if bank:
                name += f" bank {bank}"
            setup = [(0, 0, _meta(0x03, name.encode("latin-1")))]
            if prog is not None:
                setup += [
                    (0, 1, bytes([0xB0 | ch, 0, bank // 128])),
                    (0, 2, bytes([0xB0 | ch, 32, bank % 128])),
                    (0, 3, bytes([0xC0 | ch, prog])),
                ]
            out_tracks.append((name, setup + evs))
    return out_tracks


def write_midi(path, tracks, division):
    with open(path, "wb") as f:
        f.write(b"MThd" + struct.pack(">IHHH", 6, 1, len(tracks), division))
        for _, evs in tracks:
            f.write(_encode_track(evs))


def track_instrument(name):
    """'Ch4 Instr 48 bank 1' -> (1, 48); None for tracks without notes."""
    m = re.match(r"Ch\d+ Instr (\d+)(?: bank (\d+))?$", name)
    return (int(m.group(2) or 0), int(m.group(1))) if m else None


def _raw(e):
    if e["kind"] == "meta":
        return None if e["type"] == 0x2F else _meta(e["type"], e["data"])
    if e["kind"] == "sysex":
        return b"\xF0" + _varlen(len(e["data"])) + e["data"]
    return _chan_bytes(e)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("midi")
    ap.add_argument("sf2")
    ap.add_argument("-o", "--out")
    args = ap.parse_args()
    out = args.out or os.path.splitext(args.midi)[0] + "_split.mid"

    midi = parse_midi(args.midi)
    presets = parse_sf2_presets(args.sf2)
    tracks = split(midi, presets)

    write_midi(out, tracks, midi["division"])

    print(f"Wrote {out}: {len(midi['tracks'])} tracks -> {len(tracks)} tracks\n")
    print("FL channel name      SF2 preset to load")
    print("-" * 50)
    for name, _ in tracks:
        inst = track_instrument(name)
        if inst is None:
            print(f"{name:<20} (no instrument)")
        else:
            print(f"{name:<20} {presets.get(inst, '?')}  (bank {inst[0]}, program {inst[1]})")


if __name__ == "__main__":
    main()
