"""Turn SF2 presets into standalone SFZ instruments (one .sfz per preset + WAV samples).

Usage:
    python sf2sfz.py BANK.sf2 OUT_DIR [--only 1,2,48]

Handles the SF2 settings VGMTrans writes: key/velocity ranges, root key, tuning,
volume, pan, volume envelope and loop mode. Unknown settings are ignored.
"""
import argparse
import math
import os
import re
import struct
import sys

# SF2 generator numbers we translate
G_START_OFS, G_END_OFS, G_LOOP_START_OFS, G_LOOP_END_OFS = 0, 1, 2, 3
G_START_COARSE, G_END_COARSE, G_LOOP_START_COARSE, G_LOOP_END_COARSE = 4, 12, 45, 50
G_PAN = 17
G_DELAY, G_ATTACK, G_HOLD, G_DECAY, G_SUSTAIN, G_RELEASE = 33, 34, 35, 36, 37, 38
G_INSTRUMENT, G_KEYRANGE, G_VELRANGE = 41, 43, 44
G_ATTENUATION, G_COARSE_TUNE, G_FINE_TUNE = 48, 51, 52
G_SAMPLE_ID, G_SAMPLE_MODES, G_SCALE_TUNING, G_EXCLUSIVE, G_ROOT_KEY = 53, 54, 56, 57, 58

SIGNED = {G_PAN, G_DELAY, G_ATTACK, G_HOLD, G_DECAY, G_RELEASE, G_COARSE_TUNE, G_FINE_TUNE,
          G_START_OFS, G_END_OFS, G_LOOP_START_OFS, G_LOOP_END_OFS,
          G_START_COARSE, G_END_COARSE, G_LOOP_START_COARSE, G_LOOP_END_COARSE}
# Preset-level values of these are added to the instrument values (SF2 spec 9.4)
ADDITIVE = {G_PAN, G_DELAY, G_ATTACK, G_HOLD, G_DECAY, G_SUSTAIN, G_RELEASE,
            G_ATTENUATION, G_COARSE_TUNE, G_FINE_TUNE}
DEFAULTS = {G_DELAY: -12000, G_ATTACK: -12000, G_HOLD: -12000, G_DECAY: -12000,
            G_RELEASE: -12000, G_SUSTAIN: 0, G_ATTENUATION: 0, G_PAN: 0,
            G_COARSE_TUNE: 0, G_FINE_TUNE: 0, G_SAMPLE_MODES: 0, G_SCALE_TUNING: 100,
            G_EXCLUSIVE: 0, G_ROOT_KEY: -1}


# ------------------------------------------------------------------ parsing

def _chunks(data, start, end):
    pos = start
    while pos + 8 <= end:
        tag = data[pos:pos + 4]
        size = struct.unpack("<I", data[pos + 4:pos + 8])[0]
        yield tag, pos + 8, size
        pos += 8 + size + (size & 1)


def load_sf2(path):
    data = open(path, "rb").read()
    if data[:4] != b"RIFF" or data[8:12] != b"sfbk":
        sys.exit(f"{path} is not a SoundFont 2 file")
    lists = {}
    for tag, start, size in _chunks(data, 12, len(data)):
        if tag == b"LIST":
            kind = data[start:start + 4]
            lists[kind] = {t: data[s:s + n] for t, s, n in _chunks(data, start + 4, start + size)}
    sdta, pdta = lists[b"sdta"], lists[b"pdta"]

    def records(name, size):
        raw = pdta[name]
        return [raw[i:i + size] for i in range(0, len(raw), size)]

    def gens(name):
        out = []
        for r in records(name, 4):
            op = struct.unpack("<H", r[:2])[0]
            if op == G_KEYRANGE or op == G_VELRANGE:
                val = (r[2], r[3])
            elif op in SIGNED:
                val = struct.unpack("<h", r[2:4])[0]
            else:
                val = struct.unpack("<H", r[2:4])[0]
            out.append((op, val))
        return out

    def zones(hdr_name, hdr_size, name_len, bag_name, gen_list):
        hdrs = records(hdr_name, hdr_size)
        bags = [struct.unpack("<H", b[:2])[0] for b in records(bag_name, 4)]
        result = []
        for i in range(len(hdrs) - 1):  # last header is the terminator
            h = hdrs[i]
            bag_idx = struct.unpack("<H", h[name_len + (4 if hdr_name == b"phdr" else 0):][:2])[0]
            next_bag = struct.unpack("<H", hdrs[i + 1][name_len + (4 if hdr_name == b"phdr" else 0):][:2])[0]
            zs = []
            for b in range(bag_idx, next_bag):
                zs.append(dict(gen_list[bags[b]:bags[b + 1]]))
            result.append((h, zs))
        return result

    presets = []
    for h, zs in zones(b"phdr", 38, 20, b"pbag", gens(b"pgen")):
        name = h[:20].split(b"\0")[0].decode("latin-1")
        prog, bank = struct.unpack("<HH", h[20:24])
        presets.append({"name": name, "bank": bank, "program": prog, "zones": zs})
    instruments = []
    for h, zs in zones(b"inst", 22, 20, b"ibag", gens(b"igen")):
        instruments.append({"name": h[:20].split(b"\0")[0].decode("latin-1"), "zones": zs})
    samples = []
    for r in records(b"shdr", 46)[:-1]:
        name = r[:20].split(b"\0")[0].decode("latin-1")
        start, end, ls, le, rate, pitch, corr, link, stype = struct.unpack("<IIIIIBbHH", r[20:46])
        samples.append({"name": name, "start": start, "end": end, "loop_start": ls, "loop_end": le,
                        "rate": rate, "pitch": pitch, "correction": corr, "type": stype})
    return {"presets": presets, "instruments": instruments, "samples": samples, "smpl": sdta[b"smpl"]}


