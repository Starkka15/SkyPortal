"""SkyPortal for PC: a desktop window around the SkyPortal service.

Runs the same service as the Decky plugin (py_modules/skyportal.py) in this process:
the game connects to it (TCP 127.0.0.1:47150 on Windows, /tmp/skyportal.sock on
Linux), a real Wii/PS3 portal is passed through when one is plugged in, and figures
from dumps can be placed on a virtual portal, mixed with real toys.

License: GPL-3.0-or-later.
"""

import logging
import os
import shutil
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "py_modules"))

import skyportal  # noqa: E402

from PySide6.QtCore import QObject, QSettings, QSize, QStandardPaths, Qt, QTimer, QUrl, Signal  # noqa: E402
from PySide6.QtGui import (QAction, QColor, QDesktopServices, QFont, QIcon, QPainter,  # noqa: E402
                           QPixmap)
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QComboBox, QFileDialog,  # noqa: E402
                               QFrame, QGroupBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
                               QListWidget, QListWidgetItem, QMainWindow, QMenu, QMessageBox,
                               QPlainTextEdit, QProgressBar, QPushButton, QSizePolicy, QSplitter, QToolButton,
                               QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

APP_NAME = "SkyPortal"
VERSION = "0.2.1"
TCP = "127.0.0.1:47150"
SOCKET = "/tmp/skyportal.sock"

ELEMENT_COLORS = {
    "Air": "#5fb8e8", "Earth": "#9a6b3c", "Fire": "#e8552a", "Life": "#4caf3e",
    "Magic": "#9b4fd0", "Tech": "#e89b1c", "Undead": "#6d6d93", "Water": "#2f7fd6",
    "Magic Items": "#c9a227", "Adventure Packs": "#3fa596", "Other": "#8a8a8a",
}

# Magic and Magic Items would both be "M".
BADGE_LETTERS = {"Magic Items": "I", "Other": "?"}

log = logging.getLogger("skyportal.gui")


def element_of(fid):
    return skyportal.figure_group(fid) if fid is not None and fid >= 0 else "Other"


_icon_cache = {}


def element_icon(group, size=22):
    """A round badge in the element's colour with its first letter."""
    key = (group, size)
    if key in _icon_cache:
        return _icon_cache[key]
    pm = QPixmap(size * 2, size * 2)  # drawn at 2x for high-DPI screens
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setBrush(QColor(ELEMENT_COLORS.get(group, ELEMENT_COLORS["Other"])))
    p.setPen(Qt.NoPen)
    p.drawEllipse(2, 2, size * 2 - 4, size * 2 - 4)
    p.setPen(QColor("white"))
    f = QFont()
    f.setBold(True)
    f.setPixelSize(int(size * 1.0))
    p.setFont(f)
    p.drawText(pm.rect(), Qt.AlignCenter, BADGE_LETTERS.get(group, group[0]))
    p.end()
    pm.setDevicePixelRatio(2.0)
    icon = QIcon(pm)
    _icon_cache[key] = icon
    return icon


def app_icon():
    """A portal seen from the side: dark base, glowing top. Drawn at each icon size."""
    icon = QIcon()
    for size in (16, 24, 32, 48, 64, 128, 256):
        pm = QPixmap(size, size)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        p.scale(size / 64.0, size / 64.0)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#1b3d7a"))
        p.drawEllipse(4, 26, 56, 30)
        p.setBrush(QColor("#57a6ff"))
        p.drawEllipse(9, 24, 46, 20)
        p.setBrush(QColor("#c9e4ff"))
        p.drawEllipse(19, 28, 26, 10)
        p.end()
        icon.addPixmap(pm)
    return icon


def file_figure_id(path):
    try:
        with open(path, "rb") as f:
            head = f.read(0x12)
        return head[0x10] | head[0x11] << 8 if len(head) >= 0x12 else -1
    except OSError:
        return -1


class Bridge(QObject):
    """Signals from worker threads to the window."""
    dump_progress = Signal(int, int)
    dump_done = Signal(object, str)


