"""vgmtrans-to-fl desktop app.

Turns a song ripped with VGMTrans (.mid + .sf2) into an FL Studio project where every
channel plays the right instrument, after you name each instrument by ear. Names are
remembered for the next time the same instrument shows up, in any song or game.

Run:  python app.py      (or double-click start.bat)
"""
import os
import subprocess
import sys
import time
import traceback
from datetime import datetime

from PySide6.QtCore import QFileSystemWatcher, QObject, QSize, Qt, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QFont, QGuiApplication, QKeySequence, QShortcut
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QFileDialog, QFrame,
                               QGridLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QMainWindow, QMessageBox, QPlainTextEdit,
                               QProgressBar, QPushButton, QScrollArea, QStackedWidget,
                               QVBoxLayout, QWidget)

import build_flp
import names as name_memory
import pipeline
import setup_check

APP_NAME = "VGMTrans to FL"
VGMTRANS_URL = "https://github.com/vgmtrans/vgmtrans/releases"
SFORZANDO_URL = "https://www.plogue.com/products/sforzando.html"
FL_URL = "https://www.image-line.com/fl-studio-download/"

GREEN, ORANGE, GREY = "#2e7d32", "#b26a00", "#8a8f98"

STYLE = """
QWidget { font-size: 10.5pt; }
QListWidget#steps { border: none; padding: 14px 0; }
QListWidget#steps::item { padding: 10px 14px; border-radius: 6px; margin: 2px 8px; }
QListWidget#steps::item:selected { background: palette(highlight); color: palette(highlighted-text); }
QLabel#title { font-size: 18pt; font-weight: 600; }
QLabel#subtitle { color: palette(placeholder-text); }
QLabel#big { font-size: 12.5pt; }
QFrame#card { border: 1px solid palette(mid); border-radius: 8px; }
QFrame#warncard { border: 1px solid #d9a441; border-radius: 8px; background: rgba(217,164,65,0.10); }
QPushButton { padding: 6px 14px; }
QPushButton#primary { font-weight: 600; padding: 9px 22px; }
QPushButton#link { border: none; background: transparent; color: palette(link); padding: 2px 0;
                   text-align: left; }
QPushButton#link:hover { text-decoration: underline; }
QLineEdit { padding: 6px 8px; }
"""


# ---------------------------------------------------------------- helpers

def open_path(path):
    """Open a file or folder with its default app (FL Studio for .flp)."""
    if sys.platform == "win32":
        os.startfile(path)
    else:
        subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", path])


def show_in_folder(path):
    if sys.platform == "win32" and os.path.isfile(path):
        subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
    else:
        open_path(path if os.path.isdir(path) else os.path.dirname(path))


def launch(exe):
    subprocess.Popen([exe], cwd=os.path.dirname(exe))


def label(text="", obj=None, wrap=True):
    lbl = QLabel(text)
    lbl.setWordWrap(wrap)
    lbl.setTextFormat(Qt.RichText)
    lbl.setOpenExternalLinks(True)
    lbl.setTextInteractionFlags(Qt.TextBrowserInteraction)
    if obj:
        lbl.setObjectName(obj)
    return lbl


def button(text, on_click=None, primary=False, link=False, tip=None):
    b = QPushButton(text)
    if primary:
        b.setObjectName("primary")
    if link:
        b.setObjectName("link")
        b.setCursor(Qt.PointingHandCursor)
    if tip:
        b.setToolTip(tip)
    if on_click:
        b.clicked.connect(on_click)
    return b


def copy_button(get_text, text="Copy path"):
    """Button that copies get_text() and briefly says so."""
    b = QPushButton(text)
    b.setMinimumWidth(b.fontMetrics().horizontalAdvance("Copied!") + 40)

    def copy():
        QGuiApplication.clipboard().setText(get_text())
        b.setText("Copied!")
        QTimer.singleShot(1500, lambda: b.setText(text))

    b.clicked.connect(copy)
    return b


def card(*items, obj="card"):
    frame = QFrame()
    frame.setObjectName(obj)
    lay = QVBoxLayout(frame)
    lay.setContentsMargins(16, 14, 16, 14)
    lay.setSpacing(8)
    for w in items:
        if isinstance(w, QWidget):
            lay.addWidget(w)
        elif w is not None:
            lay.addLayout(w)
    return frame


def hrow(*widgets, stretch=True):
    lay = QHBoxLayout()
    lay.setSpacing(8)
    for w in widgets:
        if w is not None:
            lay.addWidget(w)
    if stretch:
        lay.addStretch(1)
    return lay


def status_icon(state):
    """✓ (done), ! (needs attention) or ○ (not yet)."""
    text, color = {"ok": ("✓", GREEN), "warn": ("!", ORANGE), "todo": ("○", GREY)}[state]
    return f"<span style='color:{color}; font-size:15pt; font-weight:700'>{text}</span>"