# ------------------------------------------------------------------ helpers

def _split_global(zones, terminal_gen):
    """SF2: a first zone without the terminal generator is the global zone."""
    if zones and terminal_gen not in zones[0]:
        return zones[0], zones[1:]
    return {}, zones


def _intersect(a, b):
    lo, hi = max(a[0], b[0]), min(a[1], b[1])
    return (lo, hi) if lo <= hi else None


def _seconds(timecents):
    return 0.0 if timecents <= -12000 else 2 ** (timecents / 1200)


def _safe(name):
    return re.sub(r'[<>:"/\\|?*]+', "_", name).strip() or "unnamed"


def write_wav(path, pcm, rate, loop=None):
    """16-bit mono WAV, with a 'smpl' loop chunk so other samplers see the loop too."""
    extra = b""
    if loop:
        smpl = struct.pack("<9I", 0, 0, int(1e9 / rate), 60, 0, 0, 0, 1, 0)
        smpl += struct.pack("<6I", 0, 0, loop[0], loop[1], 0, 0)
        extra = b"smpl" + struct.pack("<I", len(smpl)) + smpl
    fmt = struct.pack("<HHIIHH", 1, 1, rate, rate * 2, 2, 16)
    body = b"WAVE" + b"fmt " + struct.pack("<I", 16) + fmt + extra + b"data" + struct.pack("<I", len(pcm)) + pcm
    with open(path, "wb") as f:
        f.write(b"RIFF" + struct.pack("<I", len(body)) + body)


# ------------------------------------------------------------------ conversion

def preset_regions(sf, preset):
    """Flatten a preset into a list of fully-resolved regions (one per sample zone)."""
    pglobal, pzones = _split_global(preset["zones"], G_INSTRUMENT)
    regions = []
    for pz in pzones:
        pgen = {**pglobal, **pz}
        inst = sf["instruments"][pgen[G_INSTRUMENT]]
        iglobal, izones = _split_global(inst["zones"], G_SAMPLE_ID)
        for iz in izones:
            igen = {**DEFAULTS, **iglobal, **iz}
            g = dict(igen)
            for op in ADDITIVE:
                if op in pgen:
                    g[op] = igen.get(op, DEFAULTS.get(op, 0)) + pgen[op]
            key = _intersect(igen.get(G_KEYRANGE, (0, 127)), pgen.get(G_KEYRANGE, (0, 127)))
            vel = _intersect(igen.get(G_VELRANGE, (0, 127)), pgen.get(G_VELRANGE, (0, 127)))
            if key is None or vel is None:
                continue
            g[G_KEYRANGE], g[G_VELRANGE] = key, vel
            regions.append(g)
    return regions


def sample_offsets(sf, g):
    """Start/end/loop points of a zone's sample, relative to the sample's own start."""
    s = sf["samples"][g[G_SAMPLE_ID]]
    start = s["start"] + g.get(G_START_OFS, 0) + 32768 * g.get(G_START_COARSE, 0)
    end = s["end"] + g.get(G_END_OFS, 0) + 32768 * g.get(G_END_COARSE, 0)
    ls = s["loop_start"] + g.get(G_LOOP_START_OFS, 0) + 32768 * g.get(G_LOOP_START_COARSE, 0)
    le = s["loop_end"] + g.get(G_LOOP_END_OFS, 0) + 32768 * g.get(G_LOOP_END_COARSE, 0)
    return start, end, ls, le


