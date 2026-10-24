"""Command-line version of the app: analyze, previews, split MIDI, SFZ files.

Usage:
    python convert.py                      (uses the .mid and .sf2 in this folder)
    python convert.py SONG.mid BANK.sf2

Instruments you named before (in the app) get their names automatically.
Every run writes into a new folder:  output/<song>_<date>_<time>/
"""
import glob
import os
import sys

import pipeline

HERE = os.path.dirname(os.path.abspath(__file__))


def pick_input(ext, exclude=None):
    found = [f for f in glob.glob(os.path.join(HERE, "*" + ext))
             if not (exclude and exclude in os.path.basename(f))]
    if len(found) != 1:
        names = ", ".join(os.path.basename(f) for f in found) or "none"
        sys.exit(f"Expected exactly one {ext} file next to convert.py, found: {names}\n"
                 f"Pass the files explicitly: python convert.py SONG.mid BANK.sf2")
    return found[0]


def main():
    if len(sys.argv) == 3:
        midi_path, sf2_path = sys.argv[1], sys.argv[2]
    elif len(sys.argv) == 1:
        midi_path, sf2_path = pick_input(".mid", exclude="_split"), pick_input(".sf2")
    else:
        sys.exit(__doc__)

    out_dir = pipeline.new_run_dir(os.path.splitext(os.path.basename(midi_path))[0])
    state = pipeline.prepare(midi_path, sf2_path, out_dir)
    names = {i["key"]: i["remembered"] for i in state["instruments"] if i["remembered"]}
    result = pipeline.finalize(out_dir, names)

    print(f"Done -> {out_dir}\n")
    for i in state["instruments"]:
        label = names.get(i["key"], "(unnamed)")
        print(f"  Instr {i['program']:<4} {label:<24} channels {', '.join(map(str, i['channels']))}")
    print(f"\nImport into FL: {result['split_mid']}")
    missing = [i for i in state["instruments"] if i["missing"]]
    if missing:
        print("\nWARNING - instruments not in the SF2 (those channels will be silent):")
        for i in missing:
            print(f"  bank {i['bank']} program {i['program']}")


if __name__ == "__main__":
    main()
