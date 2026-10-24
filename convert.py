"""Command-line version of the app: analyze, previews, split MIDI, SFZ files.

Usage:
    python convert.py                      (newest song in the rips folder)
    python convert.py SONG.mid BANK.sf2

Instruments you named before (in the app) get their names automatically.
Every run writes into a new folder:  output/<song>_<date>_<time>/
"""
import os
import sys

import pipeline

from paths import APP_DIR as HERE


def main():
    if len(sys.argv) == 3:
        midi_path, sf2_path = sys.argv[1], sys.argv[2]
    elif len(sys.argv) == 1:
        rips = [r for r in pipeline.find_rips() if r["sf2"]]
        if not rips:
            sys.exit("No songs in the rips folder. Export a .mid + .sf2 from VGMTrans into "
                     f"{pipeline.RIPS_DIR}, or pass the files: python convert.py SONG.mid BANK.sf2")
        midi_path, sf2_path = rips[0]["midi"], rips[0]["sf2"]
        print(f"Newest song in rips: {os.path.basename(midi_path)} + {os.path.basename(sf2_path)}")
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
