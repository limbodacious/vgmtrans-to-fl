"""Turn every MIDI Out channel in an FL project into sforzando with the right .sfz.

Usage:
    python build_flp.py PROJECT.flp [OUTPUT_FOLDER]

PROJECT.flp:   a project where you imported <song>_split.mid (MIDI Out, one channel
               per track) and saved. Channel names must still contain "Instr N".
OUTPUT_FOLDER: the convert.py output folder with the .sfz files. Default: the newest
               output/ folder for this song.

Writes PROJECT_sforzando.flp next to the original (the original is not touched).

sforzando's setup is copied from a "donor" channel. The first time, the project
must contain one channel you set to sforzando yourself; it is saved to
template/sforzando_donor.json and reused for every later project.
"""
import glob
import json
import os
import re
import struct
import sys
import zlib
from xml.sax.saxutils import quoteattr

import flp

HERE = os.path.dirname(os.path.abspath(__file__))
DONOR_PATH = os.path.join(HERE, "template", "sforzando_donor.json")
STATE_RECORD = 53      # wrapper record holding the VST3 state
COMPONENT_CHUNK = 3    # VST3 component state (sforzando's own settings)


# --------------------------------------------------- FL wrapper blob (event 213)

def wrapper_records(blob):
    """Fruity Wrapper data: u32 version, then records of (u32 id, u64 size, data)."""
    pos, recs = 4, []
    while pos + 12 <= len(blob):
        rid, size = struct.unpack("<IQ", blob[pos:pos + 12])
        recs.append([rid, blob[pos + 12:pos + 12 + size]])
        pos += 12 + size
    if pos != len(blob):
        raise ValueError("unexpected Fruity Wrapper layout")
    return blob[:4], recs


def wrapper_join(version, recs):
    return version + b"".join(struct.pack("<IQ", rid, len(d)) + d for rid, d in recs)


def vst3_chunks(state):
    """VST3 state inside record 53: header (u32 1, u32 1, u64 n, n bytes), then
    chunks of (u32 type, u64 size, data)."""
    hlen = struct.unpack("<Q", state[8:16])[0]
    head, pos, chunks = state[:16 + hlen], 16 + hlen, []
    while pos + 12 <= len(state):
        ctype, size = struct.unpack("<IQ", state[pos:pos + 12])
        chunks.append([ctype, state[pos + 12:pos + 12 + size]])
        pos += 12 + size
    if pos != len(state):
        raise ValueError("unexpected VST3 state layout")
    return head, chunks


def vst3_join(head, chunks):
    return head + b"".join(struct.pack("<IQ", t, len(d)) + d for t, d in chunks)


def read_sforzando_xml(component):
    """Component chunk: u32 0, u32 n, then n bytes of b'CEGP' + u32 xml_len + zlib(xml)."""
    n = struct.unpack("<I", component[4:8])[0]
    inner = component[8:8 + n]
    if inner[:4] != b"CEGP":
        raise ValueError("not a sforzando state")
    return component[:4], zlib.decompress(inner[8:]).decode("utf-8")


def write_sforzando_xml(prefix, xml):
    raw = xml.encode("utf-8")
    inner = b"CEGP" + struct.pack("<I", len(raw)) + zlib.compress(raw, 9)
    return prefix + struct.pack("<I", len(inner)) + inner


def set_sfz(blob213, sfz_path):
    """Return a copy of sforzando's wrapper blob that loads sfz_path."""
    version, recs = wrapper_records(blob213)
    rec = next(r for r in recs if r[0] == STATE_RECORD)
    head, chunks = vst3_chunks(rec[1])
    comp = next(c for c in chunks if c[0] == COMPONENT_CHUNK)
    prefix, xml = read_sforzando_xml(comp[1])
    new_name = quoteattr(sfz_path.replace("\\", "/"))
    xml, count = re.subn(r'(<Slot id="0" name=)("[^"]*"|\'[^\']*\')', lambda m: m.group(1) + new_name, xml, count=1)
    if count != 1:
        raise ValueError("sforzando state has no instrument slot")
    comp[1] = write_sforzando_xml(prefix, xml)
    rec[1] = vst3_join(head, chunks)
    return wrapper_join(version, recs)


def current_sfz(blob213):
    version, recs = wrapper_records(blob213)
    rec = next(r for r in recs if r[0] == STATE_RECORD)
    _, chunks = vst3_chunks(rec[1])
    _, xml = read_sforzando_xml(next(c for c in chunks if c[0] == COMPONENT_CHUNK)[1])
    m = re.search(r'<Slot id="0" name="([^"]*)"', xml)
    return m.group(1) if m else None


