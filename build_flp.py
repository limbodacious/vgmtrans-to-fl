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
            return {int(k): bytes.fromhex(v) for k, v in json.load(f).items()}, DONOR_PATH
    sys.exit("No sforzando setup found. Set ONE channel to sforzando (any .sfz) in FL, "
             "save, and run this again. It's remembered after that.")


def find_output_folder(events):
    title = next((flp.text(v) for e, v in events if e == 231), "")
    song = re.sub(r"_split$", "", title)
    folders = sorted(glob.glob(os.path.join(HERE, "output", f"{glob.escape(song)}_*")), key=os.path.getmtime)
    folders = [f for f in folders if glob.glob(os.path.join(f, "*.sfz"))]
    if not folders:
        sys.exit(f"Couldn't find an output folder for '{song}'. Pass it as the 2nd argument.")
    return folders[-1]


def find_sfz(folder, name):
    m = re.search(r"Instr (\d+)(?: bank (\d+))?", name)
    if not m:
        return None
    stem = f"Instr {m.group(1)}" + (f" bank {m.group(2)}" if m.group(2) else "")
    for f in sorted(glob.glob(os.path.join(folder, "*.sfz"))):
        base = os.path.splitext(os.path.basename(f))[0]
        if base == stem or base.startswith(stem + " - "):
            return os.path.abspath(f)
    return None


def main():
    if len(sys.argv) not in (2, 3):
        sys.exit(__doc__)
    src = sys.argv[1]
    header, events = flp.read(src)
    blocks = flp.channel_blocks(events)
    donor, donor_from = find_or_load_donor(events, blocks)
    folder = sys.argv[2] if len(sys.argv) == 3 else find_output_folder(events)

    # Pattern names ("<song> - Ch4 Instr 48 ...") follow channel order after
    # "Split by channel"; used to restore names FL replaced with "sforzando".
    pattern_names = [flp.text(v) for e, v in events if e == 193]

    print(f"sfz folder: {folder}")
    print(f"sforzando setup from: {donor_from}\n")
    changed, skipped = 0, []
    for idx, (s, e) in sorted(blocks.items()):
        info = channel_info(events, s, e)
        kind = info.get(flp.PLUGIN_INTERNAL, (0, ""))[1]
        name = info.get(flp.PLUGIN_NAME, (0, ""))[1]
        if kind != "MIDI out":
            if is_sforzando(events, info):
                note = ""
                if "Instr" not in name and idx < len(pattern_names) and "Instr" in pattern_names[idx]:
                    new = pattern_names[idx].split(" - ", 1)[-1]
                    events[info[flp.PLUGIN_NAME][0]][1] = (new + chr(0)).encode("utf-16-le")
                    note, name = f" (renamed from '{name}')", new
                print(f"  ch {idx:<3} {name:<34} already sforzando "
                      f"({os.path.basename(current_sfz(info[213][1]))}){note}")
            continue
        sfz = find_sfz(folder, name)
        if not sfz:
            skipped.append(name)
            continue
        events[info[flp.PLUGIN_INTERNAL][0]][1] = donor[flp.PLUGIN_INTERNAL]
        if 212 in info:
            events[info[212][0]][1] = donor[212]
        events[info[213][0]][1] = set_sfz(donor[213], sfz)
        changed += 1
        print(f"  ch {idx:<3} {name:<34} -> {os.path.basename(sfz)}")

    out = os.path.splitext(src)[0] + "_sforzando.flp"
    flp.write(out, header, events)
    print(f"\n{changed} channel(s) switched to sforzando -> {out}")
    if skipped:
        print("Not changed (no matching .sfz):", ", ".join(skipped))


if __name__ == "__main__":
    main()
