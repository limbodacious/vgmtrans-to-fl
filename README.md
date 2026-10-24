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

## Download

Get **VGMTrans-to-FL.exe** from the [Releases](../../releases) page. Put it in its own
folder (for example `Documents\VGMTrans-to-FL`) and double-click it. The app creates its
folders (`rips`, `input`, `output`, ...) next to the .exe.

Windows may show "Windows protected your PC" because the .exe isn't signed: click
**More info → Run anyway**.

## Requirements

- Windows
- FL Studio 21 (tested with 21.2; other versions not checked yet)
- [VGMTrans](https://github.com/vgmtrans/vgmtrans/releases) and
  [sforzando](https://www.plogue.com/products/sforzando.html) (both free)

## Run from source

Needs [Python 3.10+](https://www.python.org/downloads/). Double-click **`start.bat`**: the
first run installs PySide6 (about 300 MB) into `.venv`. Or by hand:

```
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python app.py
```

To build the .exe yourself: `.venv\Scripts\python -m pip install pyinstaller`, then
`.venv\Scripts\python build_exe.py` (output: `dist\VGMTrans-to-FL.exe`).

The app walks you through the rest:

1. **Set up**: checks for FL Studio and sforzando by itself, and links to VGMTrans.
2. **Pick a song**: in VGMTrans, click the song's red note icon (bottom list), then
   **Convert → Export as MIDI and SF2** into the `rips` folder. It shows up in the app.
3. **Name instruments**: click each box to hear the instrument and type a name.
4. **FL setup (first song only)**: import the song in FL once, set one channel to
   sforzando, save into `input`. The app learns from that project.
5. **Done**: open the finished project in FL Studio.

After the first song, it's just: pick a song → name instruments → open in FL Studio.

**How accurate is the direct build?** It writes notes, controllers (volume, pan, pitch
bend, program, tempo) and channels the same way FL's MIDI import does, byte for byte
(verified on two songs). If a song uses something not verified yet (tempo changes,
expression, channel 10...), the Done page says so. *Rebuild it through FL's own import*
on that page builds it both ways and compares them.

Before trusting a new game, run `python check.py`: it lists which songs in `rips` use
features that haven't been verified, and the fewest songs to rebuild through FL's
import to cover all of them.

## Where things are saved

| Path | What |
|---|---|
| `rips/` | Your VGMTrans exports. Subfolders (one per game) are fine |
| `input/` | FL projects you save after importing (subfolders are fine); the app uses the one named after the song, or the newest |
| `output/<song>_<date>_<time>/` | One folder per song: split MIDI, `.sfz` instruments, `samples/`, `previews/`, finished `.flp` |
| `data/instrument-names.json` | Your remembered names (keyed by an audio fingerprint, so it works across games) |
| `data/settings.json` | Where VGMTrans is, and your app preferences |
| `template/sforzando_donor.json` | Your sforzando setup, copied from the first project |
| `template/skeleton.flp` | The first FL-imported project; the base for building later songs |

Keep each song's output folder: the FL project loads its instruments from there.

## Command line

The app's steps are also available as scripts (standard library only, no PySide6 needed):

| Script | What it does |
|---|---|
| `python check.py [FOLDER]` | Which songs use features not yet verified against FL's import |
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