class Page(QWidget):
    def __init__(self, title, subtitle=""):
        super().__init__()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(30, 26, 30, 20)
        outer.setSpacing(6)
        self.title = label(title, "title")
        self.subtitle = label(subtitle, "subtitle")
        outer.addWidget(self.title)
        outer.addWidget(self.subtitle)
        outer.addSpacing(12)
        self.body = QVBoxLayout()
        self.body.setSpacing(12)
        outer.addLayout(self.body, 1)
        self.footer = QHBoxLayout()
        self.footer.setSpacing(8)
        outer.addLayout(self.footer)

    def shown(self):
        """Called every time the page is shown."""


class Worker(QObject):
    """Runs a function on a background thread so the window stays responsive."""
    done = Signal(object)
    failed = Signal(str)

    def __init__(self, fn, *args):
        super().__init__()
        self.fn, self.args = fn, args

    def run(self):
        try:
            self.done.emit(self.fn(*self.args))
        except build_flp.SetupMissing as e:
            self.failed.emit(str(e))
        except Exception:
            self.failed.emit(traceback.format_exc())


# ------------------------------------------------------------------ 1. set up

class CheckRow(QWidget):
    """One line of the setup checklist: status icon, text, and buttons."""

    def __init__(self, title):
        super().__init__()
        lay = QGridLayout(self)
        lay.setContentsMargins(0, 4, 0, 4)
        lay.setHorizontalSpacing(14)
        self.icon = label()
        self.icon.setFixedWidth(22)
        self.icon.setAlignment(Qt.AlignTop | Qt.AlignHCenter)
        self.text = label()
        self.buttons = QHBoxLayout()
        self.buttons.setSpacing(8)
        lay.addWidget(self.icon, 0, 0, 2, 1)
        lay.addWidget(label(f"<b>{title}</b>"), 0, 1)
        lay.addWidget(self.text, 1, 1)
        lay.addLayout(self.buttons, 0, 2, 2, 1)
        lay.setColumnStretch(1, 1)

    def set(self, state, text, *buttons):
        self.icon.setText(status_icon(state))
        self.text.setText(text)
        while self.buttons.count():
            w = self.buttons.takeAt(0).widget()
            if w:
                w.setParent(None)
        for b in buttons:
            self.buttons.addWidget(b)


class SetupPage(Page):
    def __init__(self, app):
        super().__init__("Get set up", "You need three free or already-installed programs. "
                                       "The app checks for them by itself.")
        self.app = app
        self.fl = CheckRow("FL Studio")
        self.sfz = CheckRow("sforzando")
        self.vgm = CheckRow("VGMTrans")
        self.body.addWidget(card(self.fl, self.sfz, self.vgm))
        self.first_song = label()
        self.body.addWidget(card(self.first_song))
        self.body.addStretch(1)
        self.footer.addStretch(1)
        self.footer.addWidget(button("Continue →", self.next, primary=True))

    def shown(self):
        fl = setup_check.find_fl_studio()
        if fl:
            self.fl.set("ok", "Found on this PC.")
        else:
            self.fl.set("warn", "Not found. You need FL Studio to open the projects.",
                        button("Get FL Studio", lambda: QDesktopServices.openUrl(QUrl(FL_URL))),
                        button("Check again", self.shown))
        if setup_check.find_sforzando():
            self.sfz.set("ok", "Installed. It plays the game's instruments inside FL Studio.")
        else:
            self.sfz.set("warn", "Not installed yet. Download it (free), install it, then in FL Studio go "
                                 "to <b>Options → Manage plugins → Find plugins</b>.",
                         button("Download sforzando", lambda: QDesktopServices.openUrl(QUrl(SFORZANDO_URL))),
                         button("Check again", self.shown))
        vgm = setup_check.vgmtrans_path()
        if vgm:
            self.vgm.set("ok", "Ready. It pulls the music out of your game.",
                         button("Change…", self.find_vgmtrans, link=True))
        else:
            self.vgm.set("todo", "Pulls the music out of your game. Download it (free), unzip it, then "
                                 "show the app where <b>VGMTrans.exe</b> is so it can open it for you.",
                         button("Download VGMTrans", lambda: QDesktopServices.openUrl(QUrl(VGMTRANS_URL))),
                         button("Find VGMTrans.exe…", self.find_vgmtrans))
        if pipeline.can_generate():
            self.first_song.setText(f"{status_icon('ok')}&nbsp; <b>FL Studio is set up.</b> The app builds "
                                    "your projects by itself; no extra steps in FL.")
        else:
            self.first_song.setText(f"{status_icon('todo')}&nbsp; <b>Your first song</b> needs one short "
                                    "setup in FL Studio (the app walks you through it). After that, every "
                                    "song is built for you.")

    def find_vgmtrans(self):
        path, _ = QFileDialog.getOpenFileName(self, "Where is VGMTrans.exe?", "",
                                              "VGMTrans (VGMTrans*.exe);;Programs (*.exe)")
        if path:
            setup_check.save_setting("vgmtrans", path)
            self.shown()

    def next(self):
        setup_check.save_setting("welcomed", True)
        self.app.go(1)


