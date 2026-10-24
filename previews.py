"""Render a short preview WAV of each instrument, playing the notes it plays in the song.

Standard library only. Used by convert.py; writes <out_dir>/previews/<name>.wav.
"""
import math
import os
import struct
from array import array
from collections import defaultdict

from analyze import Clock
from sf2sfz import (G_ATTACK, G_ATTENUATION, G_COARSE_TUNE, G_DECAY, G_DELAY, G_FINE_TUNE,
                    G_HOLD, G_KEYRANGE, G_RELEASE, G_ROOT_KEY, G_SAMPLE_ID, G_SAMPLE_MODES,
                    G_SCALE_TUNING, G_SUSTAIN, G_VELRANGE, preset_regions, sample_offsets,
                    write_wav)

SR = 32000           # close to the DS's own output rate; keeps rendering fast
CLIP_SECONDS = 8.0
MAX_RELEASE = 1.0


def _tc(v):
    return 0.0 if v <= -12000 else 2 ** (v / 1200)


def _render_note(sf, g, pcm_cache, key, vel, dur, mix, offset):
    """Add one note into mix (array of floats) starting at sample `offset`."""
    s = sf["samples"][g[G_SAMPLE_ID]]
    start, end, ls, le = sample_offsets(sf, g)
    ck = (start, end)
    if ck not in pcm_cache:
        raw = array("h")
        raw.frombytes(sf["smpl"][start * 2:end * 2])
        pcm_cache[ck] = raw
    pcm = pcm_cache[ck]
    n_src = len(pcm)
    if n_src < 2:
        return
    ls, le = ls - start, le - start
    looped = (g[G_SAMPLE_MODES] & 3) in (1, 3) and 0 <= ls < le <= n_src
    loop_len = le - ls

    root = g[G_ROOT_KEY] if 0 <= g[G_ROOT_KEY] <= 127 else s["pitch"]
    semis = (key - root) * g[G_SCALE_TUNING] / 100 + g[G_COARSE_TUNE]
    step = 2 ** (semis / 12 + (g[G_FINE_TUNE] + s["correction"]) / 1200) * s["rate"] / SR

    delay, attack, hold = _tc(g[G_DELAY]), _tc(g[G_ATTACK]), _tc(g[G_HOLD])
    decay = max(_tc(g[G_DECAY]), 1e-4)
    release = max(min(_tc(g[G_RELEASE]), MAX_RELEASE), 1e-4)
    sustain_db = -max(0, g[G_SUSTAIN]) / 10
    gain = 10 ** (-g[G_ATTENUATION] / 200) * (vel / 127) ** 2 / 32768

    total = min(int((dur + release) * SR), len(mix) - offset)
    a_end, d_start = delay + attack, delay + attack + hold
    pos, rel_db = 0.0, None
    for i in range(total):
        t = i / SR
        # envelope in dB, SF2-style
        if t < delay:
            amp = 0.0
        else:
            db = max(-(t - d_start) / decay * 100, sustain_db) if t > d_start else 0.0
            if t >= dur:
                if rel_db is None:
                    rel_db = db
                db = rel_db - (t - dur) / release * 100
                if db <= -100:
                    break
            amp = 10 ** (db / 20)
            if t < a_end:
                amp *= (t - delay) / max(attack, 1e-4)
        # sample position with loop
        if looped and pos >= le:
            pos = ls + (pos - ls) % loop_len
        elif not looped and pos >= n_src - 1:
            break
        i0 = int(pos)
        frac = pos - i0
        i1 = i0 + 1
        if i1 >= n_src or (looped and i1 >= le):
            i1 = ls if looped else n_src - 1
        mix[offset + i] += (pcm[i0] + (pcm[i1] - pcm[i0]) * frac) * amp * gain
        pos += step


def notes_by_instrument(midi):
    """{(bank, program): [(start_sec, dur_sec, key, vel), ...]} from the original MIDI.
    Uses the busiest channel for each instrument and its densest CLIP_SECONDS window."""
    clock = Clock(midi)
    per = defaultdict(lambda: defaultdict(list))
    for t_idx, events in enumerate(midi["tracks"]):
        state, sounding = {}, {}
        for e in events:
            if e["kind"] != "chan":
                continue
            ch, st, a = e["ch"], e["status"], e["args"]
            s = state.setdefault(ch, {"msb": 0, "lsb": 0, "prog": None})
            if st == 0xB0 and a[0] == 0:
                s["msb"] = a[1]
            elif st == 0xB0 and a[0] == 32:
                s["lsb"] = a[1]
            elif st == 0xC0:
                s["prog"] = a[0]
            elif st == 0x90 and a[1] > 0:
                inst = (s["msb"] * 128 + s["lsb"], s["prog"])
                sounding.setdefault((ch, a[0]), []).append((e["tick"], a[1], inst))
            elif st == 0x80 or (st == 0x90 and a[1] == 0):
                stack = sounding.get((ch, a[0]))
                if stack:
                    on_tick, vel, inst = stack.pop(0)
                    t0 = clock.seconds(on_tick)
                    per[inst][(t_idx, ch)].append((t0, clock.seconds(e["tick"]) - t0, a[0], vel))
    result = {}
    for inst, chans in per.items():
        notes = sorted(max(chans.values(), key=len))
        starts = [n[0] for n in notes]
        best, best_count, j = 0, -1, 0
        for i, s0 in enumerate(starts):
            while j < len(starts) and starts[j] < s0 + CLIP_SECONDS:
                j += 1
            if j - i > best_count:
                best, best_count = i, j - i
        t0 = starts[best]
        result[inst] = [(s - t0, d, k, v) for s, d, k, v in notes[best:] if s - t0 < CLIP_SECONDS]
    return result


def render_previews(sf, midi, used, out_dir, name_fn):
    """Write previews/<name_fn(bank, prog)>.wav for each (bank, prog) in used."""
    folder = os.path.join(out_dir, "previews")
    os.makedirs(folder, exist_ok=True)
    notes = notes_by_instrument(midi)
    presets = {(p["bank"], p["program"]): p for p in sf["presets"]}
    pcm_cache = {}
    for inst in sorted(used):
        if inst not in presets:
            continue
        regions = preset_regions(sf, presets[inst])
        mix = array("f", bytes(4 * int((CLIP_SECONDS + MAX_RELEASE) * SR)))
        for start, dur, key, vel in notes.get(inst, [(0.0, 1.0, 60, 100)]):
            for g in regions:
                if (g[G_KEYRANGE][0] <= key <= g[G_KEYRANGE][1]
                        and g[G_VELRANGE][0] <= vel <= g[G_VELRANGE][1]):
                    _render_note(sf, g, pcm_cache, key, vel, dur, mix, int(start * SR))
        peak = max((abs(x) for x in mix), default=0) or 1.0
        scale = 0.9 * 32767 / peak
        out = array("h", (int(x * scale) for x in mix))
        write_wav(os.path.join(folder, name_fn(*inst) + ".wav"), out.tobytes(), SR)
