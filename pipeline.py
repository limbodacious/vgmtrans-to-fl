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


def build(out_dir, flp_path):
    import build_flp
    with open(os.path.join(out_dir, "manifest.json"), encoding="utf-8") as f:
        manifest = json.load(f)
    out_path = os.path.join(out_dir, f"{manifest['song']}_sforzando.flp")
    return build_flp.build(flp_path, out_path, manifest["tracks"])
