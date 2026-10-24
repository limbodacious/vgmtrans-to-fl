"""vgmtrans-to-fl desktop app.

Walks you from a VGMTrans rip (.mid + .sf2) to an FL Studio project where every
channel is sforzando with the right instrument, and lets you name each
instrument by ear. Names are remembered for the next time the same instrument
shows up, in any song or game.

Run:  python app.py      (needs PySide6: pip install -r requirements.txt)
"""
import os
import subprocess
import sys
import traceback

from PySide6.QtCore import QFileSystemWatcher, QObject, Qt, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QFont, QGuiApplication, QKeySequence, QShortcut
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import (QApplication, QComboBox, QFileDialog, QFrame, QGridLayout, QHBoxLayout,
                               QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow,
                               QMessageBox, QPlainTextEdit, QProgressBar, QPushButton,
                               QScrollArea, QSizePolicy, QStackedWidget, QVBoxLayout, QWidget)

import build_flp
import names as name_memory
import pipeline

APP_NAME = "vgmtrans-to-fl"
VGMTRANS_URL = "https://github.com/vgmtrans/vgmtrans/releases"
SFORZANDO_URL = "https://www.plogue.com/products/sforzando.html"

STYLE = """
QWidget { font-size: 10.5pt; }
QListWidget#steps { border: none; padding: 12px 0; font-size: 10.5pt; }
QListWidget#steps::item { padding: 10px 12px; border-radius: 6px; margin: 2px 8px; }
QListWidget#steps::item:selected { background: palette(highlight); color: palette(highlighted-text); }
QLabel#title { font-size: 17pt; font-weight: 600; }
QLabel#subtitle { color: palette(placeholder-text); }
QFrame#card { border: 1px solid palette(mid); border-radius: 8px; }
QLabel#badge { color: #2e7d32; font-weight: 600; }
QLabel#warn { color: #b26a00; font-weight: 600; }
QPushButton { padding: 6px 14px; }
QPushButton#primary { font-weight: 600; padding: 8px 18px; }
QLineEdit { padding: 5px 8px; }
"""


# ---------------------------------------------------------------- helpers

def open_path(path):
    """Open a file or folder with the system's default app (FL Studio for .flp)."""
    if sys.platform == "win32":
        os.startfile(path)
    else:
        subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", path])


def show_in_folder(path):
    if sys.platform == "win32" and os.path.isfile(path):
        subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
    else:
        open_path(path if os.path.isdir(path) else os.path.dirname(path))


def label(text, obj=None, wrap=True):
    lbl = QLabel(text)
    lbl.setWordWrap(wrap)
    lbl.setTextFormat(Qt.RichText)
    lbl.setOpenExternalLinks(True)
    lbl.setTextInteractionFlags(Qt.TextBrowserInteraction)
    if obj:
        lbl.setObjectName(obj)
    return lbl


def card(*widgets):
    frame = QFrame()
    frame.setObjectName("card")
    lay = QVBoxLayout(frame)
    lay.setContentsMargins(16, 14, 16, 14)
    for w in widgets:
        if isinstance(w, QWidget):
            lay.addWidget(w)
        else:
            lay.addLayout(w)
    return frame


def row(*widgets, stretch_end=True):
    lay = QHBoxLayout()
    for w in widgets:
        lay.addWidget(w)
    if stretch_end:
        lay.addStretch(1)
    return lay


class Page(QWidget):
    def __init__(self, title, subtitle=""):
        super().__init__()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(28, 24, 28, 20)
        outer.setSpacing(6)
        outer.addWidget(label(title, "title"))
        if subtitle:
            outer.addWidget(label(subtitle, "subtitle"))
        outer.addSpacing(10)
        self.body = QVBoxLayout()
        self.body.setSpacing(12)
        outer.addLayout(self.body, 1)
        self.footer = QHBoxLayout()
        self.footer.addStretch(1)
        outer.addLayout(self.footer)


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


# ------------------------------------------------------------------ pages