class StatusPill(QFrame):
    def __init__(self, title):
        super().__init__()
        self.setObjectName("pill")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 6, 12, 6)
        lay.setSpacing(8)
        self.dot = QLabel()
        self.dot.setFixedSize(12, 12)
        self.title = QLabel(title)
        self.title.setStyleSheet("font-weight: 600;")
        self.text = QLabel()
        lay.addWidget(self.dot)
        lay.addWidget(self.title)
        lay.addWidget(self.text, 1)

    def set(self, ok, text, warn=False):
        color = "#3cb44b" if ok else ("#e8a317" if warn else "#9a9a9a")
        self.dot.setStyleSheet("background: %s; border-radius: 6px;" % color)
        self.text.setText(text)


class PortalRow(QWidget):
    """One figure on the portal: badge, name, detail, and Remove for dump figures."""

    def __init__(self, group, name, detail, on_remove=None):
        super().__init__()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(6, 4, 6, 4)
        badge = QLabel()
        badge.setPixmap(element_icon(group, 26).pixmap(QSize(26, 26)))
        lay.addWidget(badge)
        col = QVBoxLayout()
        col.setSpacing(0)
        n = QLabel(name)
        n.setStyleSheet("font-weight: 600;")
        d = QLabel(detail)
        d.setStyleSheet("color: palette(placeholder-text);")
        # Let the text give way in a narrow window, so the Remove button stays in view.
        for lab in (n, d):
            lab.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
            lab.setToolTip(lab.text())
        col.addWidget(n)
        col.addWidget(d)
        lay.addLayout(col, 1)
        if on_remove:
            b = QPushButton("Remove")
            b.clicked.connect(on_remove)
            lay.addWidget(b)


