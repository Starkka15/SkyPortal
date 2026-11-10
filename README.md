# SkyPortal

A Portal of Power for the native
[Skylanders: Spyro's Adventure port](https://github.com/Starkka15/skylanders-sa-pc), as a
[Decky Loader](https://github.com/SteamDeckHomebrew/decky-loader) plugin for SteamOS
(ROG Ally, Steam Deck) and as a desktop app for Windows.

- **Real portal passthrough:** plug in a Wii/PS3 Portal of Power (USB 1430:0150, including
  the wireless receiver) and the game sees it. The quick access panel shows which toys
  are standing on it.
- **Figures from dumps:** place `.sky`/`.bin`/`.dump` figure dumps (1024-byte MIFARE
  images) on a virtual portal, mixed freely with real toys. The game's writes (levels,
  gold, hats) are saved back into the dump file.
- **Dumping:** read the toys standing on a real portal into `~/Skylanders/Dumped/`.
- **Library by element:** dumps are grouped by element (Air, Earth, Fire, Life, Magic,
  Tech, Undead, Water, Magic Items, Adventure Packs) from the toy's own id, or by folder.

Only use dumps of toys you own.

> **About this project.** SkyPortal isn't mine to gatekeep. It's a basic starting
> point to build on (features, fixes, polish), not a finished or maintained product. If
> you feel up to it, feel free to continue the work; forks and pull requests are welcome.

## Install (Decky)

Download `SkyPortal.zip` from [Releases](../../releases) and install it from Decky's
settings (developer mode, "Install plugin from ZIP"), or unpack it into
`~/homebrew/plugins/` and restart Decky. The plugin runs as root, which it needs to drive
a real portal through usbfs.

Figure dumps go in `~/Skylanders` (subfolders are fine). Settings and the list of placed
figures are kept in Decky's settings folder for the plugin.

## How it talks to the game

The port's SDK routes the game's portal calls (`XamInputNonController{Get,Set}Raw`) to
this service over `/tmp/skyportal.sock` on Linux (TCP 127.0.0.1:47150 on Windows; the
`SKYPORTAL_SOCKET` environment variable overrides it). Frames are
`[type][length][payload]`: `W` a packet from the game, `R` a read request, `D` the reply.

The Wii/PS3 receiver rejects interrupt-OUT transfers, which Linux's usbhid always uses, so
the service detaches usbhid and drives the portal through usbfs: commands as control
SET_REPORT, replies as interrupt-IN reads.

## PC (Windows)

Download `SkyPortal.exe` from [Releases](../../releases) and run it before or while the
game runs. No install and no Python needed.

- **Portal:** a plugged-in Wii/PS3 portal is used directly through Windows HID, with no
  driver to install. Its toys show under "On the portal".
- **Library:** figure dumps are listed by element or by folder, with search. Double-click
  one (or select it and press "Place on portal") to put it on the virtual portal, and use
  Remove to lift it. Drag dump files onto the window, or use "Add dumps...", to copy them
  into the library.
- **Dumping:** "Dump toys on the portal" saves every toy on the real portal to the
  library's `Dumped` folder.

The library is `Documents\Skylanders` by default (File → Change library folder). The game
connects over TCP 127.0.0.1:47150.

Building the exe (Python 3 with PySide6 and PyInstaller):

```
python -m pip install PySide6-Essentials pyinstaller
python pc/build.py
```

The window also runs from source on Linux (`python pc/skyportal_gui.py`). A real portal
there needs root, as with the Decky plugin.

## Command line

`py_modules/skyportal.py` also runs on its own (Python 3, no dependencies):

```
python skyportal.py --library <folder of dumps> [--tcp 127.0.0.1:47150] [--load <dump>]
```

then type `list`, `load <file> [slot]`, `remove <slot>`, `clear`, `dump`, `state` or
`quit`. A real portal is used on Windows (HID) and on Linux (usbfs, as root).

## Building the plugin

Node.js with pnpm, and Python 3:

```
pnpm install
pnpm build
python build_zip.py      # writes out/SkyPortal.zip
```

## License

GPL-3.0-or-later (see LICENSE). Skylanders is a trademark of Activision; this project is
not affiliated with or endorsed by Activision.
