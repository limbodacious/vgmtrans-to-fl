# fl-studio-soundfont

Gets VGMTrans rips of DS music (e.g. Pokémon Platinum) into FL Studio with the right
instrument on every channel. Standard-library Python only.

## Quick start

1. Put one `.mid` and one `.sf2` from VGMTrans in this folder.
2. Run `python convert.py` (or `python convert.py SONG.mid BANK.sf2`).
3. Open the new folder it prints: `output/<song>_<date>_<time>/`.
4. FL Studio: File > Import > MIDI file > `<song>_split.mid`, channel type **MIDI Out**,
   "Create one channel per track" on.
5. For each channel (named like `Ch4 Instr 48`): right-click > Replace > sforzando,
   then drag `Instr 48.sfz` from the output folder onto sforzando.

Keep the `samples/` folder next to the `.sfz` files; sforzando loads the WAVs from there.

## Optional: skip assigning sforzando by hand (`build_flp.py`)

1. In FL: import `<song>_split.mid` (MIDI Out, one channel per track), do
   Playlist > clip menu > **Split by channel**, and save the project.
2. `python build_flp.py MyProject.flp`
3. Open `MyProject_sforzando.flp`. Every channel is sforzando with its `.sfz` loaded.

It picks the newest `output/` folder for that song (or pass one: `python build_flp.py
MyProject.flp output/<folder>`). The original project isn't changed.

First time only: the script needs one channel you set to sforzando by hand, to copy
its setup from. That setup is saved in `template/sforzando_donor.json` and reused,
so later projects don't need it.

## What's in each output folder

| File | Purpose |
|---|---|
| `<song>_split.mid` | Each track uses one instrument for the whole song |
| `Instr N.sfz` | One instrument from the SF2, ready for sforzando |
| `samples/` | WAVs used by the `.sfz` files (with loop points) |
| `previews/` | A few seconds of each instrument playing its part, to tell them apart by ear |
| `analysis.txt` | Which instrument plays on which channel, and when it switches |

## Individual scripts

| Script | What it does |
|---|---|
| `analyze.py SONG.mid BANK.sf2` | Prints the instrument timeline per channel |
| `split.py SONG.mid BANK.sf2` | Writes only the split MIDI |
| `sf2sfz.py BANK.sf2 OUT_DIR [--only 1,2,48]` | Converts SF2 presets to SFZ |

## Notes

- The SFZ files copy the SF2's key ranges, tuning, volume, pan, envelope and loops.
  Volume uses the SF2 spec directly (no extra scaling), so levels may differ slightly
  from Fruity Soundfont Player.
- Don't send the split MIDI into one multi-instrument synth (BassMidi): tracks from the
  same original channel share a MIDI channel and would overwrite each other's instrument.
- To put an edited song back into the game, the split tracks must be merged back into
  the original channels first (not built yet).