# ------------------------------------------------------------------ 2. pick a song

class PickPage(Page):
    def __init__(self, app):
        super().__init__("Pick a song", "Songs you export from VGMTrans show up here by themselves.")
        self.app = app
        self.rips, self.seen = [], None

        # song list
        self.songs = QListWidget()
        self.songs.setAlternatingRowColors(True)
        self.songs.currentRowChanged.connect(self.song_selected)
        self.songs.itemDoubleClicked.connect(lambda _: self.start())
        self.sf2_combo = QComboBox()
        self.sf2_row = QWidget()
        sr = QHBoxLayout(self.sf2_row)
        sr.setContentsMargins(0, 0, 0, 0)
        sr.addWidget(QLabel("Instrument file:"))
        sr.addWidget(self.sf2_combo, 1)
        self.sf2_row.hide()
        self.change_sf2 = button("Wrong instruments? Pick a different instrument file", link=True,
                                 on_click=lambda: (self.sf2_row.show(), self.change_sf2.hide()))
        self.list_card = card(label("<b>Your songs</b> (newest first)"), self.songs,
                              hrow(self.change_sf2), self.sf2_row)
        self.body.addWidget(self.list_card, 1)

        # how-to (always available; open when there are no songs yet)
        self.howto_toggle = button("", link=True, on_click=self.toggle_howto)
        self.howto = QWidget()
        hl = QVBoxLayout(self.howto)
        hl.setContentsMargins(0, 4, 0, 0)
        hl.addWidget(label(
            "<ol style='margin-left:-20px; line-height:150%'>"
            "<li>Open <b>VGMTrans</b> and drag your game file into it.</li>"
            "<li>In the <b>bottom list</b>, click the song's <b>red note icon</b>. "
            "Not sure it's the right one? Press <b>▶</b> under the list to hear it.</li>"
            "<li>Click <b>Convert</b> at the top (it can take <b>3 clicks</b> to open), choose "
            "<b>Export as MIDI and SF2</b>, and save it in the app's <b>rips</b> folder.</li></ol>"))
        self.open_vgm = button("Open VGMTrans", self.open_vgmtrans)
        hl.addLayout(hrow(self.open_vgm,
                          copy_button(lambda: os.path.normpath(pipeline.RIPS_DIR), "Copy rips folder path"),
                          button("Open rips folder", lambda: open_path(pipeline.RIPS_DIR))))
        self.body.addWidget(card(hrow(self.howto_toggle), self.howto))

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setTextVisible(False)
        self.progress.hide()
        self.status = label("", "subtitle")
        self.body.addWidget(self.progress)
        self.body.addWidget(self.status)

        self.footer.addWidget(button("← Back", lambda: app.go(0)))
        self.footer.addStretch(1)
        self.go_btn = button("Use this song →", self.start, primary=True)
        self.footer.addWidget(self.go_btn)

        os.makedirs(pipeline.RIPS_DIR, exist_ok=True)
        self.watcher = QFileSystemWatcher([pipeline.RIPS_DIR])
        self.watcher.directoryChanged.connect(lambda _: QTimer.singleShot(800, self.refresh))
        self.refresh()
        self.set_howto(not self.rips)

    def shown(self):
        self.open_vgm.setEnabled(bool(setup_check.vgmtrans_path()))
        self.open_vgm.setToolTip("" if self.open_vgm.isEnabled() else
                                 "Show the app where VGMTrans is, on the Set up page")
        self.refresh()

    def set_howto(self, open_):
        self.howto.setVisible(open_)
        self.howto_toggle.setText(("▾ " if open_ else "▸ ") + "How do I get a song out of a game?")

    def toggle_howto(self):
        self.set_howto(not self.howto.isVisible())

    def open_vgmtrans(self):
        exe = setup_check.vgmtrans_path()
        if exe:
            launch(exe)

    def refresh(self):
        row = self.songs.currentRow()
        current = self.rips[row]["midi"] if 0 <= row < len(self.rips) else None
        self.rips = pipeline.find_rips()
        dirs = {os.path.dirname(r["midi"]) for r in self.rips} | {pipeline.RIPS_DIR}
        missing = [d for d in dirs if d not in self.watcher.directories()]
        if missing:
            self.watcher.addPaths(missing)

        new = None
        if self.seen is not None:
            fresh = [r for r in self.rips if r["midi"] not in self.seen]
            new = fresh[0]["midi"] if fresh else None
        self.seen = {r["midi"] for r in self.rips}

        self.songs.blockSignals(True)
        self.songs.clear()
        select = 0
        for i, r in enumerate(self.rips):
            rel = os.path.relpath(os.path.dirname(r["midi"]), pipeline.RIPS_DIR)
            where = "" if rel == "." else f"{rel}  ·  "
            tag = "   ★ NEW" if r["midi"] == new else ""
            item = QListWidgetItem(f"{r['song']}{tag}\n{where}saved {r['when']}")
            item.setSizeHint(QSize(0, self.songs.fontMetrics().height() * 2 + 14))
            self.songs.addItem(item)
            if r["midi"] == (new or current):
                select = i
        self.songs.blockSignals(False)

        self.list_card.setVisible(bool(self.rips))
        if self.rips:
            self.songs.setCurrentRow(select)
        if new:
            self.status.setText(f"<span style='color:{GREEN}'>New song found: <b>"
                                f"{os.path.splitext(os.path.basename(new))[0]}</b></span>")
        self.song_selected(self.songs.currentRow())
        if not self.rips:
            self.set_howto(True)

    def song_selected(self, i):
        self.sf2_combo.clear()
        ok = 0 <= i < len(self.rips)
        if ok:
            r = self.rips[i]
            for opt in r["sf2_options"]:
                self.sf2_combo.addItem(os.path.relpath(opt, pipeline.RIPS_DIR), opt)
            if r["sf2"]:
                self.sf2_combo.setCurrentIndex(r["sf2_options"].index(r["sf2"]))
        has_sf2 = self.sf2_combo.count() > 0
        self.change_sf2.setVisible(ok and self.sf2_combo.count() > 1 and not self.sf2_row.isVisible())
        if ok and not has_sf2:
            self.status.setText(f"<span style='color:{ORANGE}'>This song's instrument file (.sf2) is "
                                "missing. Export it again with <b>Export as MIDI and SF2</b>.</span>")
        self.go_btn.setEnabled(ok and has_sf2)

    def start(self):
        i = self.songs.currentRow()
        if not (0 <= i < len(self.rips)) or self.sf2_combo.count() == 0:
            return
        midi, sf2 = self.rips[i]["midi"], self.sf2_combo.currentData()
        self.go_btn.setEnabled(False)
        self.progress.show()
        self.status.setText("Getting the instruments ready to listen to…")
        out_dir = pipeline.new_run_dir(self.rips[i]["song"])
        self.app.run_dir = out_dir
        self.app.result = None
        self.app.run_in_background(pipeline.prepare, (midi, sf2, out_dir, lambda *a: None),
                                   self.finished, self.failed)

    def finished(self, state):
        self.progress.hide()
        self.status.setText("")
        self.go_btn.setEnabled(True)
        self.app.state = state
        self.app.pages[2].load(state)
        self.app.go(2)

    def failed(self, msg):
        self.progress.hide()
        self.status.setText("")
        self.go_btn.setEnabled(True)
        self.app.error("That song couldn't be read",
                       "Try exporting it again from VGMTrans with Export as MIDI and SF2.", msg)


