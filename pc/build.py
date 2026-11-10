"""Builds SkyPortal.exe (Windows) or SkyPortal (Linux), one file, into pc/dist/.

Needs PySide6-Essentials and PyInstaller:
    python -m pip install PySide6-Essentials pyinstaller
    python pc/build.py
"""

import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ICON = os.path.join(HERE, "skyportal.ico")


def make_icon():
    """The window icon, saved as a multi-size .ico for the exe."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QSize
    from PySide6.QtWidgets import QApplication
    sys.path.insert(0, HERE)
    app = QApplication.instance() or QApplication([])  # noqa: F841 - needed for QPixmap
    import skyportal_gui
    icon = skyportal_gui.app_icon()
    if not icon.pixmap(QSize(256, 256)).save(ICON, "ICO"):
        sys.exit("could not write %s" % ICON)


def main():
    if not os.path.exists(ICON):
        make_icon()
    args = [
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onefile", "--windowed",
        "--name", "SkyPortal",
        "--distpath", os.path.join(HERE, "dist"),
        "--workpath", os.path.join(HERE, "build"),
        "--specpath", os.path.join(HERE, "build"),
        "--paths", os.path.join(HERE, "..", "py_modules"),
        "--hidden-import", "skyportal",
        # Only QtCore/QtGui/QtWidgets are used.
        "--exclude-module", "tkinter",
    ]
    if os.name == "nt":
        args += ["--icon", ICON]
    args.append(os.path.join(HERE, "skyportal_gui.py"))
    subprocess.check_call(args)


if __name__ == "__main__":
    main()
