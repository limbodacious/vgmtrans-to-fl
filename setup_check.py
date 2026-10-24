"""Finds the programs the app works with, and keeps the app's small settings file.

Only looks in the standard install folders; it never searches the disk.
"""
import glob
import json
import os

from paths import APP_DIR as HERE
SETTINGS_PATH = os.path.join(HERE, "data", "settings.json")

PROGRAM_FILES = [os.environ.get("ProgramFiles", r"C:\Program Files"),
                 os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")]


def find_fl_studio():
    """Path to FL Studio's .exe, newest version first, or None."""
    found = []
    for pf in PROGRAM_FILES:
        found += glob.glob(os.path.join(pf, "Image-Line", "FL Studio*", "FL64.exe"))
        found += glob.glob(os.path.join(pf, "Image-Line", "FL Studio*", "FL.exe"))
    return sorted(found, reverse=True)[0] if found else None


def find_sforzando():
    """Path to an installed sforzando plugin (VST3 or VST2), or None."""
    pf = PROGRAM_FILES[0]
    candidates = [os.path.join(pf, "Common Files", "VST3", "sforzando.vst3")]
    for folder in ("VSTPlugins", os.path.join("Steinberg", "VSTPlugins"), os.path.join("Common Files", "VST2"),
                   os.path.join("Image-Line", "FL Studio 21", "Plugins", "VST")):
        candidates += glob.glob(os.path.join(pf, folder, "**", "sforzando*.dll"), recursive=True)
    return next((c for c in candidates if os.path.exists(c)), None)


def load_settings():
    try:
        with open(SETTINGS_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_setting(key, value):
    settings = load_settings()
    settings[key] = value
    os.makedirs(os.path.dirname(SETTINGS_PATH), exist_ok=True)
    with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
        json.dump(settings, f, indent=1)


def vgmtrans_path():
    path = load_settings().get("vgmtrans")
    return path if path and os.path.exists(path) else None