class SetupPage(Page):
    def __init__(self, app):
        super().__init__("Welcome", "Turn a VGMTrans rip into an FL Studio project with the right "
                                    "instrument on every channel. You'll need these once:")
        self.app = app
        vgm = QPushButton("Get VGMTrans")
        vgm.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(VGMTRANS_URL)))
        self.body.addWidget(card(
            label("<b>1. VGMTrans</b> (free) rips the music and instruments out of a game."),
            label("Download the latest Windows release, unzip it, and run <code>VGMTrans.exe</code>."),
            row(vgm)))
        sfz = QPushButton("Get sforzando")
        sfz.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(SFORZANDO_URL)))
        self.body.addWidget(card(
            label("<b>2. sforzando</b> (free, by Plogue) plays the instruments inside FL Studio."),
            label("Install it, then in FL Studio open <i>Options → Manage plugins</i> and click "
                  "<i>Find plugins</i> so FL sees it."),
            row(sfz)))
        self.template_status = label("")
        self.body.addWidget(card(
            label("<b>3. FL Studio template</b>"),
            self.template_status))
        self.memory_status = label("")
        self.body.addWidget(card(label("<b>Remembered instrument names</b>"), self.memory_status))
        self.body.addStretch(1)
        nxt = QPushButton("Next: rip a song →")
        nxt.setObjectName("primary")
        nxt.clicked.connect(lambda: app.go(1))
        self.footer.addWidget(nxt)

    def refresh(self):
        if build_flp.has_donor():
            self.template_status.setText("<span style='color:#2e7d32'>✓ Set up.</span> The app knows "
                                         "how sforzando is set up on your PC.")
        else:
            self.template_status.setText("Not set up yet. You'll do it once, in step 5. It takes a "
                                         "minute: you set one channel to sforzando by hand, and the "
                                         "app copies that for every channel after.")
        n = len(name_memory.load()["instruments"])
        self.memory_status.setText(f"{n} instrument{'s' if n != 1 else ''} named so far. Names you "
                                   "give are filled in automatically next time.")


class RipPage(Page):
    def __init__(self, app):
        super().__init__("Rip a song with VGMTrans",
                         "Export the song as MIDI + SF2 into the rips folder, then pick it below.")
        self.app = app
        steps = label(
            "<ol style='margin-left:-18px'>"
            "<li>Open <b>VGMTrans</b> and drag your game file (for example a <code>.nds</code> ROM "
            "you own) into its window.</li>"
            "<li>The songs are in the <b>bottom panel</b>. Click the song's <b>red music-note "
            "icon</b> to select it. To check it's the right song, use the <b>play button below "
            "that panel</b>. DS songs usually start with <code>SEQ_</code>.</li>"
            "<li>Click <b>Convert</b> in the menu bar at the top. The menu may only open on the "
            "<b>third click</b>; that's normal. Choose <b>Export as MIDI and SF2</b>. "
            "(Right-clicking the song doesn't work in some versions, so use the Convert menu.)</li>"
            "<li>Save into the <b>rips folder</b>:</li></ol>")
        self.rips_edit = QLineEdit(os.path.normpath(pipeline.RIPS_DIR))
        self.rips_edit.setReadOnly(True)
        copy_btn = QPushButton("Copy path")
        copy_btn.clicked.connect(lambda: QGuiApplication.clipboard().setText(self.rips_edit.text()))
        open_btn = QPushButton("Open folder")
        open_btn.clicked.connect(lambda: open_path(pipeline.RIPS_DIR))
        path_row = QHBoxLayout()
        path_row.addWidget(self.rips_edit, 1)
        path_row.addWidget(copy_btn)
        path_row.addWidget(open_btn)
        self.body.addWidget(card(steps, path_row))

        self.songs = QListWidget()
        self.songs.setMinimumHeight(120)
        self.songs.currentRowChanged.connect(self.song_selected)
        self.songs.itemDoubleClicked.connect(lambda _: self.start())
        self.sf2_combo = QComboBox()
        self.sf2_combo.setToolTip("The instrument bank exported with this song")
        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self.refresh)
        sf2_row = QHBoxLayout()
        sf2_row.addWidget(QLabel("Instruments:"))
        sf2_row.addWidget(self.sf2_combo, 1)
        sf2_row.addWidget(refresh)
        self.empty = label("Nothing in the rips folder yet. Export a song there and it shows up "
                           "here automatically.", "subtitle")
        songs_card = card(label("<b>Songs in your rips folder</b> (newest first)"),
                          self.empty, self.songs, sf2_row)
        lay = songs_card.layout()
        lay.setStretchFactor(self.songs, 1)
        lay.addStretch(0)
        self.body.addWidget(songs_card, 1)

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.hide()
        self.status = label("", "subtitle")
        self.body.addWidget(self.progress)
        self.body.addWidget(self.status)

        back = QPushButton("← Back")
        back.clicked.connect(lambda: app.go(0))
        self.go_btn = QPushButton("Next: name samples →")
        self.go_btn.setObjectName("primary")
        self.go_btn.clicked.connect(self.start)
        self.footer.addWidget(back)
        self.footer.addWidget(self.go_btn)

        os.makedirs(pipeline.RIPS_DIR, exist_ok=True)
        self.watcher = QFileSystemWatcher([pipeline.RIPS_DIR])
        self.watcher.directoryChanged.connect(lambda _: QTimer.singleShot(800, self.refresh))
        self.rips = []
        self.refresh()

    def refresh(self):
        row_now = self.songs.currentRow()
        current = self.rips[row_now]["midi"] if 0 <= row_now < len(self.rips) else None
        self.rips = pipeline.find_rips()
        # watch subfolders too, in case people sort rips by game
        dirs = {os.path.dirname(r["midi"]) for r in self.rips} | {pipeline.RIPS_DIR}
        missing = [d for d in dirs if d not in self.watcher.directories()]
        if missing:
            self.watcher.addPaths(missing)
        self.songs.blockSignals(True)
        self.songs.clear()
        select = 0
        for i, r in enumerate(self.rips):
            sf2 = os.path.basename(r["sf2"]) if r["sf2"] else "no .sf2 found"
            rel = os.path.relpath(os.path.dirname(r["midi"]), pipeline.RIPS_DIR)
            where = "" if rel == "." else f"{rel}  ·  "
            self.songs.addItem(f"{r['song']}    —   {where}{sf2}  ·  {r['when']}")
            if r["midi"] == current:
                select = i
        self.songs.blockSignals(False)
        has = bool(self.rips)
        self.empty.setVisible(not has)
        self.songs.setVisible(has)
        if has:
            self.songs.setCurrentRow(select)
        self.song_selected(self.songs.currentRow())

    def song_selected(self, i):
        self.sf2_combo.clear()
        if 0 <= i < len(self.rips):
            r = self.rips[i]
            for opt in r["sf2_options"]:
                self.sf2_combo.addItem(os.path.relpath(opt, pipeline.RIPS_DIR), opt)
            if r["sf2"]:
                self.sf2_combo.setCurrentIndex(r["sf2_options"].index(r["sf2"]))
        self.go_btn.setEnabled(0 <= i < len(self.rips) and self.sf2_combo.count() > 0)

    def start(self):
        i = self.songs.currentRow()
        if not (0 <= i < len(self.rips)) or self.sf2_combo.count() == 0:
            return
        midi, sf2 = self.rips[i]["midi"], self.sf2_combo.currentData()
        self.go_btn.setEnabled(False)
        self.progress.show()
        self.status.setText("Reading the song and making a short preview of each instrument…")
        out_dir = pipeline.new_run_dir(self.rips[i]["song"])
        self.app.run_dir = out_dir
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
        self.app.error("Couldn't read that song", msg)


