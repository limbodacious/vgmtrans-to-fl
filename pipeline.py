"""The steps shared by the UI (app.py) and the command line (convert.py).

    prepare(midi, sf2, out_dir)   analysis + previews + remembered names -> state.json
    finalize(out_dir, names)      named split MIDI + one SFZ per instrument + manifest.json
    build(out_dir, flp)           FL project with every channel set to sforzando
"""
import contextlib
import glob
import io
import json
import os
import re
import shutil
from datetime import datetime

import names as name_memory
from analyze import analyze, parse_midi, parse_sf2_presets, print_report
from previews import render_previews
from sf2sfz import convert as sf2_to_sfz, load_sf2
from split import split, write_midi

HERE = os.path.dirname(os.path.abspath(__file__))
OUTPUT_ROOT = os.path.join(HERE, "output")
RIPS_DIR = os.path.join(HERE, "rips")
INPUT_DIR = os.path.join(HERE, "input")


def find_inputs():
    """FL projects in the input folder and its subfolders (FL often saves a project in
    its own folder). Skips FL's Backup folders, autosaves and the app's own output."""
    found = []
    if not os.path.isdir(INPUT_DIR):
        return found
    for root, dirs, files in os.walk(INPUT_DIR):
        dirs[:] = [d for d in dirs if d.lower() != "backup"]
        for f in files:
            low = f.lower()
            if low.endswith(".flp") and "(autosaved" not in low and not low.endswith("_sforzando.flp"):
                found.append(os.path.join(root, f))
    return found


def newest_input(song=None):
    """The project to build from: the newest one named after the song if there is
    one, otherwise the newest project in the input folder. None if there are none."""
    flps = find_inputs()
    if song:
        named = [f for f in flps if os.path.splitext(os.path.basename(f))[0].lower() == song.lower()]
        flps = named or flps
    return max(flps, key=os.path.getmtime) if flps else None


def find_rips(folder=None):
    """Songs in the rips folder (and its subfolders), newest first.

    VGMTrans names the MIDI after the song and the SF2 after its bank, so they don't
    share a name. Each MIDI is paired with the .sf2 in the same folder whose file time
    is closest (they're written together by "Export as MIDI and SF2")."""
    folder = folder or RIPS_DIR
    mids, sf2s = [], []
    for root, _, files in os.walk(folder):
        for f in files:
            path = os.path.join(root, f)
            low = f.lower()
            if low.endswith((".mid", ".midi")) and not low.endswith("_split.mid"):
                mids.append(path)
            elif low.endswith(".sf2"):
                sf2s.append(path)
    songs = []
    for m in mids:
        mt = os.path.getmtime(m)
        same_dir = [s for s in sf2s if os.path.dirname(s) == os.path.dirname(m)]
        options = sorted(same_dir or sf2s, key=lambda s: abs(os.path.getmtime(s) - mt))
        songs.append({
            "midi": m, "song": os.path.splitext(os.path.basename(m))[0],
            "sf2": options[0] if options else None,
            "sf2_options": options + [s for s in sf2s if s not in options],
            "mtime": mt, "when": datetime.fromtimestamp(mt).strftime("%b %d %H:%M"),
        })
    return sorted(songs, key=lambda r: r["mtime"], reverse=True)


def new_run_dir(song):
    safe = re.sub(r"[^\w.-]+", "_", song).strip("_") or "song"
    path = os.path.join(OUTPUT_ROOT, f"{safe}_{datetime.now():%Y-%m-%d_%H%M%S}")
    os.makedirs(path)
    return path


def inst_key(bank, prog):
    return f"{bank}:{prog}"


def parse_key(key):
    bank, prog = key.split(":")
    return int(bank), int(prog)


def preview_stem(bank, prog):
    return f"Instr {prog}" + (f" bank {bank}" if bank else "")


def sfz_stem(bank, prog, name):
    return preview_stem(bank, prog) + (f" - {name}" if name else "")


def _state_path(out_dir):
    return os.path.join(out_dir, "state.json")


def load_state(out_dir):
    with open(_state_path(out_dir), encoding="utf-8") as f:
        return json.load(f)


def _save_state(out_dir, state):
    with open(_state_path(out_dir), "w", encoding="utf-8") as f:
        json.dump(state, f, indent=1, ensure_ascii=False)