class MainWindow(QMainWindow):
    def __init__(self, library=None):
        super().__init__()
        self.settings = QSettings(APP_NAME, APP_NAME)
        self.bridge = Bridge()
        self.bridge.dump_progress.connect(self._on_dump_progress)
        self.bridge.dump_done.connect(self._on_dump_done)
        self.svc = None
        self.last_portal_key = None
        self.seen_events = 0.0
        self.dump_thread = None
        self.real_names = {}  # slot -> name of a toy on the real portal, for the dump progress

        self.setWindowTitle("SkyPortal")
        self.setWindowIcon(app_icon())
        self.setAcceptDrops(True)
        self._build_ui()
        self._build_menu()

        geo = self.settings.value("geometry")
        if geo is not None:
            self.restoreGeometry(geo)
        else:
            self.resize(980, 640)

        # --library DIR (for testing) is used for this run only and not remembered.
        self.library_dir = os.path.normpath(library or self.settings.value("library", "") or self._default_library())
        self._start_service()

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._poll)
        self.timer.start(300)

    # ----------------------------------------------------------------- setup
    def _default_library(self):
        docs = QStandardPaths.writableLocation(QStandardPaths.DocumentsLocation) or os.path.expanduser("~")
        return os.path.join(docs, "Skylanders")

    def _state_file(self):
        d = QStandardPaths.writableLocation(QStandardPaths.AppDataLocation)
        os.makedirs(d, exist_ok=True)
        return os.path.join(d, "state.json")

    def _start_service(self):
        tcp = TCP if os.name == "nt" else None
        svc = skyportal.PortalService(self.library_dir, state_file=self._state_file(),
                                      socket_path=SOCKET, tcp=tcp, use_real=True)
        try:
            svc.start()
        except OSError as e:
            where = TCP if tcp else SOCKET
            QMessageBox.critical(self, "SkyPortal",
                                 "SkyPortal could not open %s for the game:\n%s\n\n"
                                 "Is SkyPortal already running?" % (where, e))
            svc.running = False
            self.svc = None
            self.status_game.set(False, "Not listening (%s)" % e, warn=True)
            return
        self.svc = svc
        self.library_label.setText(self.library_dir)
        self.library_label.setToolTip(self.library_dir)
        self.seen_events = 0.0
        self.refresh_library()

    def _build_ui(self):
        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        pills = QHBoxLayout()
        self.status_portal = StatusPill("Portal")
        self.status_game = StatusPill("Game")
        pills.addWidget(self.status_portal, 1)
        pills.addWidget(self.status_game, 1)
        root.addLayout(pills)

        split = QSplitter(Qt.Horizontal)
        root.addWidget(split, 1)

        # ---- left: what is on the portal, dumping, events
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)

        on_box = QGroupBox("On the portal")
        ob = QVBoxLayout(on_box)
        self.portal_list = QListWidget()
        self.portal_list.setSelectionMode(QAbstractItemView.NoSelection)
        self.portal_list.setFocusPolicy(Qt.NoFocus)
        self.portal_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        ob.addWidget(self.portal_list, 1)
        self.portal_empty = QLabel("Nothing on the portal. Place a figure from the library, "
                                   "or put a toy on a real portal.")
        self.portal_empty.setWordWrap(True)
        self.portal_empty.setStyleSheet("color: palette(placeholder-text);")
        ob.addWidget(self.portal_empty)
        self.remove_all = QPushButton("Remove all dump figures")
        self.remove_all.clicked.connect(self.lift_all)
        ob.addWidget(self.remove_all)
        ll.addWidget(on_box, 3)

        dump_box = QGroupBox("Dump toys")
        db = QVBoxLayout(dump_box)
        hint = QLabel("Saves every toy on the real portal to the library's Dumped folder.")
        hint.setWordWrap(True)
        db.addWidget(hint)
        self.dump_btn = QPushButton("Dump toys on the portal")
        self.dump_btn.clicked.connect(self.start_dump)
        db.addWidget(self.dump_btn)
        self.dump_label = QLabel()
        self.dump_label.setVisible(False)
        db.addWidget(self.dump_label)
        self.dump_bar = QProgressBar()
        self.dump_bar.setRange(0, 64)
        self.dump_bar.setTextVisible(False)
        self.dump_bar.setVisible(False)
        db.addWidget(self.dump_bar)
        ll.addWidget(dump_box)

        ev_box = QGroupBox("Activity")
        eb = QVBoxLayout(ev_box)
        self.events = QPlainTextEdit()
        self.events.setReadOnly(True)
        self.events.setMaximumBlockCount(500)
        eb.addWidget(self.events)
        ll.addWidget(ev_box, 2)
        split.addWidget(left)

        # ---- right: library
        lib_box = QGroupBox("Figure library")
        lb = QVBoxLayout(lib_box)
        top = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search figures")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._apply_filter)
        top.addWidget(self.search, 1)
        self.group_mode = QComboBox()
        self.group_mode.addItems(["By element", "By folder"])
        self.group_mode.setCurrentIndex(int(self.settings.value("group_mode", 0)))
        self.group_mode.currentIndexChanged.connect(self._group_mode_changed)
        top.addWidget(self.group_mode)
        refresh = QToolButton()
        refresh.setText("Refresh")
        refresh.clicked.connect(self.refresh_library)
        top.addWidget(refresh)
        lb.addLayout(top)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Name", "Figure", "Folder"])
        self.tree.setRootIsDecorated(True)
        self.tree.setUniformRowHeights(True)
        self.tree.setIconSize(QSize(20, 20))
        self.tree.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self.tree.header().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.tree.header().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.tree.itemDoubleClicked.connect(lambda it, _c: self.place_item(it))
        self.tree.itemSelectionChanged.connect(self._update_buttons)
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._tree_menu)
        lb.addWidget(self.tree, 1)

        self.lib_empty = QLabel()
        self.lib_empty.setWordWrap(True)
        self.lib_empty.setStyleSheet("color: palette(placeholder-text);")
        lb.addWidget(self.lib_empty)

        bottom = QHBoxLayout()
        self.library_label = QLabel()
        self.library_label.setStyleSheet("color: palette(placeholder-text);")
        self.library_label.setMinimumWidth(50)
        bottom.addWidget(self.library_label, 1)
        add_btn = QPushButton("Add dumps...")
        add_btn.clicked.connect(self.add_dumps)
        bottom.addWidget(add_btn)
        self.place_btn = QPushButton("Place on portal")
        self.place_btn.setDefault(True)
        self.place_btn.clicked.connect(lambda: self.place_item(self.tree.currentItem()))
        bottom.addWidget(self.place_btn)
        lb.addLayout(bottom)
        split.addWidget(lib_box)
        split.setStretchFactor(0, 2)
        split.setStretchFactor(1, 3)
        split.setSizes([380, 600])

        self.setCentralWidget(central)
        self.setStyleSheet("#pill { border: 1px solid palette(mid); border-radius: 8px; }")
        self._update_buttons()

    def _build_menu(self):
        m = self.menuBar().addMenu("&File")
        a = QAction("&Open library folder", self)
        a.triggered.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(self.library_dir)))
        m.addAction(a)
        a = QAction("&Change library folder...", self)
        a.triggered.connect(self.change_library)
        m.addAction(a)
        a = QAction("&Add dumps...", self)
        a.triggered.connect(self.add_dumps)
        m.addAction(a)
        m.addSeparator()
        a = QAction("E&xit", self)
        a.triggered.connect(self.close)
        m.addAction(a)
        h = self.menuBar().addMenu("&Help")
        a = QAction("&About SkyPortal", self)
        a.triggered.connect(self.about)
        h.addAction(a)

    # ----------------------------------------------------------------- library
    def refresh_library(self):
        if not self.svc:
            return
        expanded = {self.tree.topLevelItem(i).text(0) for i in range(self.tree.topLevelItemCount())
                    if self.tree.topLevelItem(i).isExpanded()}
        first = self.tree.topLevelItemCount() == 0
        scroll = self.tree.verticalScrollBar().value()
        self.tree.clear()
        count = 0
        if self.group_mode.currentIndex() == 0:
            for g in self.svc.library_by_element():
                top = QTreeWidgetItem([g["group"], "", ""])
                top.setIcon(0, element_icon(g["group"]))
                top.setFirstColumnSpanned(True)
                top.setFlags(Qt.ItemIsEnabled)
                f = top.font(0)
                f.setBold(True)
                top.setFont(0, f)
                for fig in g["figures"]:
                    it = QTreeWidgetItem([fig["name"], fig["kind"], fig["folder"]])
                    it.setIcon(0, element_icon(g["group"]))
                    it.setData(0, Qt.UserRole, fig["file"])
                    top.addChild(it)
                    count += 1
                self.tree.addTopLevelItem(top)
                top.setText(0, "%s (%d)" % (g["group"], top.childCount()))
                top.setData(0, Qt.UserRole + 1, g["group"])
                top.setExpanded(first or g["group"] in {e.split(" (")[0] for e in expanded})
        else:
            for entry in self.svc.library():
                name = entry["folder"] or "(library folder)"
                top = QTreeWidgetItem(["%s (%d)" % (name, len(entry["files"])), "", ""])
                top.setFirstColumnSpanned(True)
                top.setFlags(Qt.ItemIsEnabled)
                f = top.font(0)
                f.setBold(True)
                top.setFont(0, f)
                for fname in entry["files"]:
                    rel = (entry["folder"] + "/" + fname) if entry["folder"] else fname
                    fid = file_figure_id(os.path.join(self.library_dir, rel))
                    group = element_of(fid)
                    kind = skyportal.NAMES.get(fid, "Figure %d" % fid)
                    it = QTreeWidgetItem([os.path.splitext(fname)[0], kind, entry["folder"]])
                    it.setIcon(0, element_icon(group))
                    it.setData(0, Qt.UserRole, rel)
                    top.addChild(it)
                    count += 1
                self.tree.addTopLevelItem(top)
                top.setExpanded(first or top.text(0) in expanded)
        self.tree.setColumnHidden(2, self.group_mode.currentIndex() == 1)
        if count:
            self.lib_empty.hide()
        else:
            self.lib_empty.setText("No figure dumps yet. Put .sky, .bin or .dump files (1024-byte "
                                   "toy images) in the library folder, drag them onto this window, "
                                   "or dump your own toys from a real portal. Only use dumps of "
                                   "toys you own.")
            self.lib_empty.show()
        self._apply_filter()
        if first:
            QTimer.singleShot(0, self.tree.scrollToTop)
        else:
            QTimer.singleShot(0, lambda: self.tree.verticalScrollBar().setValue(scroll))
        self._update_buttons()

    def _group_mode_changed(self, i):
        self.settings.setValue("group_mode", i)
        self.tree.clear()
        self.refresh_library()

    def _apply_filter(self):
        text = self.search.text().strip().lower()
        for i in range(self.tree.topLevelItemCount()):
            top = self.tree.topLevelItem(i)
            visible = 0
            for j in range(top.childCount()):
                ch = top.child(j)
                hit = not text or text in ch.text(0).lower() or text in ch.text(1).lower()
                ch.setHidden(not hit)
                visible += hit
            top.setHidden(visible == 0)
            if text and visible:
                top.setExpanded(True)

    def _tree_menu(self, pos):
        it = self.tree.itemAt(pos)
        if not it or not it.data(0, Qt.UserRole):
            return
        m = QMenu(self)
        m.addAction("Place on portal", lambda: self.place_item(it))
        path = os.path.join(self.library_dir, it.data(0, Qt.UserRole))
        m.addAction("Show in folder", lambda: QDesktopServices.openUrl(
            QUrl.fromLocalFile(os.path.dirname(path))))
        m.exec(self.tree.viewport().mapToGlobal(pos))

    def change_library(self):
        d = QFileDialog.getExistingDirectory(self, "Library folder", self.library_dir)
        if not d or os.path.abspath(d) == os.path.abspath(self.library_dir):
            return
        if self.dump_thread and self.dump_thread.is_alive():
            QMessageBox.information(self, "SkyPortal", "Wait for the dump to finish first.")
            return
        if self.svc:
            self.svc.stop()
            time.sleep(0.2)
        self.library_dir = os.path.normpath(d)
        self.settings.setValue("library", d)
        self.tree.clear()
        self._start_service()

    def add_dumps(self):
        files, _ = QFileDialog.getOpenFileNames(self, "Add figure dumps", "",
                                                "Figure dumps (*.sky *.bin *.dump *.dmp);;All files (*)")
        self._import(files)

    def _import(self, files):
        files = [f for f in files if f.lower().endswith(skyportal.DUMP_EXTS)]
        if not files:
            return
        os.makedirs(self.library_dir, exist_ok=True)
        added, skipped = 0, []
        for f in files:
            try:
                if os.path.getsize(f) not in (skyportal.FIG_SIZE, 1056):
                    skipped.append(os.path.basename(f))
                    continue
                dest = os.path.join(self.library_dir, os.path.basename(f))
                if os.path.abspath(f) == os.path.abspath(dest):
                    continue
                if os.path.exists(dest):
                    base, ext = os.path.splitext(dest)
                    n = 2
                    while os.path.exists("%s (%d)%s" % (base, n, ext)):
                        n += 1
                    dest = "%s (%d)%s" % (base, n, ext)
                shutil.copy2(f, dest)
                added += 1
            except OSError as e:
                skipped.append("%s (%s)" % (os.path.basename(f), e))
        self._log("added %d dump(s) to the library" % added)
        if skipped:
            QMessageBox.warning(self, "SkyPortal", "These files were not added (a toy dump is 1024 "
                                "bytes):\n\n" + "\n".join(skipped))
        self.refresh_library()

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        self._import([u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()])

    # ----------------------------------------------------------------- portal
    def place_item(self, it):
        if not it or not self.svc:
            return
        rel = it.data(0, Qt.UserRole)
        if not rel:
            return
        try:
            self.svc.load(rel)
        except (ValueError, OSError) as e:
            QMessageBox.information(self, "SkyPortal", str(e))
        self._poll()

    def lift(self, slot):
        if self.svc:
            self.svc.remove(slot)
            self._poll()

    def lift_all(self):
        if self.svc:
            self.svc.clear()
            self._poll()

    def start_dump(self):
        if not self.svc or (self.dump_thread and self.dump_thread.is_alive()):
            return
        self.dump_bar.setValue(0)
        self.dump_label.setText("Starting...")
        self.dump_label.setVisible(True)
        self.dump_bar.setVisible(True)
        self.dump_btn.setEnabled(False)
        svc = self.svc

        def run():
            try:
                res = svc.dump(progress=lambda s, b: self.bridge.dump_progress.emit(s, b))
                self.bridge.dump_done.emit(res, "")
            except Exception as e:  # noqa: BLE001 - reported to the user
                self.bridge.dump_done.emit(None, str(e))

        self.dump_thread = threading.Thread(target=run, daemon=True, name="dump")
        self.dump_thread.start()

    def _on_dump_progress(self, slot, block):
        self.dump_bar.setValue(block + 1)
        name = self.real_names.get(slot) or "the toy in slot %d" % slot
        self.dump_label.setText("Reading %s: block %d of 64" % (name, block + 1))

    def _on_dump_done(self, res, err):
        self.dump_bar.setVisible(False)
        self.dump_label.setVisible(False)
        self._update_buttons()
        if err:
            QMessageBox.warning(self, "SkyPortal", "The dump did not finish: %s" % err)
            return
        names = ", ".join(r["name"] for r in res)
        self.statusBar().showMessage("Dumped %s to %s" % (names, os.path.join(self.library_dir, "Dumped")),
                                     8000)
        self.refresh_library()

    # ----------------------------------------------------------------- polling
    def _poll(self):
        if not self.svc:
            return
        st = self.svc.state()
        if st["real_portal"]:
            n = len(st["real_toys"])
            self.status_portal.set(True, "Real portal connected, %d toy%s on it" % (n, "" if n == 1 else "s"))
        elif st["real_error"]:
            self.status_portal.set(False, "Real portal found but not usable: %s" % st["real_error"], warn=True)
        else:
            self.status_portal.set(False, "No real portal, dump figures only")
        if st["game_connected"]:
            self.status_game.set(True, "Connected" + (", portal active" if st["active"] else ""))
        else:
            self.status_game.set(False, "Waiting for the game")

        self.real_names = {t["slot"]: t["name"] for t in st["real_toys"] if t["name"]}
        key = (tuple((t["slot"], t["name"]) for t in st["real_toys"]),
               tuple((l["slot"], l["file"]) for l in st["loaded"]))
        if key != self.last_portal_key:
            self.last_portal_key = key
            self._fill_portal(st)

        for t, msg in list(self.svc.events):
            if t > self.seen_events:
                self.seen_events = t
                self._log(msg, t)
        self._update_buttons(st)

    def _fill_portal(self, st):
        self.portal_list.clear()
        rows = []
        for t in st["real_toys"]:
            rows.append((t["slot"], None, t))
        for l in st["loaded"]:
            rows.append((l["slot"], l, None))
        for slot, l, t in sorted(rows, key=lambda r: r[0]):
            if t is not None:
                fid = next((k for k, v in skyportal.NAMES.items() if v == t["name"]), None)
                w = PortalRow(element_of(fid), t["name"] or "Reading toy...", "Real toy, slot %d" % slot)
            else:
                w = PortalRow(element_of(l["id"]), l["name"], "%s from dump, slot %d" % (l["kind"], slot),
                              on_remove=lambda _=False, s=slot: self.lift(s))
                w.setToolTip(os.path.join(self.library_dir, l["file"]))
            it = QListWidgetItem()
            it.setSizeHint(w.sizeHint())
            self.portal_list.addItem(it)
            self.portal_list.setItemWidget(it, w)
        empty = not rows
        self.portal_list.setVisible(not empty)
        self.portal_empty.setVisible(empty)

    def _update_buttons(self, st=None):
        if not hasattr(self, "place_btn"):
            return
        it = self.tree.currentItem()
        self.place_btn.setEnabled(bool(self.svc and it and it.data(0, Qt.UserRole)))
        if st is None and self.svc:
            st = self.svc.state()
        dumping = bool(self.dump_thread and self.dump_thread.is_alive())
        self.dump_btn.setEnabled(bool(st and st["real_portal"] and st["real_toys"] and not dumping))
        self.dump_btn.setToolTip("" if st and st["real_portal"] else "Plug in a real Wii/PS3 portal to dump toys.")
        self.remove_all.setEnabled(bool(st and st["loaded"]))

    def _log(self, msg, t=None):
        self.events.appendPlainText("%s  %s" % (time.strftime("%H:%M:%S", time.localtime(t or time.time())), msg))

    # ----------------------------------------------------------------- misc
    def about(self):
        QMessageBox.about(self, "About SkyPortal",
                          "<b>SkyPortal %s</b><br>Portal of Power for the Skylanders: Spyro's "
                          "Adventure PC port.<br><br>Passes a real Wii/PS3 portal through to the game, "
                          "places figures from dumps on a virtual portal, and dumps your own toys."
                          "<br><br>License: GPL-3.0-or-later<br>"
                          "<a href='https://github.com/Starkka15/SkyPortal'>github.com/Starkka15/SkyPortal</a>"
                          % VERSION)

    def closeEvent(self, e):
        self.settings.setValue("geometry", self.saveGeometry())
        if self.svc:
            self.svc.stop()
        super().closeEvent(e)


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    QApplication.setApplicationName(APP_NAME)
    QApplication.setOrganizationName(APP_NAME)
    app = QApplication(sys.argv)
    args = app.arguments()
    library = args[args.index("--library") + 1] if "--library" in args[:-1] else None
    w = MainWindow(library)
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