# ------------------------------------------------------------------ project edit

def channel_info(events, start, end):
    info = {}
    for i in range(start, end):
        eid, val = events[i]
        if eid in (flp.PLUGIN_INTERNAL, flp.PLUGIN_NAME):
            info[eid] = (i, flp.text(val))
        elif eid in (212, 213):
            info[eid] = (i, val)
    return info


def is_sforzando(events, info):
    if info.get(flp.PLUGIN_INTERNAL, (0, ""))[1] != "Fruity Wrapper" or 213 not in info:
        return False
    try:
        current_sfz(info[213][1])
        return True
    except (ValueError, StopIteration, zlib.error, struct.error):
        return False


class SetupMissing(Exception):
    """No sforzando channel in the project and no saved setup yet."""


def has_donor():
    return os.path.exists(DONOR_PATH)


def find_or_load_donor(events, blocks):
    for idx, (s, e) in blocks.items():
        info = channel_info(events, s, e)
        if is_sforzando(events, info):
            donor = {k: events[info[k][0]][1].hex() for k in (flp.PLUGIN_INTERNAL, 212, 213)}
            os.makedirs(os.path.dirname(DONOR_PATH), exist_ok=True)
            with open(DONOR_PATH, "w") as f:
                json.dump(donor, f)
            return {int(k): bytes.fromhex(v) for k, v in donor.items()}, f"channel {idx} of this project"
    if os.path.exists(DONOR_PATH):
        with open(DONOR_PATH) as f:
            return {int(k): bytes.fromhex(v) for k, v in json.load(f).items()}, "saved setup"
    raise SetupMissing("No sforzando setup found yet. In FL, set ONE channel to sforzando "
                       "(load any .sfz on it), save the project, and try again. "
                       "It's remembered after that.")


def _match(name, tracks):
    """Find the .sfz for an FL channel / pattern name using the manifest."""
    if name in tracks:
        return name
    # FL pattern names look like "<song> - <track name>"
    for t in sorted(tracks, key=len, reverse=True):
        if name.endswith(" - " + t) or name.endswith(t):
            return t
    return None


# One playlist clip in FL 21's format (60 bytes). The constant fields are copied
# from clips FL itself saved.
_CLIP = struct.Struct("<IHHIHH2sH4sii28s")
_PATTERN_BASE = 20480