class InstrumentRow(QFrame):
    def __init__(self, inst, preview_path, player):
        super().__init__()
        self.setObjectName("card")
        self.inst, self.preview_path, self.player = inst, preview_path, player
        lay = QGridLayout(self)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setHorizontalSpacing(12)
        self.play_btn = QPushButton("▶")
        self.play_btn.setFixedWidth(44)
        self.play_btn.setToolTip("Play / stop this instrument")
        self.play_btn.clicked.connect(self.toggle)
        title = f"<b>Instrument {inst['program']}</b>" + (f" (bank {inst['bank']})" if inst["bank"] else "")
        chans = ", ".join(str(c) for c in inst["channels"])
        info = f"channel{'s' if len(inst['channels']) != 1 else ''} {chans} · {inst['notes']} notes"
        self.edit = QLineEdit(inst.get("remembered") or "")
        self.edit.setPlaceholderText("Name this sample, e.g. Strings")
        self.edit.setMaxLength(40)
        self.badge = label("")
        self.edit.textChanged.connect(self.update_badge)
        self.update_badge()
        if inst.get("missing"):
            self.badge.setText("<span style='color:#b26a00'>not in the SF2 — will be silent</span>")
            self.play_btn.setEnabled(False)
        lay.addWidget(self.play_btn, 0, 0, 2, 1)
        lay.addWidget(label(title), 0, 1)
        lay.addWidget(label(info, "subtitle"), 1, 1)
        lay.addWidget(self.edit, 0, 2, 2, 1)
        lay.addWidget(self.badge, 0, 3, 2, 1)
        lay.setColumnStretch(2, 1)
        lay.setColumnMinimumWidth(1, 200)
        lay.setColumnMinimumWidth(3, 90)

    def update_badge(self):
        remembered = self.inst.get("remembered")
        text = self.edit.text().strip()
        if remembered and text == remembered:
            self.badge.setText("<span style='color:#2e7d32'>remembered</span>")
        elif remembered and not text:
            self.badge.setText("<span style='color:#b26a00'>will be forgotten</span>")
        elif remembered:
            self.badge.setText("<span style='color:#b26a00'>renamed</span>")
        else:
            self.badge.setText("")

    def toggle(self):
        if self.player.current is self and self.player.playbackState() == QMediaPlayer.PlayingState:
            self.player.stop()
            return
        self.player.play_row(self)

    def set_playing(self, playing):
        self.play_btn.setText("■" if playing else "▶")


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


