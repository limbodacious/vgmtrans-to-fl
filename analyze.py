"""Show which SF2 instrument every MIDI track uses, and when it switches.

Usage:
    python analyze.py SONG.mid BANK.sf2 [--json out.json]

No extra packages needed (standard library only).
"""
import argparse
import json
import struct
import sys
from collections import defaultdict


# ---------------------------------------------------------------- MIDI parsing

def _read_varlen(data, pos):
    value = 0
    while True:
        b = data[pos]
        pos += 1
        value = (value << 7) | (b & 0x7F)
        if not b & 0x80:
            return value, pos


def parse_midi(path):
    data = open(path, "rb").read()
    if data[:4] != b"MThd":
        sys.exit(f"{path} is not a MIDI file")
    hlen = struct.unpack(">I", data[4:8])[0]
    fmt, ntracks, division = struct.unpack(">HHH", data[8:14])
    if division & 0x8000:
        sys.exit("SMPTE time division is not supported")
    pos = 8 + hlen
    tracks = []
    for _ in range(ntracks):
        if data[pos:pos + 4] != b"MTrk":
            sys.exit("Broken MIDI: expected MTrk chunk")
        tlen = struct.unpack(">I", data[pos + 4:pos + 8])[0]
        tracks.append(_parse_track(data[pos + 8:pos + 8 + tlen]))
        pos += 8 + tlen
    return {"format": fmt, "division": division, "tracks": tracks}


def _parse_track(data):
    events, pos, tick, status = [], 0, 0, None
    while pos < len(data):
        delta, pos = _read_varlen(data, pos)
        tick += delta
        b = data[pos]
        if b == 0xFF:  # meta
            mtype = data[pos + 1]
            length, pos = _read_varlen(data, pos + 2)
            events.append({"tick": tick, "kind": "meta", "type": mtype, "data": data[pos:pos + length]})
            pos += length
            continue
        if b in (0xF0, 0xF7):  # sysex
            length, pos = _read_varlen(data, pos + 1)
            events.append({"tick": tick, "kind": "sysex", "data": data[pos:pos + length]})
            pos += length
            continue
        if b & 0x80:
            status = b
            pos += 1
        if status is None:
            raise ValueError("Running status without a previous status byte")
        hi, ch = status & 0xF0, status & 0x0F
        nbytes = 1 if hi in (0xC0, 0xD0) else 2
        args = data[pos:pos + nbytes]
        pos += nbytes
        events.append({"tick": tick, "kind": "chan", "status": hi, "ch": ch, "args": tuple(args)})
    return events


# ----------------------------------------------------------------- SF2 parsing