def _pattern_lengths(header, events):
    """{pattern id: clip length in ticks}: last note end, rounded up to a whole bar
    (that's the length FL gives a pattern clip by default)."""
    ppq = struct.unpack("<HHH", header)[2]
    num = next((flp.as_int(v) for e, v in events if e == flp.TIME_SIG_NUM), 4) or 4
    beat = next((flp.as_int(v) for e, v in events if e == flp.TIME_SIG_BEAT), 4) or 4
    bar = ppq * 4 * num // beat
    lengths, current = {}, None
    for eid, val in events:
        if eid == flp.PATTERN_NEW:
            current = flp.as_int(val)
        elif eid == flp.PATTERN_NOTES and current is not None and val:
            end = max(pos + length for pos, length in
                      (struct.unpack_from("<I4xI", val, k) for k in range(0, len(val), 24)))
            lengths[current] = max(lengths.get(current, 0), -(-end // bar) * bar)
    return lengths


def fill_playlist(header, events):
    """If the playlist is empty, put every pattern on its own track at bar 1.
    Returns how many clips were placed (0 if the playlist already had clips)."""
    slot = next((i for i, (e, _) in enumerate(events) if e == flp.PLAYLIST), None)
    if slot is None or events[slot][1]:
        return 0
    lengths = _pattern_lengths(header, events)
    clips = b""
    for track, pid in enumerate(sorted(lengths)):
        clips += _CLIP.pack(0, _PATTERN_BASE, _PATTERN_BASE + pid, lengths[pid], 499 - track, 0,
                            b"\x78\x00", 0x8040, b"\x40\x64\x80\x80", -1, -1,
                            struct.pack("<I", track + 1) + bytes(24))
    events[slot][1] = clips
    return len(lengths)


def set_data_folder(events, out_path):
    """FL keeps an absolute path to the project's data folder (where it puts Backup/
    and recordings). Point it at a folder next to the new project, or FL asks where
    the folder went when the project is opened."""
    folder = os.path.splitext(os.path.abspath(out_path))[0]
    os.makedirs(folder, exist_ok=True)
    value = (folder.rstrip("\\/") + os.sep + chr(0)).encode("utf-16-le")
    for ev in events:
        if ev[0] == flp.DATA_PATH:
            ev[1] = value
            return


def drop_midi_only_events(events, channels):
    """Remove program change / modulation records of the given channel indexes from
    every pattern (what FL does when a channel's plugin is replaced)."""
    import flgen
    for ev in events:
        if ev[0] == 223 and ev[1]:
            recs = [flgen.CTRL.unpack_from(ev[1], k) for k in range(0, len(ev[1]), 12)]
            keep = [r for r in recs if not (r[3] in channels and (r[1], r[2]) in flgen.MIDI_ONLY)]
            if len(keep) != len(recs):
                ev[1] = flgen.encode_controllers(keep)


def build(src, out_path, tracks):
    """tracks: {FL channel name: .sfz path} from manifest.json.
    Returns a dict with the lines to show, counts and the output path."""
    header, events = flp.read(src)
    blocks = flp.channel_blocks(events)
    donor, donor_from = find_or_load_donor(events, blocks)
    pattern_names = [flp.text(v) for e, v in events if e == 193]

    lines, changed, skipped, switched = [], 0, [], set()
    for idx, (s, e) in sorted(blocks.items()):
        info = channel_info(events, s, e)
        kind = info.get(flp.PLUGIN_INTERNAL, (0, ""))[1]
        name = info.get(flp.PLUGIN_NAME, (0, ""))[1]
        if kind != "MIDI out":
            if is_sforzando(events, info):
                note = ""
                # FL renames a replaced channel to "sforzando"; restore it from its pattern
                if name not in tracks and idx < len(pattern_names):
                    t = _match(pattern_names[idx], tracks)
                    if t:
                        events[info[flp.PLUGIN_NAME][0]][1] = (t + chr(0)).encode("utf-16-le")
                        note, name = f" (renamed from '{name}')", t
                # Make sure it plays this channel's own instrument, whatever was dragged on
                if name in tracks:
                    want = tracks[name].replace("\\", "/")
                    if os.path.normcase(current_sfz(info[213][1]) or "") != os.path.normcase(want):
                        events[info[213][0]][1] = set_sfz(events[info[213][0]][1], tracks[name])
                        note += " (instrument corrected)"
                lines.append(f"{name}: already sforzando -> {os.path.basename(tracks.get(name, ''))}{note}")
            continue
        t = _match(name, tracks)
        if not t:
            skipped.append(name)
            continue
        events[info[flp.PLUGIN_INTERNAL][0]][1] = donor[flp.PLUGIN_INTERNAL]
        if 212 in info:
            events[info[212][0]][1] = donor[212]
        events[info[213][0]][1] = set_sfz(donor[213], tracks[t])
        switched.add(idx)
        changed += 1
        lines.append(f"{name} -> {os.path.basename(tracks[t])}")

    drop_midi_only_events(events, switched)
    set_data_folder(events, out_path)
    placed = fill_playlist(header, events)
    if placed:
        lines.append(f"Playlist: placed {placed} pattern(s), one per track, all starting at bar 1")
    flp.write(out_path, header, events)
    return {"out": out_path, "changed": changed, "skipped": skipped,
            "lines": lines, "donor_from": donor_from}


def main():
    if len(sys.argv) not in (2, 3):
        sys.exit(__doc__)
    src = sys.argv[1]
    folder = sys.argv[2] if len(sys.argv) == 3 else None
    if folder is None:
        _, events = flp.read(src)
        title = next((flp.text(v) for e, v in events if e == 231), "")
        song = re.sub(r"_split$", "", title)
        found = sorted(glob.glob(os.path.join(HERE, "output", f"{glob.escape(song)}*", "manifest.json")),
                       key=os.path.getmtime)
        if not found:
            sys.exit(f"Couldn't find an output folder for '{song}'. Pass it as the 2nd argument.")
        folder = os.path.dirname(found[-1])
    with open(os.path.join(folder, "manifest.json"), encoding="utf-8") as f:
        tracks = json.load(f)["tracks"]
    try:
        r = build(src, os.path.splitext(src)[0] + "_sforzando.flp", tracks)
    except SetupMissing as e:
        sys.exit(str(e))
    print(f"sfz folder: {folder}")
    print(f"sforzando setup from: {r['donor_from']}")
    print()
    for line in r["lines"]:
        print("  " + line)
    print()
    print(f"{r['changed']} channel(s) switched to sforzando -> {r['out']}")
    if r["skipped"]:
        print("Not changed (no matching .sfz):", ", ".join(r["skipped"]))


if __name__ == "__main__":
    main()
