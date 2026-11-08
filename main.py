"""SkyPortal Decky plugin backend: runs the Portal of Power service for the native
Skylanders: Spyro's Adventure port and exposes it to the quick access panel.

The service (py_modules/skyportal.py) listens on /tmp/skyportal.sock for the game, drives a
real Wii/PS3 portal over usbfs when one is plugged in (needs root, hence the plugin's "root"
flag), and places figures from dumps in ~/Skylanders on a virtual portal.

License: GPL-3.0-or-later.
"""

import asyncio
import logging
import os
import pwd

import decky

import skyportal

LIBRARY_DIR = os.path.join(decky.DECKY_USER_HOME, "Skylanders")
STATE_FILE = os.path.join(decky.DECKY_PLUGIN_SETTINGS_DIR, "state.json")
SOCKET_PATH = os.environ.get("SKYPORTAL_SOCKET", "/tmp/skyportal.sock")


def _user_ids():
    try:
        pw = pwd.getpwnam(decky.DECKY_USER)
        return pw.pw_uid, pw.pw_gid
    except KeyError:
        return None


class Plugin:
    service = None
    dump_progress = None
    dump_result = None

    async def _main(self):
        logging.getLogger("skyportal").setLevel(logging.INFO)
        owner = _user_ids()
        os.makedirs(LIBRARY_DIR, exist_ok=True)
        if owner:
            try:
                os.chown(LIBRARY_DIR, *owner)
            except OSError:
                pass
        Plugin.service = skyportal.PortalService(LIBRARY_DIR, state_file=STATE_FILE,
                                                 socket_path=SOCKET_PATH, file_owner=owner)
        Plugin.service.start()
        decky.logger.info("SkyPortal service started, library %s, socket %s", LIBRARY_DIR,
                          SOCKET_PATH)

    async def _unload(self):
        if Plugin.service:
            Plugin.service.stop()
            Plugin.service = None
        decky.logger.info("SkyPortal service stopped")

    async def _uninstall(self):
        pass

    # ----- calls from the frontend
    async def get_state(self):
        st = Plugin.service.state()
        st["library_dir"] = LIBRARY_DIR
        st["dump_progress"] = Plugin.dump_progress
        st["dump_result"] = Plugin.dump_result
        return st

    async def get_library(self):
        return Plugin.service.library()

    async def get_library_by_element(self):
        return Plugin.service.library_by_element()

    async def place(self, file):
        try:
            slot = Plugin.service.load(file)
            return {"ok": True, "slot": slot}
        except (OSError, ValueError) as e:
            return {"ok": False, "error": str(e)}

    async def lift(self, slot):
        name = Plugin.service.remove(int(slot))
        return {"ok": name is not None, "name": name}

    async def lift_all(self):
        Plugin.service.clear()
        return {"ok": True}

    async def dump(self):
        """Dump the figures on the real portal into ~/Skylanders/Dumped (runs in a thread;
        progress shows up in get_state)."""
        if Plugin.dump_progress is not None:
            return {"ok": False, "error": "a dump is already running"}
        Plugin.dump_progress = "starting"
        Plugin.dump_result = None

        def progress(slot, block):
            Plugin.dump_progress = "slot %d, block %d of 64" % (slot, block + 1)

        def work():
            try:
                done = Plugin.service.dump(progress)
                Plugin.dump_result = {"ok": True, "figures": done}
            except Exception as e:  # noqa: BLE001 - reported to the panel
                Plugin.dump_result = {"ok": False, "error": str(e)}
            finally:
                Plugin.dump_progress = None

        asyncio.get_running_loop().run_in_executor(None, work)
        return {"ok": True}