def parse_sf2_presets(path):
    """Return {(bank, program): preset name}."""
    data = open(path, "rb").read()
    i = data.find(b"phdr")
    if i < 0:
        sys.exit(f"{path}: no preset list found (not a SoundFont?)")
    size = struct.unpack("<I", data[i + 4:i + 8])[0]
    presets = {}
    for k in range(size // 38 - 1):  # last record is the terminal 'EOP'
        rec = data[i + 8 + k * 38: i + 8 + (k + 1) * 38]
        name = rec[:20].split(b"\0")[0].decode("latin-1")
        prog, bank = struct.unpack("<HH", rec[20:24])
        presets[(bank, prog)] = name
    return presets


# --------------------------------------------------------------------- helpers

class Clock:
    """Converts ticks to bar:beat and seconds using the song's tempo/meter."""

    def __init__(self, midi):
        self.div = midi["division"]
        self.tempos, self.meters = [], []
        for tr in midi["tracks"]:
            for e in tr:
                if e["kind"] == "meta" and e["type"] == 0x51:
                    self.tempos.append((e["tick"], int.from_bytes(e["data"], "big")))
                elif e["kind"] == "meta" and e["type"] == 0x58:
                    self.meters.append((e["tick"], e["data"][0], 2 ** e["data"][1]))
        self.tempos.sort()
        self.meters.sort()
        if not self.tempos or self.tempos[0][0] != 0:
            self.tempos.insert(0, (0, 500000))
        if not self.meters or self.meters[0][0] != 0:
            self.meters.insert(0, (0, 4, 4))

    def seconds(self, tick):
        sec, last_tick, uspq = 0.0, 0, self.tempos[0][1]
        for t, tempo in self.tempos:
            if t > tick:
                break
            sec += (t - last_tick) * uspq / 1e6 / self.div
            last_tick, uspq = t, tempo
        return sec + (tick - last_tick) * uspq / 1e6 / self.div

    def bar_beat(self, tick):
        bar, start = 1, 0
        meters = self.meters + [(float("inf"), 0, 0)]
        for (t, num, den), (nxt, _, _) in zip(meters, meters[1:]):
            beat_ticks = self.div * 4 // den
            bar_ticks = beat_ticks * num
            if tick < nxt:
                rel = tick - t
                bar += rel // bar_ticks
                beat = (rel % bar_ticks) / beat_ticks + 1
                return f"{bar}:{beat:g}"
            bar += (nxt - t) // bar_ticks
        return "?"

    def stamp(self, tick):
        s = self.seconds(tick)
        return f"bar {self.bar_beat(tick):<7} {int(s // 60)}:{s % 60:05.2f}"


def analyze(midi, presets):
    clock = Clock(midi)
    report = []
    for idx, events in enumerate(midi["tracks"]):
        name = ""
        for e in events:
            if e["kind"] == "meta" and e["type"] == 0x03:
                name = e["data"].decode("latin-1")
                break
        chans = defaultdict(lambda: {
            "notes": 0, "bank_msb": 0, "bank_lsb": 0, "rpn": [127, 127],
            "bend_range": None, "bend_min": 0, "bend_max": 0,
            "programs": [], "first_note_tick": None, "lowest": 127, "highest": 0,
        })
        for e in events:
            if e["kind"] != "chan":
                continue
            c = chans[e["ch"]]
            st, a = e["status"], e["args"]
            if st == 0x90 and a[1] > 0:
                c["notes"] += 1
                c["lowest"] = min(c["lowest"], a[0])
                c["highest"] = max(c["highest"], a[0])
                if c["first_note_tick"] is None:
                    c["first_note_tick"] = e["tick"]
                if c["programs"]:
                    c["programs"][-1]["notes"] += 1
            elif st == 0xB0:
                cc, val = a
                if cc == 0:
                    c["bank_msb"] = val
                elif cc == 32:
                    c["bank_lsb"] = val
                elif cc == 101:
                    c["rpn"][0] = val
                elif cc == 100:
                    c["rpn"][1] = val
                elif cc == 6 and c["rpn"] == [0, 0]:
                    c["bend_range"] = val
            elif st == 0xC0:
                bank = c["bank_msb"] * 128 + c["bank_lsb"]
                prog = a[0]
                preset = presets.get((bank, prog)) or presets.get((c["bank_msb"], prog))
                c["programs"].append({
                    "tick": e["tick"], "when": clock.stamp(e["tick"]),
                    "bank": bank, "program": prog,
                    "preset": preset, "missing": preset is None, "notes": 0,
                })
            elif st == 0xE0:
                bend = (a[0] | (a[1] << 7)) - 8192
                c["bend_min"] = min(c["bend_min"], bend)
                c["bend_max"] = max(c["bend_max"], bend)
        for ch, c in chans.items():
            c["notes_before_first_program"] = (
                c["first_note_tick"] is not None
                and (not c["programs"] or c["programs"][0]["tick"] > c["first_note_tick"])
            )
        report.append({"track": idx, "name": name, "channels": dict(chans)})
    return report


NOTE_NAMES = "C C# D D# E F F# G G# A A# B".split()


def note_name(n):
    return f"{NOTE_NAMES[n % 12]}{n // 12 - 1}"


def print_report(report, presets, midi_path, sf2_path):
    print(f"MIDI: {midi_path}")
    print(f"SF2:  {sf2_path}  ({len(presets)} presets)\n")
    used, switching, problems = set(), [], []
    for tr in report:
        if not tr["channels"]:
            continue
        label = f"Track {tr['track']}" + (f" '{tr['name']}'" if tr["name"] else "")
        for ch, c in sorted(tr["channels"].items()):
            if c["notes"] == 0 and not c["programs"]:
                continue
            head = f"{label}  MIDI ch {ch + 1}  ({c['notes']} notes"
            if c["notes"]:
                head += f", {note_name(c['lowest'])}-{note_name(c['highest'])}"
            head += ")"
            print(head)
            # Only list program changes that differ from the one before them.
            real, last = [], None
            for p in c["programs"]:
                key = (p["bank"], p["program"])
                if key != last:
                    real.append(dict(p))
                    last = key
                else:
                    real[-1]["notes"] += p["notes"]
            for p in real:
                used.add((p["bank"], p["program"]))
                name = p["preset"] or "!! NOT IN SF2 !!"
                print(f"   {p['when']}  ->  bank {p['bank']} prog {p['program']:<3} {name:<22} ({p['notes']} notes)")
                if p["missing"]:
                    problems.append(f"{label} ch {ch + 1}: bank {p['bank']} prog {p['program']} is missing from the SF2")
            if len(real) > 1:
                distinct = len({(p["bank"], p["program"]) for p in real})
                switching.append(f"{label} ch {ch + 1}: {len(real) - 1} switches between {distinct} instruments")
            if not real and c["notes"]:
                print("   (no program change - player default, usually prog 0)")
            if c["notes_before_first_program"] and real:
                problems.append(f"{label} ch {ch + 1}: notes play before the first program change")
            if c["bend_min"] or c["bend_max"]:
                rng = c["bend_range"]
                rng_txt = f"{rng} semitones" if rng is not None else "not set (players assume 2)"
                print(f"   pitch bend used ({c['bend_min']}..{c['bend_max']}), range {rng_txt}")
            print()
    print("=" * 70)
    print(f"Distinct instruments used: {len(used)}")
    print("  " + ", ".join(f"{presets.get(k, '?')}" for k in sorted(used)))
    print(f"\nChannels that switch instruments mid-song: {len(switching)}")
    for s in switching:
        print("  " + s)
    print(f"\nProblems: {len(problems)}")
    for p in problems:
        print("  " + p)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("midi")
    ap.add_argument("sf2")
    ap.add_argument("--json", help="also write the full report as JSON")
    args = ap.parse_args()
    midi = parse_midi(args.midi)
    presets = parse_sf2_presets(args.sf2)
    report = analyze(midi, presets)
    print_report(report, presets, args.midi, args.sf2)
    if args.json:
        with open(args.json, "w") as f:
            json.dump(report, f, indent=2, default=str)
        print(f"\nJSON written to {args.json}")


if __name__ == "__main__":
    main()