# ------------------------------------------------------------------ 3. name instruments

class Player(QMediaPlayer):
    def __init__(self):
        super().__init__()
        self.out = QAudioOutput()
        self.setAudioOutput(self.out)
        self.current = None
        self.playbackStateChanged.connect(self._state)

    def play_row(self, r):
        if self.current and self.current is not r:
            self.current.set_playing(False)
        self.current = r
        self.setSource(QUrl.fromLocalFile(r.preview_path))
        self.play()

    def _state(self, st):
        if self.current:
            self.current.set_playing(st == QMediaPlayer.PlayingState)


class NameEdit(QLineEdit):
    focused = Signal()

    def focusInEvent(self, e):
        super().focusInEvent(e)
        self.focused.emit()


class InstrumentRow(QFrame):
    changed = Signal()

    def __init__(self, inst, preview_path, player, page):
        super().__init__()
        self.setObjectName("card")
        self.inst, self.preview_path, self.player, self.page = inst, preview_path, player, page
        lay = QGridLayout(self)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setHorizontalSpacing(14)
        self.play_btn = QPushButton("▶")
        self.play_btn.setFixedSize(46, 38)
        self.play_btn.setToolTip("Listen")
        self.play_btn.clicked.connect(self.toggle)
        chans = inst["channels"]
        where = (f"channel {chans[0]}" if len(chans) == 1 else
                 "channels " + ", ".join(map(str, chans[:-1])) + f" and {chans[-1]}")
        title = f"<b>Instrument {inst['program']}</b>" + (f" (bank {inst['bank']})" if inst["bank"] else "")
        self.edit = NameEdit(inst.get("remembered") or "")
        self.edit.setPlaceholderText("What does it sound like? e.g. Strings, Snare, Bass")
        self.edit.setMaxLength(40)
        self.edit.focused.connect(self.on_focus)
        self.edit.textChanged.connect(self.update_badge)
        self.edit.textChanged.connect(self.changed)
        self.badge = label()
        self.badge.setFixedWidth(120)
        lay.addWidget(self.play_btn, 0, 0, 2, 1)
        lay.addWidget(label(title), 0, 1)
        lay.addWidget(label(f"Plays on {where}", "subtitle"), 1, 1)
        lay.addWidget(self.edit, 0, 2, 2, 1)
        lay.addWidget(self.badge, 0, 3, 2, 1)
        lay.setColumnStretch(2, 1)
        lay.setColumnMinimumWidth(1, 210)
        if inst.get("missing"):
            self.play_btn.setEnabled(False)
        self.update_badge()

    def on_focus(self):
        if self.page.quiet_focus:
            return
        if self.page.autoplay.isChecked() and not self.inst.get("missing"):
            self.player.play_row(self)

    def update_badge(self):
        remembered, text = self.inst.get("remembered"), self.edit.text().strip()
        if self.inst.get("missing"):
            msg = f"<span style='color:{ORANGE}'>Not in the instrument file; will be silent</span>"
        elif remembered and text == remembered:
            msg = f"<span style='color:{GREEN}'>✓ Named before</span>"
        elif remembered and not text:
            msg = f"<span style='color:{ORANGE}'>Name will be forgotten</span>"
        elif remembered:
            msg = f"<span style='color:{ORANGE}'>Renamed</span>"
        else:
            msg = ""
        self.badge.setText(msg)

    def toggle(self):
        if self.player.current is self and self.player.playbackState() == QMediaPlayer.PlayingState:
            self.player.stop()
        else:
            self.player.play_row(self)

    def set_playing(self, playing):
        self.play_btn.setText("■" if playing else "▶")
        self.play_btn.setToolTip("Stop" if playing else "Listen")