def prepare(midi_path, sf2_path, out_dir, log=print):
    midi = parse_midi(midi_path)
    presets = parse_sf2_presets(sf2_path)
    report = analyze(midi, presets)

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        print_report(report, presets, midi_path, sf2_path)
    with open(os.path.join(out_dir, "analysis.txt"), "w", encoding="utf-8") as f:
        f.write(buf.getvalue())

    # Which instruments are used, on which channels, how many notes
    usage = {}
    for tr in report:
        for ch, c in tr["channels"].items():
            for p in c["programs"]:
                u = usage.setdefault((p["bank"], p["program"]), {"channels": set(), "notes": 0})
                if p["notes"]:
                    u["channels"].add(ch + 1)
                u["notes"] += p["notes"]
    used = {inst for inst, u in usage.items() if u["notes"]}

    sf = load_sf2(sf2_path)
    by_inst = {(p["bank"], p["program"]): p for p in sf["presets"]}
    mem = name_memory.load()

    log("Rendering previews...")
    render_previews(sf2_path, midi, used, out_dir, preview_stem)

    instruments = []
    for bank, prog in sorted(used, key=lambda k: (k[0], k[1])):
        preset = by_inst.get((bank, prog))
        fp = name_memory.fingerprint(sf, preset) if preset else None
        remembered = name_memory.lookup(mem, fp) if fp else None
        instruments.append({
            "key": inst_key(bank, prog), "bank": bank, "program": prog,
            "sf2_name": presets.get((bank, prog)), "missing": preset is None,
            "fingerprint": fp, "remembered": remembered,
            "channels": sorted(usage[(bank, prog)]["channels"]),
            "notes": usage[(bank, prog)]["notes"],
            "preview": f"previews/{preview_stem(bank, prog)}.wav",
        })

    state = {
        "song": os.path.splitext(os.path.basename(midi_path))[0],
        "midi": os.path.abspath(midi_path), "sf2": os.path.abspath(sf2_path),
        "instruments": instruments, "names": {}, "finalized": False,
    }
    _save_state(out_dir, state)
    return state


