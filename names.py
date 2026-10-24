"""Remembered instrument names.

Each instrument is identified by a fingerprint of its actual audio data (every
sample it uses plus the key range each sample covers). The same instrument in
another song, bank or game gets the same fingerprint, so its name is reused.

Stored in data/instrument-names.json:
    {"instruments": {fingerprint: {"name": ..., "updated": ..., "seen": [...]}}}
"""
import hashlib
import json
import os
import re
from datetime import datetime

from sf2sfz import G_KEYRANGE, preset_regions, sample_offsets

from paths import APP_DIR as HERE
NAMES_PATH = os.path.join(HERE, "data", "instrument-names.json")


def clean_name(name):
    """Keep names safe for file names and FL channel names."""
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', " ", name or "")
    return re.sub(r"\s+", " ", name).strip()[:40]


def fingerprint(sf, preset):
    parts = []
    for g in preset_regions(sf, preset):
        start, end, _, _ = sample_offsets(sf, g)
        h = hashlib.sha1(sf["smpl"][start * 2:end * 2]).hexdigest()[:16]
        parts.append(f"{g[G_KEYRANGE][0]}-{g[G_KEYRANGE][1]}:{h}")
    return hashlib.sha1("|".join(sorted(parts)).encode()).hexdigest()[:16]


def load():
    try:
        with open(NAMES_PATH, encoding="utf-8") as f:
            mem = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        mem = {}
    mem.setdefault("instruments", {})
    return mem


def save(mem):
    os.makedirs(os.path.dirname(NAMES_PATH), exist_ok=True)
    tmp = NAMES_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(mem, f, indent=1, sort_keys=True, ensure_ascii=False)
    os.replace(tmp, NAMES_PATH)


def lookup(mem, fp):
    entry = mem["instruments"].get(fp)
    return entry["name"] if entry else None


def remember(mem, fp, name, seen_in):
    """Save a name. An empty name forgets the instrument."""
    name = clean_name(name)
    if not name:
        mem["instruments"].pop(fp, None)
        return
    entry = mem["instruments"].setdefault(fp, {"seen": []})
    entry["name"] = name
    entry["updated"] = datetime.now().isoformat(timespec="seconds")
    if seen_in not in entry["seen"]:
        entry["seen"].append(seen_in)
