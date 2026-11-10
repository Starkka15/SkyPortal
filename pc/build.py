"""Builds SkyPortal for release into pc/release/:

  SkyPortal-<version>-portable.exe   one file, runs from anywhere
  SkyPortal-<version>-Setup.exe      installer (Windows, needs Inno Setup 6)

The installer packs PyInstaller's folder build (pc/dist/SkyPortal/), which starts faster
than the one-file exe because nothing is unpacked at each launch.

Needs PySide6-Essentials and PyInstaller:
    python -m pip install PySide6-Essentials pyinstaller
    python pc/build.py [--iscc PATH\\ISCC.exe]

On Linux it builds the one-file program only.
"""

import os
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ICON = os.path.join(HERE, "skyportal.ico")
DIST = os.path.join(HERE, "dist")
WORK = os.path.join(HERE, "build")
RELEASE = os.path.join(HERE, "release")


def version():
    with open(os.path.join(HERE, "skyportal_gui.py"), encoding="utf-8") as f:
        return re.search(r'^VERSION = "([^"]+)"', f.read(), re.M).group(1)


def make_icon():
    """The window icon, saved as an .ico for the exe."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QSize
    from PySide6.QtWidgets import QApplication
    sys.path.insert(0, HERE)
    app = QApplication.instance() or QApplication([])  # noqa: F841 - needed for QPixmap
    import skyportal_gui
    if not skyportal_gui.app_icon().pixmap(QSize(256, 256)).save(ICON, "ICO"):
        sys.exit("could not write %s" % ICON)


def version_file(ver):
    """A Windows version resource, so the exe's properties show the version."""
    nums = (tuple(int(x) for x in ver.split(".")) + (0, 0, 0, 0))[:4]
    path = os.path.join(WORK, "version.txt")
    os.makedirs(WORK, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write("""VSVersionInfo(
  ffi=FixedFileInfo(filevers=%(n)r, prodvers=%(n)r, mask=0x3f, flags=0x0, OS=0x40004,
                    fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('FileDescription', 'SkyPortal - Portal of Power for Skylanders PC ports'),
      StringStruct('ProductName', 'SkyPortal'),
      StringStruct('FileVersion', '%(v)s'),
      StringStruct('ProductVersion', '%(v)s'),
      StringStruct('LegalCopyright', 'GPL-3.0-or-later'),
      StringStruct('OriginalFilename', 'SkyPortal.exe')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
""" % {"n": nums, "v": ver})
    return path


def pyinstaller(mode, ver):
    args = [
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", mode, "--windowed",
        "--name", "SkyPortal",
        "--distpath", DIST,
        "--workpath", os.path.join(WORK, mode.strip("-")),
        "--specpath", os.path.join(WORK, mode.strip("-")),
        "--paths", os.path.join(HERE, "..", "py_modules"),
        "--hidden-import", "skyportal",
        "--exclude-module", "tkinter",
    ]
    if os.name == "nt":
        args += ["--icon", ICON, "--version-file", version_file(ver)]
    args.append(os.path.join(HERE, "skyportal_gui.py"))
    subprocess.check_call(args)


def main():
    ver = version()
    iscc = None
    if "--iscc" in sys.argv[:-1]:
        iscc = sys.argv[sys.argv.index("--iscc") + 1]
    elif os.name == "nt":
        for c in (shutil.which("ISCC"), r"G:\InnoSetup\ISCC.exe",
                  os.path.expandvars(r"%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"),
                  os.path.expandvars(r"%LocalAppData%\Programs\Inno Setup 6\ISCC.exe")):
            if c and os.path.exists(c):
                iscc = c
                break
    if os.name == "nt" and not os.path.exists(ICON):
        make_icon()
    os.makedirs(RELEASE, exist_ok=True)

    pyinstaller("--onefile", ver)
    exe = "SkyPortal.exe" if os.name == "nt" else "SkyPortal"
    portable = os.path.join(RELEASE, "SkyPortal-%s-portable%s" % (ver, ".exe" if os.name == "nt" else ""))
    shutil.copy2(os.path.join(DIST, exe), portable)
    print("built", portable)

    if os.name != "nt":
        return
    pyinstaller("--onedir", ver)
    if not iscc:
        print("Inno Setup not found (pass --iscc PATH): skipped the installer")
        return
    subprocess.check_call([iscc, "/Q", "/DAppVersion=" + ver, os.path.join(HERE, "installer.iss")])
    print("built", os.path.join(RELEASE, "SkyPortal-%s-Setup.exe" % ver))


if __name__ == "__main__":
    main()