def finalize(out_dir, names):
    """names: {"bank:prog": "Strings"} (empty/missing = keep the number)."""
    state = load_state(out_dir)
    names = {parse_key(k): name_memory.clean_name(v) for k, v in names.items()}
    names = {k: v for k, v in names.items() if v}

    # Clear files from an earlier finalize of this run
    for f in glob.glob(os.path.join(out_dir, "*.sfz")) + glob.glob(os.path.join(out_dir, "*_split.mid")):
        os.remove(f)
    shutil.rmtree(os.path.join(out_dir, "samples"), ignore_errors=True)

    midi = parse_midi(state["midi"])
    presets = parse_sf2_presets(state["sf2"])
    tracks = split(midi, presets, names)
    split_path = os.path.join(out_dir, f"{state['song']}_split.mid")
    write_midi(split_path, tracks, midi["division"])

    used = {t[2] for t in tracks if t[2]}
    sf2_to_sfz(state["sf2"], out_dir, only=used,
               name_fn=lambda p: sfz_stem(p["bank"], p["program"], names.get((p["bank"], p["program"]))))

    manifest = {"song": state["song"], "tracks": {}}
    for name, _, inst in tracks:
        if inst:
            sfz = os.path.join(out_dir, sfz_stem(*inst, names.get(inst)) + ".sfz")
            if os.path.exists(sfz):
                manifest["tracks"][name] = sfz
    with open(os.path.join(out_dir, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=1, ensure_ascii=False)

    # Remember names for next time
    mem = name_memory.load()
    seen_in = f"{state['song']} / {os.path.basename(state['sf2'])}"
    for inst in state["instruments"]:
        key = parse_key(inst["key"])
        if inst["fingerprint"] and (key in names or inst["remembered"]):
            name_memory.remember(mem, inst["fingerprint"], names.get(key, ""), seen_in)
    name_memory.save(mem)

    state["names"] = {inst_key(*k): v for k, v in names.items()}
    state["finalized"] = True
    _save_state(out_dir, state)
    return {"split_mid": split_path, "tracks": list(manifest["tracks"]), "folder": out_dir}


SKELETON_PATH = os.path.join(HERE, "template", "skeleton.flp")


def can_generate():
    """True once the app has a base project and the sforzando setup (after the first
    song went through FL's own import)."""
    import build_flp
    return os.path.exists(SKELETON_PATH) and build_flp.has_donor()


def manifest_tracks(out_dir):
    """{FL channel name: .sfz path} for a finalized run, or {} if there isn't one yet."""
    try:
        return _manifest(out_dir)["tracks"]
    except (OSError, ValueError, KeyError, TypeError):
        return {}


def _manifest(out_dir):
    with open(os.path.join(out_dir, "manifest.json"), encoding="utf-8") as f:
        return json.load(f)


def _generate_events(out_dir, out_path, title):
    import build_flp
    import flgen
    from analyze import parse_midi
    manifest = _manifest(out_dir)
    midi = parse_midi(os.path.join(out_dir, f"{manifest['song']}_split.mid"))
    donor, _ = build_flp.find_or_load_donor([], {})

    def plugin_for(sfz):
        return {201: donor[201], 212: donor[212], 213: build_flp.set_sfz(donor[213], sfz)}

    # manifest tracks are in split-MIDI order; find each one's track index by name
    names = {}
    for i, tr in enumerate(midi["tracks"]):
        for e in tr:
            if e["kind"] == "meta" and e["type"] == 0x03:
                names[e["data"].decode("latin-1")] = i
                break
    channels = [(names[n], n, sfz) for n, sfz in manifest["tracks"].items()]
    data_folder = os.path.splitext(os.path.abspath(out_path))[0]
    os.makedirs(data_folder, exist_ok=True)
    header, events = flgen.build_project(SKELETON_PATH, midi, channels, title, data_folder, plugin_for)
    placed = build_flp.fill_playlist(header, events)
    return header, events, len(channels), placed


def generate(out_dir):
    """Write the finished FL project directly, without going through FL's import."""
    import flp
    song = _manifest(out_dir)["song"]
    out_path = os.path.join(out_dir, f"{song}.flp")
    header, events, n, placed = _generate_events(out_dir, out_path, song)
    flp.write(out_path, header, events)
    lines = [f"{n} channels, each on sforzando with its instrument",
             f"Playlist: {placed} pattern(s), one per track, all starting at bar 1"]
    unknown = _unsupported_controllers(out_dir)
    if unknown:
        lines.append("Note: this song uses MIDI controllers the direct build doesn't copy yet ("
                     + ", ".join(unknown) + "). If something sounds off, use FL's import "
                     "instead; the app will report the difference.")
    return {"out": out_path, "changed": n, "skipped": [], "donor_from": "saved setup", "lines": lines}


# Controllers the direct build reproduces (bank select and RPN are used, not copied).
_KNOWN_CC = {0, 1, 6, 7, 10, 32, 38, 100, 101}


def _unsupported_controllers(out_dir):
    from analyze import parse_midi
    song = _manifest(out_dir)["song"]
    midi = parse_midi(os.path.join(out_dir, f"{song}_split.mid"))
    found = set()
    for tr in midi["tracks"]:
        for e in tr:
            if e["kind"] == "chan" and e["status"] == 0xB0 and e["args"][0] not in _KNOWN_CC:
                found.add(f"CC{e['args'][0]}")
            elif e["kind"] == "chan" and e["status"] in (0xA0, 0xD0):
                found.add("aftertouch")
    return sorted(found)


# Project-state values that come from whichever project was used as the base, not
# from the song (loop mode, a flag, creation time, an unknown value at the end).
_STATE_EVENTS = {9, 167, 237, 243}


def compare_with_fl(out_dir, fl_built_path, hand_channels=()):
    """Generate the same song from scratch and compare it with the project built from
    FL's own import. Channels you switched to sforzando by hand in FL are skipped: FL
    resets some of their settings when a plugin is replaced, so there's nothing to match.
    Returns a list of differences (empty = identical)."""
    import flgen
    import flp
    _, fl_events = flp.read(fl_built_path)
    skip = set()
    for idx, (s, e) in flgen.channel_ranges(fl_events).items():
        if idx in hand_channels:
            skip.update(range(s, e))
    title = next((flp.text(v) for e, v in fl_events if e == 231), "")
    data = next((flp.text(v) for e, v in fl_events if e == 202), "")
    _, events, _, _ = _generate_events(out_dir, fl_built_path, title)
    for ev in events:
        if ev[0] == 202:
            ev[1] = (data + chr(0)).encode("utf-16-le")
    diffs = []
    if len(events) != len(fl_events):
        diffs.append(f"event count {len(fl_events)} (FL) vs {len(events)} (generated)")
    for i, (a, b) in enumerate(zip(fl_events, events)):
        if a[0] != b[0]:
            diffs.append(f"#{i}: event {a[0]} (FL) vs {b[0]} (generated)")
            break
        if a[1] != b[1] and a[0] not in _STATE_EVENTS and i not in skip:
            diffs.append(f"#{i}: event {a[0]} differs")
    return diffs


def hand_set_channels(flp_path):
    """Indexes of channels that were already sforzando in the project you saved."""
    import build_flp
    import flgen
    import flp
    _, events = flp.read(flp_path)
    found = set()
    for idx, (s, e) in flgen.channel_ranges(events).items():
        if build_flp.is_sforzando(events, build_flp.channel_info(events, s, e)):
            found.add(idx)
    return found


def build(out_dir, flp_path):
    """Build from a project the user imported in FL. The first time, that project also
    becomes the base for generating later songs directly."""
    import build_flp
    manifest = _manifest(out_dir)
    out_path = os.path.join(out_dir, f"{manifest['song']}_sforzando.flp")
    result = build_flp.build(flp_path, out_path, manifest["tracks"])
    if not os.path.exists(SKELETON_PATH):
        os.makedirs(os.path.dirname(SKELETON_PATH), exist_ok=True)
        shutil.copyfile(flp_path, SKELETON_PATH)
        result["lines"].append("Saved this project as the base: next songs skip the FL import step")
    try:
        diffs = compare_with_fl(out_dir, out_path, hand_set_channels(flp_path))
        result["check"] = diffs
        result["lines"].append("Check: the app's own build matches FL's import exactly" if not diffs else
                               f"Check: {len(diffs)} difference(s) from FL's import: " + "; ".join(diffs[:5]))
    except Exception as e:  # the check must never block the real result
        result["lines"].append(f"Check skipped: {e}")
    return result