class NamePage(Page):
    def __init__(self, app):
        super().__init__("Name each sample",
                         "Play each instrument and type what it sounds like. These names become your "
                         "FL Studio channel names, and they're remembered for next time. "
                         "Leave a box empty to keep the number.")
        self.app = app
        self.player = Player()
        self.rows = []
        self.list_widget = QWidget()
        self.list_layout = QVBoxLayout(self.list_widget)
        self.list_layout.setSpacing(8)
        self.list_layout.setContentsMargins(0, 0, 8, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidget(self.list_widget)
        self.body.addWidget(scroll, 1)
        self.hint = label("Tip: <b>Ctrl+Space</b> plays the instrument whose box you're typing in; "
                          "<b>Enter</b> jumps to the next box.", "subtitle")
        self.body.addWidget(self.hint)
        back = QPushButton("← Back")
        back.clicked.connect(lambda: (self.player.stop(), app.go(1)))
        self.save_btn = QPushButton("Save names and make FL files →")
        self.save_btn.setObjectName("primary")
        self.save_btn.clicked.connect(self.save)
        self.footer.addWidget(back)
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
            r = InstrumentRow(inst, os.path.join(self.app.run_dir, inst["preview"]), self.player)
            self.rows.append(r)
            self.list_layout.addWidget(r)
        self.list_layout.addStretch(1)
        for a, b in zip(self.rows, self.rows[1:]):
            a.edit.returnPressed.connect(b.edit.setFocus)
        if self.rows:
            self.rows[-1].edit.returnPressed.connect(self.save_btn.setFocus)
            self.rows[0].edit.setFocus()

    def play_focused(self):
        for r in self.rows:
            if r.edit.hasFocus():
                r.toggle()

    def save(self):
        self.player.stop()
        names = {r.inst["key"]: r.edit.text() for r in self.rows}
        self.save_btn.setEnabled(False)
        self.app.run_in_background(pipeline.finalize, (self.app.run_dir, names), self.finished, self.failed)

    def finished(self, result):
        self.save_btn.setEnabled(True)
        self.app.result = result
        self.app.pages[3].load(result)
        self.app.go(3)

    def failed(self, msg):
        self.save_btn.setEnabled(True)
        self.app.error("Couldn't make the FL files", msg)


class FLPage(Page):
    def __init__(self, app):
        super().__init__("Bring it into FL Studio",
                         "FL does the import; the app then sets up every channel.")
        self.app = app
        self.path_edit = QLineEdit()
        self.path_edit.setReadOnly(True)
        copy_btn = QPushButton("Copy path")
        copy_btn.clicked.connect(lambda: QGuiApplication.clipboard().setText(self.path_edit.text()))
        folder_btn = QPushButton("Show in folder")
        folder_btn.clicked.connect(lambda: show_in_folder(self.path_edit.text()))
        path_row = QHBoxLayout()
        path_row.addWidget(self.path_edit, 1)
        path_row.addWidget(copy_btn)
        path_row.addWidget(folder_btn)
        self.body.addWidget(card(
            label("<b>1.</b> In FL Studio, start a new project and go to <i>File → Import → MIDI file</i>. "
                  "Choose this file:"),
            path_row))
        self.body.addWidget(card(label(
            "<b>2.</b> In the import window, set <b>Channel type</b> to <b>MIDI Out</b> and turn on "
            "<b>Create one channel per track</b>. Click <i>Accept</i>.")))
        self.body.addWidget(card(label(
            "<b>3.</b> In the <b>Playlist</b>, click the small menu icon at the top-left of the pattern "
            "clip and choose <b>Split by channel</b>.")))
        self.template_card = card(label(
            "<b>4. First time only:</b> right-click any one channel in the Channel Rack → "
            "<b>Replace</b> → <b>sforzando</b>. Then drag any <code>.sfz</code> file from the output "
            "folder onto sforzando. The app copies this setup for every other channel, and "
            "remembers it so you never do this again."))
        self.body.addWidget(self.template_card)
        self.save_label = label("")
        self.body.addWidget(card(self.save_label))
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.hide()
        self.body.addWidget(self.progress)
        self.body.addStretch(1)
        back = QPushButton("← Back")
        back.clicked.connect(lambda: app.go(2))
        self.pick_btn = QPushButton("Choose saved project…")
        self.pick_btn.setObjectName("primary")
        self.pick_btn.clicked.connect(self.pick)
        self.footer.addWidget(back)
        self.footer.addWidget(self.pick_btn)

    def load(self, result):
        self.path_edit.setText(os.path.normpath(result["split_mid"]))
        first = not build_flp.has_donor()
        self.template_card.setVisible(first)
        self.save_label.setText(f"<b>{5 if first else 4}.</b> Save the project (<i>File → Save as</i>), "
                                "then choose it below.")

    def pick(self):
        path, _ = QFileDialog.getOpenFileName(self, "Choose the FL project you just saved", "",
                                              "FL Studio projects (*.flp)")
        if not path:
            return
        self.pick_btn.setEnabled(False)
        self.progress.show()
        self.app.run_in_background(pipeline.build, (self.app.run_dir, path), self.finished, self.failed)

    def finished(self, res):
        self.progress.hide()
        self.pick_btn.setEnabled(True)
        self.app.pages[4].load(res)
        self.app.pages[0].refresh()
        self.app.go(4)

    def failed(self, msg):
        self.progress.hide()
        self.pick_btn.setEnabled(True)
        self.app.error("Couldn't build the project", msg)


class DonePage(Page):
    def __init__(self, app):
        super().__init__("Done", "Your project is ready. Every channel uses sforzando with its instrument loaded.")
        self.app = app
        self.summary = label("")
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        self.details.setFont(QFont("Consolas", 9))
        self.body.addWidget(self.summary)
        self.body.addWidget(self.details, 1)
        self.open_btn = QPushButton("Open in FL Studio")
        self.open_btn.setObjectName("primary")
        self.folder_btn = QPushButton("Show in folder")
        again = QPushButton("Start another song")
        again.clicked.connect(lambda: app.go(1))
        self.footer.addWidget(again)
        self.footer.addWidget(self.folder_btn)
        self.footer.addWidget(self.open_btn)
        self.out = ""
        self.open_btn.clicked.connect(lambda: open_path(self.out))
        self.folder_btn.clicked.connect(lambda: show_in_folder(self.out))

    def load(self, res):
        self.out = os.path.normpath(res["out"])
        text = f"<b>{res['changed']}</b> channel(s) set up. Saved as:<br><code>{self.out}</code>"
        if res["skipped"]:
            text += ("<br><span style='color:#b26a00'>Not changed (no matching instrument): "
                     + ", ".join(res["skipped"]) + "</span>")
        self.summary.setText(text)
        self.details.setPlainText("\n".join(res["lines"]))


# ------------------------------------------------------------------ window

class MainWindow(QMainWindow):
    STEPS = ["1  Welcome", "2  Rip a song", "3  Name samples", "4  Into FL Studio", "5  Done"]

    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.resize(980, 680)
        self.run_dir, self.state, self.result = None, None, None
        self.threads = []

        central = QWidget()
        lay = QHBoxLayout(central)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.steps = QListWidget()
        self.steps.setObjectName("steps")
        self.steps.setFixedWidth(220)
        self.steps.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.steps.setTextElideMode(Qt.ElideRight)
        for s in self.STEPS:
            self.steps.addItem(QListWidgetItem(s))
        self.steps.currentRowChanged.connect(self._step_clicked)
        line = QFrame()
        line.setFrameShape(QFrame.VLine)
        line.setFrameShadow(QFrame.Sunken)
        self.stack = QStackedWidget()
        self.pages = [SetupPage(self), RipPage(self), NamePage(self), FLPage(self), DonePage(self)]
        for p in self.pages:
            self.stack.addWidget(p)
        lay.addWidget(self.steps)
        lay.addWidget(line)
        lay.addWidget(self.stack, 1)
        self.setCentralWidget(central)
        self.pages[0].refresh()
        self.go(0)

    def unlocked(self, i):
        return {0: True, 1: True, 2: self.state is not None,
                3: self.result is not None, 4: bool(self.pages[4].out)}[i]

    def go(self, i):
        for k in range(len(self.STEPS)):
            item = self.steps.item(k)
            flags = Qt.ItemIsSelectable | Qt.ItemIsEnabled if self.unlocked(k) or k == i else Qt.NoItemFlags
            item.setFlags(flags)
        self.steps.blockSignals(True)
        self.steps.setCurrentRow(i)
        self.steps.blockSignals(False)
        self.stack.setCurrentIndex(i)
        if i == 0:
            self.pages[0].refresh()

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

    def error(self, title, msg):
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle(title)
        first = msg.strip().splitlines()[-1] if "Traceback" in msg else msg
        box.setText(first)
        if "Traceback" in msg:
            box.setDetailedText(msg)
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