class NamePage(Page):
    def __init__(self, app):
        super().__init__("Name the instruments",
                         "Click a box to hear the instrument, then type what it sounds like. These "
                         "become your FL Studio channel names. Not sure? Leave it blank.")
        self.app = app
        self.player = Player()
        self.rows = []
        self.quiet_focus = False
        top = QHBoxLayout()
        self.count = label("<b>0 of 0 named</b>")
        self.autoplay = QCheckBox("Play when I click a box")
        self.autoplay.setChecked(setup_check.load_settings().get("autoplay", True))
        self.autoplay.toggled.connect(lambda v: setup_check.save_setting("autoplay", v))
        top.addWidget(self.count)
        top.addStretch(1)
        top.addWidget(self.autoplay)
        self.body.addLayout(top)
        self.banner = label()
        self.banner.hide()
        self.body.addWidget(self.banner)

        self.list_widget = QWidget()
        self.list_layout = QVBoxLayout(self.list_widget)
        self.list_layout.setSpacing(8)
        self.list_layout.setContentsMargins(0, 0, 8, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidget(self.list_widget)
        self.body.addWidget(scroll, 1)
        self.body.addWidget(label("<b>Enter</b> moves to the next box. <b>Ctrl+Space</b> plays it again.",
                                  "subtitle"))

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setTextVisible(False)
        self.progress.hide()
        self.body.addWidget(self.progress)
        self.footer.addWidget(button("← Back", lambda: (self.player.stop(), app.go(1))))
        self.footer.addStretch(1)
        self.save_btn = button("Build my FL project →", self.save, primary=True)
        self.footer.addWidget(self.save_btn)
        QShortcut(QKeySequence("Ctrl+Space"), self, activated=self.play_focused)

    def load(self, state):
        self.player.stop()
        for r in self.rows:
            r.setParent(None)
        self.rows = []
        while self.list_layout.count():
            self.list_layout.takeAt(0)
        for inst in state["instruments"]:
            r = InstrumentRow(inst, os.path.join(self.app.run_dir, inst["preview"]), self.player, self)
            r.changed.connect(self.update_count)
            self.rows.append(r)
            self.list_layout.addWidget(r)
        self.list_layout.addStretch(1)
        for a, b in zip(self.rows, self.rows[1:]):
            a.edit.returnPressed.connect(b.edit.setFocus)
        if self.rows:
            self.rows[-1].edit.returnPressed.connect(self.save_btn.setFocus)
        known = sum(1 for r in self.rows if r.inst.get("remembered"))
        if self.rows and known == len(self.rows):
            self.banner.setText(f"{status_icon('ok')}&nbsp; Every instrument in this song was named before. "
                                "Change any you like, or go straight to building.")
            self.banner.show()
        elif known:
            self.banner.setText(f"{status_icon('ok')}&nbsp; {known} instrument(s) were named before and are "
                                "filled in already.")
            self.banner.show()
        else:
            self.banner.hide()
        self.update_count()

    def shown(self):
        first_empty = next((r for r in self.rows if not r.edit.text()), None)
        self.quiet_focus = True
        (first_empty.edit if first_empty else self.save_btn).setFocus()
        QTimer.singleShot(0, lambda: setattr(self, "quiet_focus", False))

    def update_count(self):
        named = sum(1 for r in self.rows if r.edit.text().strip())
        self.count.setText(f"<b>{named} of {len(self.rows)} named</b>")

    def play_focused(self):
        for r in self.rows:
            if r.edit.hasFocus():
                r.toggle()

    def save(self):
        self.player.stop()
        names = {r.inst["key"]: r.edit.text() for r in self.rows}
        self.save_btn.setEnabled(False)
        self.save_btn.setText("Building…")
        self.progress.show()
        self.app.run_in_background(self._finalize_and_build, (self.app.run_dir, names),
                                   self.finished, self.failed)

    @staticmethod
    def _finalize_and_build(run_dir, names):
        result = pipeline.finalize(run_dir, names)
        if pipeline.can_generate():
            result["built"] = pipeline.generate(run_dir)
        return result

    def _reset(self):
        self.progress.hide()
        self.save_btn.setEnabled(True)
        self.save_btn.setText("Build my FL project →")

    def finished(self, result):
        self._reset()
        self.app.result = result
        self.app.pages[3].load(result, first_time=not result.get("built"))
        if result.get("built"):
            self.app.show_done(result["built"])
        else:
            self.app.go(3)

    def failed(self, msg):
        self._reset()
        self.app.error("Couldn't build the project", "Something went wrong while making the FL files.", msg)


# ------------------------------------------------------------------ 4. FL setup (once)

class Step(QWidget):
    """A numbered instruction with optional buttons under it."""

    def __init__(self, number):
        super().__init__()
        lay = QGridLayout(self)
        lay.setContentsMargins(0, 2, 0, 2)
        lay.setHorizontalSpacing(12)
        num = label(f"<span style='font-size:13pt; font-weight:700'>{number}</span>")
        num.setFixedWidth(22)
        num.setAlignment(Qt.AlignTop | Qt.AlignHCenter)
        self.text = label()
        self.buttons = QHBoxLayout()
        self.buttons.setSpacing(8)
        self.buttons.addStretch(1)
        lay.addWidget(num, 0, 0, 2, 1)
        lay.addWidget(self.text, 0, 1)
        lay.addLayout(self.buttons, 1, 1)
        lay.setColumnStretch(1, 1)

    def set(self, text, *buttons):
        self.text.setText(text)
        while self.buttons.count() > 1:
            w = self.buttons.takeAt(0).widget()
            if w:
                w.setParent(None)
        for i, b in enumerate(buttons):
            self.buttons.insertWidget(i, b)


class FLPage(Page):
    def __init__(self, app):
        super().__init__("One-time setup in FL Studio", "")
        self.app = app
        self.steps = [Step(i) for i in range(1, 7)]
        self.body.addWidget(card(*self.steps))
        self.status = label()
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setTextVisible(False)
        self.body.addWidget(card(self.status, self.progress))
        self.body.addStretch(1)

        self.footer.addWidget(button("← Back", lambda: app.go(2)))
        self.footer.addWidget(button("Choose the project myself…", self.pick, link=True))
        self.footer.addStretch(1)
        self.build_btn = button("Build my FL project →", lambda: self.build(self.project), primary=True)
        self.footer.addWidget(self.build_btn)

        os.makedirs(pipeline.INPUT_DIR, exist_ok=True)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.poll)
        self.song, self.project, self.since, self.last, self.busy = None, None, 0, None, False

    def load(self, result, first_time):
        self.first_time = first_time
        self.song = self.app.state["song"] if self.app.state else ""
        split_mid = os.path.normpath(result["split_mid"])
        mid_dir, mid_name = os.path.dirname(split_mid), os.path.basename(split_mid)
        tracks = pipeline.manifest_tracks(self.app.run_dir)
        channel, sfz = next(iter(tracks.items())) if tracks else ("the first channel", "")
        needs_sforzando = not build_flp.has_donor()

        if first_time:
            self.title.setText("One-time setup in FL Studio")
            self.subtitle.setText("Do this once. The app learns from it and builds every song after this "
                                  "by itself. As soon as you save the project, the app picks it up.")
        else:
            self.title.setText("Build through FL's import")
            self.subtitle.setText("Same result, made by FL Studio's own MIDI import. The app compares it "
                                  "with its direct build and tells you if anything differs.")

        fl = setup_check.find_fl_studio()
        self.steps[0].set("Open <b>FL Studio</b> and start a new project (<b>File → New</b>).",
                          *( [button("Open FL Studio", lambda: launch(fl))] if fl else []))
        self.steps[1].set(f"Go to <b>File → Import → MIDI file</b> and pick <b>{mid_name}</b>. "
                          "Paste the folder path into the box at the top of the window to get there fast.",
                          copy_button(lambda: mid_dir, "Copy folder path"))
        self.steps[2].set("In the window that pops up, set <b>Channel type</b> to <b>MIDI Out</b>, tick "
                          "<b>Create one channel per track</b>, and click <b>Accept</b>.")
        self.steps[3].set("In the <b>Playlist</b>, click the small icon at the top-left of the song's clip "
                          "and choose <b>Split by channel</b>. (Don't arrange anything; the app does that.)")
        self.steps[4].set(f"In the <b>Channel Rack</b>, right-click <b>{channel}</b> → <b>Replace</b> → "
                          f"<b>sforzando</b>. Then drag <b>{os.path.basename(sfz)}</b> onto sforzando.",
                          button("Show that file", lambda: show_in_folder(sfz)))
        self.steps[4].setVisible(needs_sforzando)
        save_n = 6 if needs_sforzando else 5
        self.steps[5].set(f"<b>File → Save as</b>, go to the app's <b>input</b> folder, and save it as "
                          f"<b>{self.song}</b>.",
                          copy_button(lambda: os.path.normpath(pipeline.INPUT_DIR), "Copy folder path"))
        self.steps[5].layout().itemAtPosition(0, 0).widget().setText(
            f"<span style='font-size:13pt; font-weight:700'>{save_n}</span>")

        self.since, self.last, self.busy = time.time(), None, False
        self.progress.show()
        self.status.setText("Waiting for you to save the project in FL Studio…")
        self.project = None
        self.build_btn.setEnabled(False)

    def shown(self):
        self.timer.start(1500)

    def hideEvent(self, e):
        self.timer.stop()
        super().hideEvent(e)

    def poll(self):
        if self.busy:
            return
        p = pipeline.newest_input(self.song)
        if not p:
            return
        st = os.stat(p)
        if st.st_mtime >= self.since - 1:
            # build as soon as the file has stopped changing (FL finished writing it)
            sig = (p, st.st_size, st.st_mtime)
            if sig == self.last:
                self.status.setText(f"{status_icon('ok')}&nbsp; Got it: <b>{os.path.basename(p)}</b>. Building…")
                self.build(p)
            self.last = sig
        elif self.project != p:
            self.project = p
            when = datetime.fromtimestamp(st.st_mtime).strftime("%b %d %H:%M")
            self.status.setText(f"Waiting for you to save the project… (Or use the one saved {when}: "
                                f"<b>{os.path.basename(p)}</b>, with the button below.)")
            self.build_btn.setEnabled(True)

    def pick(self):
        path, _ = QFileDialog.getOpenFileName(self, "Choose the FL project you saved",
                                              pipeline.INPUT_DIR, "FL Studio projects (*.flp)")
        if path:
            self.build(path)

    def build(self, path):
        if not path or self.busy:
            return
        self.busy = True
        self.build_btn.setEnabled(False)
        self.progress.show()
        self.app.run_in_background(pipeline.build, (self.app.run_dir, path), self.finished, self.failed)

    def finished(self, res):
        self.busy = False
        self.progress.hide()
        self.app.pages[0].shown()
        self.app.show_done(res)

    def failed(self, msg):
        self.busy = False
        self.build_btn.setEnabled(True)
        self.status.setText("Waiting for you to save the project in FL Studio…")
        self.app.error("Couldn't build the project",
                       "Check that you saved the project right after the steps above.", msg)


