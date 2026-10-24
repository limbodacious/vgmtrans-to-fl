# vgmtrans-to-fl

Gets VGMTrans rips (MIDI + SF2, e.g. Pokémon Platinum on DS) into FL Studio with the right
instrument on every channel, and lets you name each instrument by ear.

VGMTrans gives you a MIDI where one channel often switches between several instruments,
plus a SoundFont with dozens of unnamed presets. Matching them up in FL by hand is slow.
This app:

1. **Splits** every channel so each FL channel plays exactly one instrument.
2. **Converts** each instrument in the SoundFont into its own sforzando (`.sfz`) instrument.
3. **Plays a preview** of each instrument so you can name it ("Strings", "Snare"...).
   Names are **remembered** by the instrument's actual audio, so the same instrument in
   another song or game is named for you.
4. **Builds the FL project** directly: every channel on sforzando with its instrument loaded,
   each pattern on its own Playlist track at bar 1. No importing in FL.

Works with any VGMTrans rip that has a MIDI and an SF2, not just DS games.

## Requirements

- Windows, [Python 3.10+](https://www.python.org/downloads/)
- FL Studio (tested with 21.2)
- [VGMTrans](https://github.com/vgmtrans/vgmtrans/releases) and
  [sforzando](https://www.plogue.com/products/sforzando.html) (both free)

## Run it

Double-click **`start.bat`**. The first run installs the app's packages (PySide6, about
300 MB) into a `.venv` folder next to it; after that it opens straight away.

Or by hand:

```
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python app.py
```

The app walks you through the rest:

| Step | What happens |
|---|---|
| Welcome | Links to VGMTrans and sforzando |
| Rip a song | In VGMTrans: select the song (red note icon, bottom panel), **Convert → Export as MIDI and SF2**, save into `rips/`. Then pick the song from the list |
| Name samples | Play each instrument and type a name; the app then builds the FL project |
| FL import (first time only) | Import the split MIDI in FL, *Split by channel*, set one channel to sforzando, save into `input/` |
| Done | Open the finished project in FL Studio |

**First song only:** the app needs one project made by FL's own MIDI import (with one
channel set to sforzando by hand). It learns FL's project layout and your sforzando setup
from it, and builds every later song by itself.

**How accurate is the direct build?** It writes notes, controllers (volume, pan, pitch
bend, program, tempo) and channels the same way FL's MIDI import does, byte for byte.
Whenever you build a song through FL's import instead (*Use FL's import instead…* on the
Done page), the app also builds it directly and reports whether the two match.

## Where things are saved

| Path | What |
|---|---|
| `rips/` | Your VGMTrans exports. Subfolders (one per game) are fine |
| `input/` | FL projects you save after importing (subfolders are fine); the app uses the one named after the song, or the newest |
| `output/<song>_<date>_<time>/` | One folder per song: split MIDI, `.sfz` instruments, `samples/`, `previews/`, finished `.flp` |
| `data/instrument-names.json` | Your remembered names (keyed by an audio fingerprint, so it works across games) |
| `template/sforzando_donor.json` | Your sforzando setup, copied from the first project |
| `template/skeleton.flp` | The first FL-imported project; the base for building later songs |

Keep each song's output folder: the FL project loads its instruments from there.

## Command line

The app's steps are also available as scripts (standard library only, no PySide6 needed):

| Script | What it does |
|---|---|
| `python convert.py [SONG.mid BANK.sf2]` | Analyze + previews + split MIDI + `.sfz` files, using remembered names (default: newest song in `rips/`) |
| `python build_flp.py PROJECT.flp [OUTPUT_FOLDER]` | Switch every MIDI Out channel to sforzando |
| `python analyze.py SONG.mid BANK.sf2` | Print which instrument plays on which channel, and when it switches |
| `python sf2sfz.py BANK.sf2 OUT_DIR [--only 1,2,48]` | Convert SF2 presets to `.sfz` |

## Notes

- The `.sfz` files copy the SF2's key ranges, tuning, volume, pan, envelope and loops. It
  sounds close to the game, but not identical: the DS's own sound chip is crunchier.
- The `.flp` editing works on the project format FL Studio 21 saves. Your original project
  is never changed; the app writes a new `_sforzando.flp` into the song's output folder.
- Ripped game files stay out of the repo: `.gitignore` excludes `.mid`, `.sf2`, `.sfz`,
  `.wav` and `output/`.
