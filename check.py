"""Find which songs to double-check against FL's own import before trusting the direct build.

Usage:
    python check.py [FOLDER]        (default: the rips folder, including subfolders)

For every MIDI it lists the features the direct build hasn't been verified on yet, then
suggests the smallest set of songs that covers all of them. Put those songs through the
app's "Use FL's import instead" button: the app compares both builds and tells you if
anything differs.
"""
import os
import sys

import pipeline
from analyze import parse_midi
from split import split


def main():
    folder = sys.argv[1] if len(sys.argv) > 1 else pipeline.RIPS_DIR
    songs = pipeline.find_rips(folder)
    if not songs:
        sys.exit(f"No .mid files in {folder}")

    results = {}
    for r in songs:
        try:
            midi = parse_midi(r["midi"])
            tracks = split(midi, {})
            split_midi = {"division": midi["division"], "tracks": [t[1] for t in tracks]}
            results[r["midi"]] = untested(split_midi)
        except Exception as e:  # a broken file shouldn't stop the scan
            results[r["midi"]] = [f"couldn't read: {e}"]

    width = max(len(os.path.relpath(m, folder)) for m in results)
    clean = 0
    for m, feats in sorted(results.items()):
        name = os.path.relpath(m, folder)
        if feats:
            print(f"{name:<{width}}  {'; '.join(feats)}")
        else:
            clean += 1
    print(f"\n{clean} of {len(results)} songs use only verified features (safe to build directly).")

    # Greedy set cover: fewest songs that together use every untested feature
    remaining = {f for feats in results.values() for f in feats if not f.startswith("couldn't")}
    if not remaining:
        print("Nothing left to check.")
        return
    picks = []
    while remaining:
        best = max(results, key=lambda m: len(remaining & set(results[m])))
        gained = remaining & set(results[best])
        if not gained:
            break
        picks.append((best, sorted(gained)))
        remaining -= gained
    print(f"\nTo cover every untested feature, put these {len(picks)} song(s) through FL's import:")
    for m, gained in picks:
        print(f"  {os.path.relpath(m, folder)}  ({'; '.join(gained)})")


def untested(split_midi):
    return pipeline.untested_features(_parse_tracks(split_midi))


def _parse_tracks(split_midi):
    """Turn split() output back into parse_midi()-style tracks by writing a temp MIDI."""
    import tempfile
    from split import write_midi
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "split.mid")
        write_midi(path, [(None, evs, None) for evs in split_midi["tracks"]], split_midi["division"])
        return parse_midi(path)


if __name__ == "__main__":
    main()
