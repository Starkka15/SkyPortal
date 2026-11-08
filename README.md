# SkyPortal

A Portal of Power for the native
[Skylanders: Spyro's Adventure port](https://github.com/Starkka15/skylanders-sa-pc), as a
[Decky Loader](https://github.com/SteamDeckHomebrew/decky-loader) plugin for SteamOS
(ROG Ally, Steam Deck) and as a plain Python service for a PC.

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

> **About this project.** SkyPortal is not mine. It is a basic starting
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

## PC / command line

`py_modules/skyportal.py` runs on its own (Python 3, no dependencies):

```
python skyportal.py --library <folder of dumps> [--tcp 127.0.0.1:47150] [--load <dump>]
```

then type `list`, `load <file> [slot]`, `remove <slot>`, `clear`, `dump`, `state` or
`quit`. On Windows it emulates the portal only (no real-portal passthrough).

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
