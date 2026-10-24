"""One command: analyze + split the MIDI + build one SFZ per instrument + previews.

Usage:
    python convert.py                      (uses the .mid and .sf2 in this folder)
    python convert.py SONG.mid BANK.sf2

Every run writes into a new folder:  output/<song>_<date>_<time>/
    <song>_split.mid     import this into FL (MIDI Out, one channel per track)
    Instr N.sfz          one per instrument; drag onto the sforzando on the
                         FL channel whose name contains "Instr N"
    samples/             WAVs used by the .sfz files (keep next to them)
    previews/            a few seconds of each instrument playing its part, to listen to
    analysis.txt         what plays where, and when instruments switch
"""
import contextlib
import glob
import io
import os
import sys
from datetime import datetime

from analyze import analyze, parse_midi, parse_sf2_presets, print_report
from previews import render_previews
from sf2sfz import convert as sf2_to_sfz, load_sf2
from split import split, track_instrument, write_midi

HERE = os.path.dirname(os.path.abspath(__file__))


def pick_input(ext, exclude=None):
    found = [f for f in glob.glob(os.path.join(HERE, "*" + ext))
             if not (exclude and exclude in os.path.basename(f))]
    if len(found) != 1:
        names = ", ".join(os.path.basename(f) for f in found) or "none"
        sys.exit(f"Expected exactly one {ext} file next to convert.py, found: {names}\n"
                 f"Pass the files explicitly: python convert.py SONG.mid BANK.sf2")
    return found[0]


def file_stem(bank, prog):
    return f"Instr {prog}" + (f" bank {bank}" if bank else "")


def main():
    if len(sys.argv) == 3:
        midi_path, sf2_path = sys.argv[1], sys.argv[2]
    elif len(sys.argv) == 1:
        midi_path, sf2_path = pick_input(".mid", exclude="_split"), pick_input(".sf2")
    else:
        sys.exit(__doc__)

    song = os.path.splitext(os.path.basename(midi_path))[0]
    out_dir = os.path.join(HERE, "output", f"{song}_{datetime.now():%Y-%m-%d_%H%M%S}")
    os.makedirs(out_dir)

    midi = parse_midi(midi_path)
    presets = parse_sf2_presets(sf2_path)

    # 1. analysis.txt
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        print_report(analyze(midi, presets), presets, midi_path, sf2_path)
    with open(os.path.join(out_dir, "analysis.txt"), "w", encoding="utf-8") as f:
        f.write(buf.getvalue())

    # 2. split MIDI
    tracks = split(midi, presets)
    write_midi(os.path.join(out_dir, f"{song}_split.mid"), tracks, midi["division"])
    used = {track_instrument(n) for n, _ in tracks} - {None}
    missing = sorted(u for u in used if u not in presets)

    # 3. one SFZ per instrument the song uses
    made = sf2_to_sfz(sf2_path, out_dir, only=used,
                      name_fn=lambda p: file_stem(p["bank"], p["program"]))

    # 4. previews
    print("Rendering previews...", flush=True)
    render_previews(load_sf2(sf2_path), midi, used, out_dir, file_stem)

    print(f"Done -> {out_dir}\n")
    print(f"{'FL channel':<22} drag this onto its sforzando")
    print("-" * 50)
    for name, _ in tracks:
        inst = track_instrument(name)
        if inst:
            print(f"{name:<22} {file_stem(*inst)}.sfz")
    print(f"\n{len(tracks)} FL channels, {len(made)} SFZ instruments, "
          f"{len(os.listdir(os.path.join(out_dir, 'samples')))} samples")
    if missing:
        print("\nWARNING - instruments not in the SF2 (those channels will be silent):")
        for b, p in missing:
            print(f"  bank {b} program {p}")


if __name__ == "__main__":
    main()