def write_sfz(sf, preset, out_dir, sfz_name, written_samples):
    lines = [f"// {preset['name']}  (SF2 bank {preset['bank']}, program {preset['program']})",
             "// Generated by sf2sfz.py", ""]
    for g in preset_regions(sf, preset):
        s = sf["samples"][g[G_SAMPLE_ID]]
        start, end, ls, le = sample_offsets(sf, g)
        mode = g[G_SAMPLE_MODES] & 3
        looped = mode in (1, 3) and le > ls

        # One WAV per distinct (sample, start, end) so offsets stay exact.
        wkey = (g[G_SAMPLE_ID], start, end)
        if wkey not in written_samples:
            fname = f"{len(written_samples):03d}_{_safe(s['name'])}.wav"
            pcm = sf["smpl"][start * 2:end * 2]
            loop = (ls - start, le - start - 1) if looped else None
            write_wav(os.path.join(out_dir, "samples", fname), pcm, s["rate"], loop)
            written_samples[wkey] = fname
        wav = written_samples[wkey]

        root = g[G_ROOT_KEY] if 0 <= g[G_ROOT_KEY] <= 127 else s["pitch"]
        r = [
            "<region>",
            f"sample=samples/{wav}",
            f"lokey={g[G_KEYRANGE][0]} hikey={g[G_KEYRANGE][1]}",
            f"lovel={g[G_VELRANGE][0]} hivel={g[G_VELRANGE][1]}",
            f"pitch_keycenter={root}",
        ]
        tune = g[G_FINE_TUNE] + s["correction"]
        if tune:
            r.append(f"tune={tune}")
        if g[G_COARSE_TUNE]:
            r.append(f"transpose={g[G_COARSE_TUNE]}")
        if g[G_SCALE_TUNING] != 100:
            r.append(f"pitch_keytrack={g[G_SCALE_TUNING]}")
        if g[G_ATTENUATION]:
            r.append(f"volume={-g[G_ATTENUATION] / 10:.2f}")
        if g[G_PAN]:
            r.append(f"pan={max(-100, min(100, g[G_PAN] / 5)):.1f}")
        if looped:
            r.append("loop_mode=" + ("loop_sustain" if mode == 3 else "loop_continuous"))
            r.append(f"loop_start={ls - start} loop_end={le - start - 1}")
        else:
            r.append("loop_mode=no_loop")
        r.append(
            f"ampeg_delay={_seconds(g[G_DELAY]):.4f} ampeg_attack={_seconds(g[G_ATTACK]):.4f} "
            f"ampeg_hold={_seconds(g[G_HOLD]):.4f} ampeg_decay={_seconds(g[G_DECAY]):.4f} "
            f"ampeg_sustain={100 * 10 ** (-max(0, g[G_SUSTAIN]) / 200):.3f} "
            f"ampeg_release={_seconds(g[G_RELEASE]):.4f}"
        )
        if g[G_EXCLUSIVE]:
            r.append(f"group={g[G_EXCLUSIVE]} off_by={g[G_EXCLUSIVE]}")
        lines.append(" ".join(r[:1]) + "\n" + "\n".join(r[1:]) + "\n")
    path = os.path.join(out_dir, sfz_name)
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return path


def convert(sf2_path, out_dir, only=None, name_fn=None):
    """only: set of (bank, program) to export, or None for all.
    name_fn: (preset) -> file name without extension."""
    sf = load_sf2(sf2_path)
    os.makedirs(os.path.join(out_dir, "samples"), exist_ok=True)
    written, made = {}, []
    for p in sf["presets"]:
        if only is not None and (p["bank"], p["program"]) not in only:
            continue
        name = name_fn(p) if name_fn else _safe(p["name"])
        made.append((p, write_sfz(sf, p, out_dir, name + ".sfz", written)))
    return made


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("sf2")
    ap.add_argument("out_dir")
    ap.add_argument("--only", help="comma-separated program numbers (bank 0)")
    args = ap.parse_args()
    only = {(0, int(x)) for x in args.only.split(",")} if args.only else None
    for p, path in convert(args.sf2, args.out_dir, only):
        print(f"{p['name']:<22} -> {path}")


if __name__ == "__main__":
    main()