# ------------------------------------------------------------------ 5. done

class DonePage(Page):
    def __init__(self, app):
        super().__init__("Your project is ready", "")
        self.app = app
        self.summary = label("", "big")
        self.body.addWidget(card(self.summary))
        self.warn = label()
        self.warn_card = card(self.warn, obj="warncard")
        self.body.addWidget(self.warn_card)

        self.details_toggle = button("▸ Details", self.toggle_details, link=True)
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        self.details.setFont(QFont("Consolas", 9))
        self.details.hide()
        self.body.addWidget(self.details_toggle)
        self.body.addWidget(self.details, 1)
        self.body.addStretch(1)
        self.recheck = button("Something sound off? Rebuild it through FL's own import", link=True,
                              on_click=self.via_fl)
        self.body.addWidget(self.recheck)

        self.auto_open = QCheckBox("Open FL Studio by itself next time")
        self.auto_open.setChecked(setup_check.load_settings().get("auto_open", False))
        self.auto_open.toggled.connect(lambda v: setup_check.save_setting("auto_open", v))
        self.footer.addWidget(self.auto_open)
        self.footer.addStretch(1)
        self.footer.addWidget(button("Do another song", lambda: app.go(1)))
        self.footer.addWidget(button("Show in folder", lambda: show_in_folder(self.out)))
        self.footer.addWidget(button("Open in FL Studio", lambda: open_path(self.out), primary=True))
        self.out = ""

    def toggle_details(self):
        self.details.setVisible(not self.details.isVisible())
        self.details_toggle.setText(("▾ " if self.details.isVisible() else "▸ ") + "Details")

    def via_fl(self):
        if self.app.result:
            self.app.pages[3].load(self.app.result, first_time=False)
            self.app.go(3, show_fl_step=True)

    def load(self, res):
        self.out = os.path.normpath(res["out"])
        n = res["changed"]
        text = (f"{status_icon('ok')}&nbsp; <b>{n} instrument channel{'s' if n != 1 else ''}</b> ready on "
                "sforzando, and every pattern starts at bar 1.<br>"
                f"<span style='font-size:9.5pt; color:{GREY}'>Saved as {self.out}</span>")
        self.summary.setText(text)
        warnings = []
        if res.get("skipped"):
            warnings.append("These channels had no matching instrument and were left as they were: "
                            + ", ".join(res["skipped"]) + ".")
        if res.get("risks"):
            warnings.append("This song uses things the app hasn't been double-checked on yet ("
                            + "; ".join(res["risks"]) + "). It's probably fine. If anything sounds off, "
                            "use the link below to rebuild it through FL's own import; the app will "
                            "compare the two for you.")
        check = res.get("check")
        if check is not None:
            if check:
                warnings.append(f"The app's own build differs from FL's import in {len(check)} place(s). "
                                "Use this project (made from FL's import), and please report the details.")
            else:
                text += (f"<br>{status_icon('ok')}&nbsp; Double-checked: the app's own build matches "
                         "FL's import exactly.")
                self.summary.setText(text)
        self.warn.setText("<br><br>".join(f"<b>Heads-up:</b> {w}" for w in warnings))
        self.warn_card.setVisible(bool(warnings))
        self.details.setPlainText("\n".join(res["lines"] + [f"- {c}" for c in (check or [])]))
        self.recheck.setVisible(check is None)
        if self.auto_open.isChecked():
            open_path(self.out)


# ------------------------------------------------------------------ window

class MainWindow(QMainWindow):
    STEPS = ["Set up", "Pick a song", "Name instruments", "FL setup (once)", "Done"]

    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.resize(1000, 700)
        self.setMinimumSize(820, 600)
        self.run_dir, self.state, self.result = None, None, None
        self.threads, self.fl_step_wanted = [], False

        central = QWidget()
        lay = QHBoxLayout(central)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.steps = QListWidget()
        self.steps.setObjectName("steps")
        self.steps.setFixedWidth(200)
        self.steps.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        for s in self.STEPS:
            self.steps.addItem(QListWidgetItem(s))
        self.steps.currentRowChanged.connect(self._step_clicked)
        line = QFrame()
        line.setFrameShape(QFrame.VLine)
        line.setFrameShadow(QFrame.Sunken)
        self.stack = QStackedWidget()
        self.pages = [SetupPage(self), PickPage(self), NamePage(self), FLPage(self), DonePage(self)]
        for p in self.pages:
            self.stack.addWidget(p)
        lay.addWidget(self.steps)
        lay.addWidget(line)
        lay.addWidget(self.stack, 1)
        self.setCentralWidget(central)

        # Returning users with everything installed start at "Pick a song"
        ready = (setup_check.load_settings().get("welcomed") and setup_check.find_fl_studio()
                 and setup_check.find_sforzando())
        self.go(1 if ready else 0)

    def unlocked(self, i):
        return {0: True, 1: True, 2: self.state is not None,
                3: self.result is not None, 4: bool(self.pages[4].out)}[i]

    def go(self, i, show_fl_step=False):
        if show_fl_step:
            self.fl_step_wanted = True
        if i in (0, 1, 2):
            self.fl_step_wanted = self.fl_step_wanted and i != 1
        # The FL step only shows until the app can build projects by itself, or when asked for
        self.steps.setRowHidden(3, pipeline.can_generate() and not self.fl_step_wanted and i != 3)
        for k in range(len(self.STEPS)):
            enabled = self.unlocked(k) or k == i
            self.steps.item(k).setFlags(Qt.ItemIsSelectable | Qt.ItemIsEnabled if enabled else Qt.NoItemFlags)
        self.steps.blockSignals(True)
        self.steps.setCurrentRow(i)
        self.steps.blockSignals(False)
        self.stack.setCurrentIndex(i)
        self.pages[i].shown()

    def show_done(self, res):
        self.pages[4].load(res)
        self.go(4)

    def _step_clicked(self, i):
        if i >= 0 and self.unlocked(i):
            self.go(i)

    def run_in_background(self, fn, args, on_done, on_fail):
        thread = QThread(self)
        worker = Worker(fn, *args)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.done.connect(on_done)
        worker.failed.connect(on_fail)
        worker.done.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.finished.connect(lambda: self.threads.remove((thread, worker)))
        self.threads.append((thread, worker))
        thread.start()

    def error(self, title, friendly, details=""):
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle(title)
        if details and "Traceback" not in details:
            friendly = details  # a message written for people (e.g. setup missing)
            details = ""
        box.setText(friendly)
        if details:
            box.setInformativeText("If it keeps happening, click Show Details and include that text "
                                   "when you report it.")
            box.setDetailedText(details)
        box.exec()


def main():
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setStyle("Fusion")
    app.setStyleSheet(STYLE)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
