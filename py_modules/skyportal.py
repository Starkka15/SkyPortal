"""SkyPortal: Portal of Power service for the native Skylanders: Spyro's Adventure port.

The game (ReXGlue SDK, src/kernel/xam/xam_portal.cpp) connects to this service and
sends/receives raw portal packets.  This service then

  * passes them to a real Wii/PS3 portal (USB 1430:0150) when one is plugged in,
  * emulates a portal from figure dumps (.sky/.dump/.bin, 1024-byte MIFARE images)
    when none is, and
  * layers "virtual" figures from dumps on top of a real portal, so dumps and real
    toys can be used together,
  * dumps the figures standing on a real portal to files.

Game link: frames [u8 type][u8 len][payload].  Game -> service: 'W' packet, 'R' read
request.  Service -> game: 'D' reply to every 'R' (len 0 = nothing to read).
Packets from the 360 game carry a 2-byte header (0B 14) before the Wii/PS3 command.

Real portal: the 1430:0150 receiver rejects interrupt-OUT transfers, and Linux usbhid
always uses that endpoint for output reports, so the portal is driven through usbfs
(usbhid detached, interface claimed): commands as control SET_REPORT(Output),
replies by interrupt-IN reads from endpoint 0x81.  Needs root (the Decky plugin is).
On Windows the portal goes through HID (WinHidPortal), which needs no driver change.

Standalone (PC testing): python skyportal.py --library DIR [--tcp 127.0.0.1:47150]
then type: list | load <file> | remove <slot> | clear | dump | state | quit

License: GPL-3.0-or-later.
"""

import collections
import ctypes
import glob
import json
import logging
import os
import select
import socket
import struct
import sys
import threading
import time

try:
    import fcntl  # Linux: real portal over usbfs
except ImportError:  # Windows: real portal through HID (WinHidPortal)
    fcntl = None
if os.name == "nt":
    import ctypes.wintypes

log = logging.getLogger("skyportal")

SLOTS = 16
HDR360 = b"\x0b\x14"
FIG_SIZE = 1024
DUMP_EXTS = (".sky", ".dump", ".bin", ".dmp")

# Figure id (block 1, bytes 0-1) -> name, Spyro's Adventure.  Chase/series variants
# share the id of the base figure in this game.
NAMES = {
    0: "Whirlwind", 1: "Sonic Boom", 2: "Warnado", 3: "Lightning Rod", 4: "Bash", 5: "Terrafin",
    6: "Dino-Rang", 7: "Prism Break", 8: "Sunburn", 9: "Eruptor", 10: "Ignitor", 11: "Flameslinger",
    12: "Zap", 13: "Wham-Shell", 14: "Gill Grunt", 15: "Slam Bam", 16: "Spyro", 17: "Voodood",
    18: "Double Trouble", 19: "Trigger Happy", 20: "Drobot", 21: "Drill Sergeant", 22: "Boomer",
    23: "Wrecking Ball", 24: "Camo", 25: "Zook", 26: "Stealth Elf", 27: "Stump Smash",
    28: "Dark Spyro", 29: "Hex", 30: "Chop Chop", 31: "Ghost Roaster", 32: "Cynder",
    200: "Anvil Rain", 201: "Hidden Treasure", 202: "Healing Elixir", 203: "Ghost Swords",
    204: "Time Twister", 205: "Sky-Iron Shield", 206: "Winged Boots", 207: "Sparx Dragonfly",
    208: "Dragonfire Cannon", 300: "Dragon's Peak", 301: "Empire of Ice", 302: "Pirate Seas",
    303: "Darklight Crypt", 304: "Volcanic Vault", 404: "Legendary Bash", 416: "Legendary Spyro",
    419: "Legendary Trigger Happy", 430: "Legendary Chop Chop", 505: "Terrabite", 514: "Gill Runt",
    519: "Trigger Snappy", 526: "Whisper Elf",
}

# Figure id -> element (Spyro's Adventure roster; legendaries and sidekicks follow their base
# figure). Ids 200-299 are magic items, 300-399 adventure packs.
ELEMENTS = {}
for _el, _ids in (
        ("Air", (0, 1, 2, 3)),
        ("Earth", (4, 5, 6, 7, 404, 505)),
        ("Fire", (8, 9, 10, 11)),
        ("Water", (12, 13, 14, 15, 514)),
        ("Magic", (16, 17, 18, 23, 28, 416)),
        ("Tech", (19, 20, 21, 22, 419, 519)),
        ("Life", (24, 25, 26, 27, 526)),
        ("Undead", (29, 30, 31, 32, 430))):
    for _i in _ids:
        ELEMENTS[_i] = _el
GROUP_ORDER = ["Air", "Earth", "Fire", "Life", "Magic", "Tech", "Undead", "Water",
               "Magic Items", "Adventure Packs", "Other"]


def figure_group(fid):
    if fid in ELEMENTS:
        return ELEMENTS[fid]
    if 200 <= fid < 300:
        return "Magic Items"
    if 300 <= fid < 400:
        return "Adventure Packs"
    return "Other"


# --------------------------------------------------------------------------- figures

def crc48(data, init=0x9AE903260CC4, poly=0x42F0E1EBA9EA3693):
    crc = init
    for b in data:
        crc ^= b << 40
        for _ in range(8):
            crc = ((crc << 1) ^ poly) if crc & (1 << 47) else (crc << 1)
    return crc & 0xFFFFFFFFFFFF


def key_a(uid, sector):
    """MIFARE key A of a Skylanders tag sector, as stored in the sector trailer."""
    if sector == 0:
        return bytes.fromhex("4B0B20107CCB")
    return crc48(bytes(uid[:4]) + bytes([sector])).to_bytes(6, "little")


def figure_id(data):
    return data[0x10] | data[0x11] << 8


def figure_name(data):
    fid = figure_id(data)
    return NAMES.get(fid, "Figure %d" % fid)


class Figure:
    """A loaded figure: 64 blocks backed by a dump file the game's writes go to."""

    def __init__(self, path):
        self.path = os.path.abspath(path)
        with open(self.path, "rb") as f:
            data = f.read()
        if len(data) < FIG_SIZE:
            data += bytes(FIG_SIZE - len(data))
        self.data = bytearray(data[:FIG_SIZE])
        self.dirty_since = 0.0

    @property
    def name(self):
        return os.path.splitext(os.path.basename(self.path))[0]

    def read_block(self, block):
        return bytes(self.data[block * 16:block * 16 + 16])

    def write_block(self, block, payload):
        self.data[block * 16:block * 16 + 16] = payload[:16]
        if not self.dirty_since:
            self.dirty_since = time.monotonic()

    def flush(self):
        if not self.dirty_since:
            return
        tmp = self.path + ".tmp"
        with open(tmp, "wb") as f:
            f.write(self.data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self.path)
        self.dirty_since = 0.0
        log.info("saved %s", self.path)


# --------------------------------------------------------------------------- emulator

class Emulator:
    """Wii/PS3 portal behaviour, as observed on a real 1430:0150 portal:
    R -> 52 01 3D, A n -> 41 n FF 77, C/L no reply, S -> status, Q/W on a figure ->
    51/57 (10|slot) block [data], Q on an empty slot -> 51 slot block.
    Status: 2 bits per slot (0 none, 1 present, 3 just added, 2 just removed),
    then a counter byte and the active flag."""

    NONE, PRESENT, REMOVED, ADDED = 0, 1, 2, 3

    def __init__(self):
        self.figures = [None] * SLOTS
        self.status = [self.NONE] * SLOTS
        self.pending = [collections.deque() for _ in range(SLOTS)]
        self.active = False
        self.counter = 0
        self.replies = collections.deque()

    def place(self, slot, fig):
        self.figures[slot] = fig
        self.pending[slot].extend([self.ADDED, self.PRESENT])

    def lift(self, slot):
        fig = self.figures[slot]
        self.figures[slot] = None
        self.pending[slot].extend([self.REMOVED, self.NONE])
        return fig

    def slot_bits(self, slot):
        """Advance and return this slot's status (one step per status packet)."""
        if self.pending[slot]:
            self.status[slot] = self.pending[slot].popleft()
        return self.status[slot]

    def status_packet(self, base_bits=0, slots=range(SLOTS)):
        bits = base_bits
        for s in slots:
            bits &= ~(3 << (2 * s))
            bits |= self.slot_bits(s) << (2 * s)
        self.counter = (self.counter + 1) & 0xFF
        return bytes([0x53]) + struct.pack("<I", bits) + bytes([self.counter, 1 if self.active else 0])

    def command(self, cmd):
        """Handle one command; replies go to self.replies."""
        if not cmd:
            return
        c = cmd[0]
        if c == 0x52:  # 'R' reset
            self.active = False
            self.replies.append(b"\x52\x01\x3d")
        elif c == 0x41:  # 'A' activate
            self.active = bool(cmd[1]) if len(cmd) > 1 else True
            self.replies.append(bytes([0x41, cmd[1] if len(cmd) > 1 else 1, 0xFF, 0x77]))
        elif c == 0x51:  # 'Q' query block
            slot, block = cmd[1] & 0x0F, cmd[2] & 0x3F
            fig = self.figures[slot]
            if fig is not None:
                self.replies.append(bytes([0x51, 0x10 | slot, block]) + fig.read_block(block))
            else:
                self.replies.append(bytes([0x51, slot, block]))
        elif c == 0x57:  # 'W' write block
            slot, block = cmd[1] & 0x0F, cmd[2] & 0x3F
            fig = self.figures[slot]
            if fig is not None and len(cmd) >= 19:
                fig.write_block(block, cmd[3:19])
                self.replies.append(bytes([0x57, 0x10 | slot, block]))
            else:
                self.replies.append(bytes([0x57, slot, block]))
        elif c == 0x53:  # 'S' status
            self.replies.append(self.status_packet())
        elif c == 0x4A:  # 'J' (later games) - acknowledge
            self.replies.append(b"\x4a")
        elif c == 0x4D:  # 'M' speaker (later games)
            self.replies.append(bytes([0x4D, cmd[1] if len(cmd) > 1 else 0, 0x00, 0x19]))
        # 'C' colour, 'L' lights and anything else: no reply.


# --------------------------------------------------------------------------- real portal

def _ioc(direction, nr, size):
    return (direction << 30) | (size << 16) | (ord("U") << 8) | nr


USBDEVFS_CONTROL = _ioc(3, 0, struct.calcsize("BBHHHIP"))
USBDEVFS_BULK = _ioc(3, 2, struct.calcsize("IIIP"))
USBDEVFS_CLAIMINTERFACE = _ioc(2, 15, 4)
USBDEVFS_RELEASEINTERFACE = _ioc(2, 16, 4)
USBDEVFS_IOCTL = _ioc(3, 18, struct.calcsize("iiP"))
USBDEVFS_DISCONNECT = (ord("U") << 8) | 22
USBDEVFS_CONNECT = (ord("U") << 8) | 23


def find_portal_node():
    """/dev/bus/usb/BBB/DDD of a Wii/PS3 portal, or None."""
    for d in glob.glob("/sys/bus/usb/devices/*"):
        try:
            with open(d + "/idVendor") as f:
                vid = f.read().strip()
            with open(d + "/idProduct") as f:
                pid = f.read().strip()
            if vid == "1430" and pid == "0150":
                with open(d + "/busnum") as f:
                    bus = int(f.read())
                with open(d + "/devnum") as f:
                    dev = int(f.read())
                return "/dev/bus/usb/%03d/%03d" % (bus, dev)
        except (OSError, ValueError):
            continue
    return None


class UsbPortal:
    def __init__(self, node):
        self.node = node
        self.fd = os.open(node, os.O_RDWR)
        self._ifioctl(USBDEVFS_DISCONNECT)  # take interface 0 away from usbhid
        fcntl.ioctl(self.fd, USBDEVFS_CLAIMINTERFACE, struct.pack("I", 0))
        self.closed = False

    def _ifioctl(self, code):
        try:
            fcntl.ioctl(self.fd, USBDEVFS_IOCTL, struct.pack("iiP", 0, code, 0))
        except OSError:
            pass  # no driver bound / already connected

    def write(self, cmd):
        buf = ctypes.create_string_buffer(bytes(cmd[:32]).ljust(32, b"\0"), 32)
        fcntl.ioctl(self.fd, USBDEVFS_CONTROL,
                    struct.pack("BBHHHIP", 0x21, 0x09, 0x0200, 0, 32, 500, ctypes.addressof(buf)))

    def read(self, timeout_ms=100):
        """One interrupt-IN report, None on timeout.  Raises OSError when unplugged."""
        buf = ctypes.create_string_buffer(32)
        try:
            n = fcntl.ioctl(self.fd, USBDEVFS_BULK,
                            struct.pack("IIIP", 0x81, 32, timeout_ms, ctypes.addressof(buf)))
        except OSError as e:
            if e.errno in (110, 62):  # ETIMEDOUT / ETIME
                return None
            raise
        return buf.raw[:n] if isinstance(n, int) and n > 0 else buf.raw

    def close(self):
        if self.closed:
            return
        self.closed = True
        try:
            fcntl.ioctl(self.fd, USBDEVFS_RELEASEINTERFACE, struct.pack("I", 0))
        except OSError:
            pass
        self._ifioctl(USBDEVFS_CONNECT)  # give it back to usbhid
        try:
            os.close(self.fd)
        except OSError:
            pass


class WinHidPortal:
    """The portal through Windows HID. HidD_SetOutputReport always goes over the control
    pipe (SET_REPORT), which the receiver accepts, and replies are interrupt-IN input
    reports read with overlapped ReadFile. No driver change is needed. Reports carry a
    leading report-id byte (0) that is added and stripped here."""

    _api = None

    @classmethod
    def _load(cls):
        if cls._api is not None:
            return cls._api
        W = ctypes.wintypes

        class GUID(ctypes.Structure):
            _fields_ = [("d1", W.DWORD), ("d2", W.WORD), ("d3", W.WORD), ("d4", ctypes.c_ubyte * 8)]

        class IFDATA(ctypes.Structure):
            _fields_ = [("cbSize", W.DWORD), ("guid", GUID), ("flags", W.DWORD),
                        ("reserved", ctypes.c_void_p)]

        class CAPS(ctypes.Structure):
            _fields_ = [("Usage", W.USHORT), ("UsagePage", W.USHORT), ("In", W.USHORT),
                        ("Out", W.USHORT), ("Feat", W.USHORT), ("rest", W.USHORT * 27)]

        class OVERLAPPED(ctypes.Structure):
            _fields_ = [("Internal", ctypes.c_void_p), ("InternalHigh", ctypes.c_void_p),
                        ("Offset", W.DWORD), ("OffsetHigh", W.DWORD), ("hEvent", W.HANDLE)]

        sa = ctypes.WinDLL("setupapi", use_last_error=True)
        hid = ctypes.WinDLL("hid", use_last_error=True)
        k = ctypes.WinDLL("kernel32", use_last_error=True)
        sa.SetupDiGetClassDevsW.restype = W.HANDLE
        sa.SetupDiGetClassDevsW.argtypes = [ctypes.POINTER(GUID), W.LPCWSTR, W.HWND, W.DWORD]
        sa.SetupDiEnumDeviceInterfaces.argtypes = [W.HANDLE, ctypes.c_void_p, ctypes.POINTER(GUID),
                                                   W.DWORD, ctypes.POINTER(IFDATA)]
        sa.SetupDiGetDeviceInterfaceDetailW.argtypes = [W.HANDLE, ctypes.POINTER(IFDATA), ctypes.c_void_p,
                                                        W.DWORD, ctypes.POINTER(W.DWORD), ctypes.c_void_p]
        sa.SetupDiDestroyDeviceInfoList.argtypes = [W.HANDLE]
        k.CreateFileW.restype = W.HANDLE
        k.CreateFileW.argtypes = [W.LPCWSTR, W.DWORD, W.DWORD, ctypes.c_void_p, W.DWORD, W.DWORD, W.HANDLE]
        k.CreateEventW.restype = W.HANDLE
        k.CreateEventW.argtypes = [ctypes.c_void_p, W.BOOL, W.BOOL, W.LPCWSTR]
        k.ReadFile.argtypes = [W.HANDLE, ctypes.c_void_p, W.DWORD, ctypes.c_void_p, ctypes.POINTER(OVERLAPPED)]
        k.GetOverlappedResult.argtypes = [W.HANDLE, ctypes.POINTER(OVERLAPPED), ctypes.POINTER(W.DWORD), W.BOOL]
        k.WaitForSingleObject.argtypes = [W.HANDLE, W.DWORD]
        k.WaitForSingleObject.restype = W.DWORD
        k.CancelIoEx.argtypes = [W.HANDLE, ctypes.c_void_p]
        k.CloseHandle.argtypes = [W.HANDLE]
        hid.HidD_GetHidGuid.argtypes = [ctypes.POINTER(GUID)]
        hid.HidD_SetOutputReport.argtypes = [W.HANDLE, ctypes.c_void_p, W.ULONG]
        hid.HidD_GetPreparsedData.argtypes = [W.HANDLE, ctypes.POINTER(ctypes.c_void_p)]
        hid.HidD_FreePreparsedData.argtypes = [ctypes.c_void_p]
        hid.HidP_GetCaps.argtypes = [ctypes.c_void_p, ctypes.POINTER(CAPS)]
        cls._api = dict(sa=sa, hid=hid, k=k, GUID=GUID, IFDATA=IFDATA, CAPS=CAPS, OVERLAPPED=OVERLAPPED)
        return cls._api

    @classmethod
    def find(cls):
        """Device path of a Wii/PS3 portal's HID interface, or None."""
        a = cls._load()
        W = ctypes.wintypes
        g = a["GUID"]()
        a["hid"].HidD_GetHidGuid(ctypes.byref(g))
        info = a["sa"].SetupDiGetClassDevsW(ctypes.byref(g), None, None, 0x12)  # PRESENT|INTERFACE
        if not info or info == W.HANDLE(-1).value:
            return None
        try:
            i = 0
            while True:
                d = a["IFDATA"]()
                d.cbSize = ctypes.sizeof(d)
                if not a["sa"].SetupDiEnumDeviceInterfaces(info, None, ctypes.byref(g), i, ctypes.byref(d)):
                    return None
                i += 1
                need = W.DWORD()
                a["sa"].SetupDiGetDeviceInterfaceDetailW(info, ctypes.byref(d), None, 0, ctypes.byref(need), None)
                if need.value < 8:
                    continue
                buf = ctypes.create_string_buffer(need.value)
                # SP_DEVICE_INTERFACE_DETAIL_DATA_W.cbSize: 8 on 64-bit, 6 on 32-bit.
                ctypes.cast(buf, ctypes.POINTER(W.DWORD))[0] = 8 if ctypes.sizeof(ctypes.c_void_p) == 8 else 6
                if a["sa"].SetupDiGetDeviceInterfaceDetailW(info, ctypes.byref(d), buf, need, None, None):
                    path = ctypes.wstring_at(ctypes.addressof(buf) + 4)
                    if "vid_1430&pid_0150" in path.lower():
                        return path
        finally:
            a["sa"].SetupDiDestroyDeviceInfoList(info)

    def __init__(self, path):
        a = self._load()
        W = ctypes.wintypes
        self.a = a
        self.node = path
        h = a["k"].CreateFileW(path, 0xC0000000, 3, None, 3, 0x40000000, None)  # R/W, share R/W, overlapped
        if not h or h == W.HANDLE(-1).value:
            raise OSError(ctypes.get_last_error(), "cannot open the portal")
        self.h = h
        pp = ctypes.c_void_p()
        caps = a["CAPS"]()
        if a["hid"].HidD_GetPreparsedData(h, ctypes.byref(pp)):
            a["hid"].HidP_GetCaps(pp, ctypes.byref(caps))
            a["hid"].HidD_FreePreparsedData(pp)
        self.in_len = caps.In or 33
        self.out_len = caps.Out or 33
        self.ev = a["k"].CreateEventW(None, True, False, None)
        self.ov = a["OVERLAPPED"]()
        self.rbuf = ctypes.create_string_buffer(self.in_len)
        self.pending = False
        self.closed = False

    def write(self, cmd):
        buf = ctypes.create_string_buffer(b"\0" + bytes(cmd[:self.out_len - 1]).ljust(self.out_len - 1, b"\0"),
                                          self.out_len)
        if not self.a["hid"].HidD_SetOutputReport(self.h, buf, self.out_len):
            raise OSError(ctypes.get_last_error(), "portal write failed")

    def read(self, timeout_ms=100):
        """One input report without its report id, None on timeout. Raises OSError when
        unplugged. A read that times out stays queued for the next call, so no report is
        lost between calls."""
        k = self.a["k"]
        W = ctypes.wintypes
        if not self.pending:
            self.ov = self.a["OVERLAPPED"]()
            self.ov.hEvent = self.ev
            if not k.ReadFile(self.h, self.rbuf, self.in_len, None, ctypes.byref(self.ov)):
                err = ctypes.get_last_error()
                if err != 997:  # ERROR_IO_PENDING
                    raise OSError(err, "portal read failed")
            self.pending = True
        if k.WaitForSingleObject(self.ev, timeout_ms) != 0:
            return None
        self.pending = False
        n = W.DWORD()
        if not k.GetOverlappedResult(self.h, ctypes.byref(self.ov), ctypes.byref(n), False):
            raise OSError(ctypes.get_last_error(), "portal read failed")
        return self.rbuf.raw[1:n.value] if n.value > 1 else None

    def close(self):
        if self.closed:
            return
        self.closed = True
        k = self.a["k"]
        if self.pending:
            k.CancelIoEx(self.h, None)
            n = ctypes.wintypes.DWORD()
            k.GetOverlappedResult(self.h, ctypes.byref(self.ov), ctypes.byref(n), True)
        k.CloseHandle(self.h)
        k.CloseHandle(self.ev)


def open_real_portal():
    """(portal, where) for a plugged-in portal, (None, None) if there is none.
    Raises OSError when one is there but cannot be opened."""
    if os.name == "nt":
        path = WinHidPortal.find()
        return (WinHidPortal(path), "HID") if path else (None, None)
    if fcntl is None:
        return None, None
    node = find_portal_node()
    return (UsbPortal(node), node) if node else (None, None)


# --------------------------------------------------------------------------- service

def _merge_status(older, newer):
    """Carry 'added'/'removed' bits of a dropped status packet into the next one."""
    ob = struct.unpack("<I", older[1:5])[0]
    nb = struct.unpack("<I", newer[1:5])[0]
    for s in range(SLOTS):
        o, n = (ob >> 2 * s) & 3, (nb >> 2 * s) & 3
        if o & 2 and not n & 2 and (o & 1) == (n & 1):
            nb |= 2 << 2 * s
    return newer[:1] + struct.pack("<I", nb) + newer[5:]


class PortalService:
    def __init__(self, library_dir, state_file=None, socket_path=None, tcp=None,
                 file_owner=None, use_real=True):
        self.library_dir = os.path.abspath(library_dir)
        self.dump_dir = os.path.join(self.library_dir, "Dumped")
        self.state_file = state_file
        self.socket_path = socket_path
        self.tcp = tcp
        self.file_owner = file_owner  # (uid, gid) for files written as root
        self.use_real = use_real
        self.lock = threading.RLock()
        self.emu = Emulator()
        self.virtual = set()  # slots holding figures from dumps
        self.real = None
        self.real_queue = collections.deque()
        self.real_bits = 0  # last status bits from the real portal
        self.real_status_time = 0.0  # monotonic time of the last status from it
        self.real_names = {}  # slot -> figure name of a toy on the real portal
        self.real_asked = {}  # slot -> monotonic time we last asked for its id block
        self.real_trace = 0  # non-status packets logged so far
        self.real_error = ""
        self.dumping = False
        self.dump_sink = None
        self.held_writes = []
        self.game_header = True
        self.game_clients = 0
        self.last_game_io = 0.0
        self.running = False
        self.threads = []
        self.server = None
        self.events = collections.deque(maxlen=50)

    # ----- lifecycle
    def start(self):
        os.makedirs(self.library_dir, exist_ok=True)
        self.running = True
        self._restore_state()
        self._serve()
        for fn in (self._flusher, self._real_manager):
            t = threading.Thread(target=fn, daemon=True, name=fn.__name__)
            t.start()
            self.threads.append(t)

    def stop(self):
        self.running = False
        try:
            if self.server:
                self.server.close()
        except OSError:
            pass
        if self.socket_path:
            try:
                os.unlink(self.socket_path)
            except OSError:
                pass
        with self.lock:
            for f in self.emu.figures:
                if f is not None:
                    f.flush()
            if self.real:
                self.real.close()
                self.real = None

    def _event(self, msg):
        log.info(msg)
        self.events.append((time.time(), msg))

    # ----- game socket
    def _serve(self):
        if self.tcp:
            host, port = self.tcp.rsplit(":", 1)
            srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            srv.bind((host, int(port)))
            where = self.tcp
        else:
            try:
                os.unlink(self.socket_path)
            except OSError:
                pass
            srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            srv.bind(self.socket_path)
            os.chmod(self.socket_path, 0o666)  # the game runs as the desktop user
            where = self.socket_path
        srv.listen(4)
        self.server = srv
        log.info("listening on %s", where)
        t = threading.Thread(target=self._accept_loop, daemon=True, name="accept")
        t.start()
        self.threads.append(t)

    def _accept_loop(self):
        while self.running:
            try:
                conn, _ = self.server.accept()
            except OSError:
                if not self.running:
                    return
                time.sleep(0.2)
                continue
            threading.Thread(target=self._client, args=(conn,), daemon=True, name="client").start()

    def _client(self, conn):
        if conn.family == socket.AF_INET:
            conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        with self.lock:
            self.game_clients += 1
        self._event("game connected")
        buf = b""
        try:
            while self.running:
                chunk = conn.recv(4096)
                if not chunk:
                    break
                buf += chunk
                while len(buf) >= 2 and len(buf) >= 2 + buf[1]:
                    typ, n = buf[0], buf[1]
                    payload, buf = buf[2:2 + n], buf[2 + n:]
                    if typ == 0x57:  # 'W'
                        self.game_write(payload)
                    elif typ == 0x52:  # 'R'
                        pkt = self.game_read()
                        conn.sendall(bytes([0x44, len(pkt)]) + pkt)
        except OSError:
            pass
        finally:
            conn.close()
            with self.lock:
                self.game_clients -= 1
            self._event("game disconnected")

    def game_write(self, pkt):
        with self.lock:
            self.last_game_io = time.monotonic()
            if pkt[:2] == HDR360:
                self.game_header = True
                cmd = pkt[2:]
            else:
                self.game_header = False
                cmd = pkt
            if not cmd:
                return
            c = cmd[0]
            slot = cmd[1] & 0x0F if len(cmd) > 1 else 0
            if c in (0x51, 0x57) and (slot in self.virtual or not self.real):
                self.emu.command(cmd)  # figure from a dump, or no real portal
                return
            if c in (0x52, 0x41):
                self.emu.command(cmd)  # keep the emulator's active state in step
                if not self.real:
                    return
                self.emu.replies.pop()  # the real portal answers these itself
                if c == 0x52:
                    # Drop what the portal sent while the service kept it active on its own,
                    # so the game's first read is the reply to its own reset.
                    self.real_queue.clear()
            elif not self.real:
                self.emu.command(cmd)
                return
            if self.dumping:
                self.held_writes.append(cmd)
                return
            self._real_write(cmd)

    def _real_write(self, cmd):
        try:
            self.real.write(cmd)
        except OSError as e:
            self._drop_real("write failed: %s" % e)

    def game_read(self):
        with self.lock:
            self.last_game_io = time.monotonic()
            pkt = self._next_packet()
            if pkt is None:
                return b""
            return (HDR360 + pkt)[:32] if self.game_header else pkt[:32]

    def _next_packet(self):
        if self.emu.replies:
            return self.emu.replies.popleft()
        if self.real:
            if self.dumping:
                return None
            q = self.real_queue
            # Keep latency down: fold runs of status packets into the newest one.
            while len(q) > 1 and q[0][0] == 0x53 and any(p[0] == 0x53 for p in list(q)[1:]):
                old = q.popleft()
                for i, p in enumerate(q):
                    if p[0] == 0x53:
                        q[i] = _merge_status(old, p)
                        break
            if not q:
                return None
            pkt = q.popleft()
            if pkt[0] == 0x53 and self.virtual:
                bits = struct.unpack("<I", pkt[1:5])[0]
                pkt = self.emu.status_packet(bits, sorted(self.virtual))[:5] + pkt[5:]
            return pkt
        if self.emu.active:
            return self.emu.status_packet()
        return None

    # ----- real portal
    def _real_manager(self):
        while self.running:
            if self.use_real and self.real is None:
                try:
                    portal, node = open_real_portal()
                except OSError as e:
                    if self.real_error != str(e):
                        self.real_error = str(e)
                        self._event("cannot open the portal: %s" % e)
                    portal = None
                if portal:
                    with self.lock:
                        self.real = portal
                        self.real_queue.clear()
                        self.real_error = ""
                        self._event("real portal connected (%s)" % node)
                        # Activate it right away: a portal sends status (which toys are
                        # on it) only after Reset + Activate, and the panel should show
                        # the toys before the game runs. The game resets it again itself.
                        self._real_write(b"\x52")
                        self._real_write(b"\x41\x01")
                    threading.Thread(target=self._real_reader, args=(portal,), daemon=True,
                                     name="real-reader").start()
            time.sleep(1.0)

    def _real_reader(self, portal):
        while self.running and not portal.closed:
            try:
                pkt = portal.read(100)
            except OSError as e:
                with self.lock:
                    if self.real is portal:
                        self._drop_real("read failed: %s" % e)
                return
            if not pkt:
                continue
            with self.lock:
                if pkt[0] == 0x53:
                    bits = struct.unpack("<I", pkt[1:5])[0]
                    if bits != self.real_bits:
                        log.info("real portal status %08X", bits)  # trace
                    self.real_bits = bits
                    self.real_status_time = time.monotonic()
                    self._name_real_toys()
                elif self.real_trace < 20:
                    self.real_trace += 1
                    log.info("real portal sent %s", pkt[:20].hex())  # trace (first 20)
                if pkt[0] == 0x51 and len(pkt) >= 5 and pkt[1] & 0x10 and pkt[2] == 1:
                    # Block 1 of a toy (ours or the game's read): bytes 0-1 are the figure id.
                    fid = pkt[3] | pkt[4] << 8
                    slot = pkt[1] & 0x0F
                    if (self.real_bits >> 2 * slot) & 1:
                        name = NAMES.get(fid, "Figure %d" % fid)
                        if self.real_names.get(slot) != name:
                            self.real_names[slot] = name
                            self._event("%s on the real portal (slot %d)" % (name, slot))
                if self.dump_sink is not None:
                    self.dump_sink.append(pkt)
                    continue
                if pkt[0] in (0x52, 0x41) and self.emu.replies:
                    pass
                self.real_queue.append(pkt)
                while len(self.real_queue) > 64:
                    self.real_queue.popleft()

    def _name_real_toys(self):
        """Forget toys that left the real portal and ask for block 1 of new ones. Only while
        no game is connected (the game's own block reads name them then) and not dumping.
        RF reads fail now and then, so ask again every half second until one answers."""
        now = time.monotonic()
        for s in range(SLOTS):
            if not (self.real_bits >> 2 * s) & 1:
                self.real_names.pop(s, None)
                self.real_asked.pop(s, None)
                continue
            if s in self.real_names or self.game_clients or self.dumping:
                continue
            if now - self.real_asked.get(s, 0.0) >= 0.5:
                self.real_asked[s] = now
                self._real_write(bytes([0x51, 0x10 | s, 0x01]))

    def _drop_real(self, why):
        if self.real:
            self.real.close()
            self.real = None
            self.real_queue.clear()
            self.real_bits = 0
            self.real_names.clear()
            self.real_asked.clear()
            self._event("real portal disconnected (%s)" % why)

    # ----- library / figures
    def library(self):
        """Folders and figure files in the library, relative paths."""
        out = []
        for root, dirs, files in os.walk(self.library_dir):
            dirs.sort()
            figs = sorted(f for f in files if f.lower().endswith(DUMP_EXTS))
            if figs:
                rel = os.path.relpath(root, self.library_dir)
                out.append({"folder": "" if rel == "." else rel.replace(os.sep, "/"),
                            "figures": [os.path.splitext(f)[0] for f in figs],
                            "files": figs})
        return out

    def library_by_element(self):
        """Every figure file grouped by element (from the figure id in block 1), in
        GROUP_ORDER: [{"group", "figures": [{"name", "file", "folder", "kind"}]}]."""
        groups = collections.defaultdict(list)
        for root, dirs, files in os.walk(self.library_dir):
            dirs.sort()
            rel_dir = os.path.relpath(root, self.library_dir)
            folder = "" if rel_dir == "." else rel_dir.replace(os.sep, "/")
            for f in sorted(files):
                if not f.lower().endswith(DUMP_EXTS):
                    continue
                try:
                    with open(os.path.join(root, f), "rb") as fh:
                        head = fh.read(0x12)
                except OSError:
                    continue
                fid = head[0x10] | head[0x11] << 8 if len(head) >= 0x12 else -1
                groups[figure_group(fid)].append({
                    "name": os.path.splitext(f)[0],
                    "file": (folder + "/" + f) if folder else f,
                    "folder": folder,
                    "kind": NAMES.get(fid, "Figure %d" % fid),
                })
        out = []
        for g in GROUP_ORDER:
            if groups.get(g):
                out.append({"group": g,
                            "figures": sorted(groups[g], key=lambda x: (x["name"].lower(), x["folder"]))})
        return out

    def _library_rel(self, path):
        """path relative to the library, with / separators, or the full path when it lies
        outside it. A figure placed from another folder, or restored from an earlier
        library, may be on another drive, where relpath raises on Windows."""
        try:
            rel = os.path.relpath(path, self.library_dir)
        except ValueError:
            return path
        return path if rel.startswith("..") else rel.replace(os.sep, "/")

    def _resolve(self, path):
        p = path if os.path.isabs(path) else os.path.join(self.library_dir, path)
        p = os.path.abspath(p)
        return p

    def load(self, path, slot=None):
        p = self._resolve(path)
        fig = Figure(p)
        with self.lock:
            for s, f in enumerate(self.emu.figures):
                if f is not None and f.path == fig.path:
                    raise ValueError("%s is already on the portal (slot %d)" % (fig.name, s))
            if slot is None:
                slot = self._free_slot()
            if self.emu.figures[slot] is not None:
                self.remove(slot)
            self.emu.place(slot, fig)
            self.virtual.add(slot)
            self._event("placed %s in slot %d" % (fig.name, slot))
            self._save_state()
            return slot

    def _free_slot(self):
        # From the top down: a real portal hands out slots from 0 up.
        for s in range(SLOTS - 1, -1, -1):
            if self.emu.figures[s] is None and not (self.real and (self.real_bits >> 2 * s) & 1):
                return s
        raise ValueError("the portal is full")

    def remove(self, slot):
        with self.lock:
            fig = self.emu.figures[slot]
            if fig is None:
                return None
            self.emu.lift(slot)
            fig.flush()
            self._event("lifted %s from slot %d" % (fig.name, slot))
            self._save_state()
            # Keep the slot marked virtual until the 'removed' status has been sent.
            return fig.name

    def clear(self):
        with self.lock:
            for s in range(SLOTS):
                if self.emu.figures[s] is not None:
                    self.remove(s)

    def state(self):
        with self.lock:
            loaded = []
            for s, f in enumerate(self.emu.figures):
                if f is not None:
                    loaded.append({"slot": s, "name": f.name,
                                   "file": self._library_rel(f.path),
                                   "id": figure_id(f.data), "kind": figure_name(f.data)})
            real_slots = [s for s in range(SLOTS) if (self.real_bits >> 2 * s) & 1] if self.real else []
            return {
                "real_portal": self.real is not None,
                "real_error": self.real_error,
                "real_slots": real_slots,
                "real_toys": [{"slot": s, "name": self.real_names.get(s, "")} for s in real_slots],
                "game_connected": self.game_clients > 0,
                "active": self.emu.active,
                "dumping": self.dumping,
                "loaded": loaded,
                "events": [m for _, m in list(self.events)[-8:]],
            }

    def _flusher(self):
        while self.running:
            time.sleep(0.5)
            with self.lock:
                now = time.monotonic()
                for f in self.emu.figures:
                    if f is not None and f.dirty_since and now - f.dirty_since > 1.0:
                        try:
                            f.flush()
                            self._chown(f.path)
                        except OSError as e:
                            log.error("saving %s failed: %s", f.path, e)
                # Forget lifted slots once their 'removed' status went out.
                for s in list(self.virtual):
                    if self.emu.figures[s] is None and not self.emu.pending[s]:
                        self.virtual.discard(s)

    def _chown(self, path):
        if self.file_owner:
            try:
                os.chown(path, *self.file_owner)
            except OSError:
                pass

    def _save_state(self):
        if not self.state_file:
            return
        loaded = {str(s): f.path for s, f in enumerate(self.emu.figures) if f is not None}
        try:
            with open(self.state_file + ".tmp", "w") as f:
                json.dump({"loaded": loaded}, f)
            os.replace(self.state_file + ".tmp", self.state_file)
            self._chown(self.state_file)
        except OSError as e:
            log.error("saving state failed: %s", e)

    def _restore_state(self):
        if not self.state_file or not os.path.exists(self.state_file):
            return
        try:
            with open(self.state_file) as f:
                st = json.load(f)
            for s, p in st.get("loaded", {}).items():
                if os.path.exists(p):
                    fig = Figure(p)
                    self.emu.figures[int(s)] = fig
                    self.emu.status[int(s)] = Emulator.PRESENT
                    self.virtual.add(int(s))
        except (OSError, ValueError) as e:
            log.error("restoring state failed: %s", e)

    # ----- dumping
    def dump(self, progress=None):
        """Read every figure on the real portal into library/Dumped/<name>.sky.
        Returns a list of {"name", "file", "slot"}; raises on failure."""
        with self.lock:
            if not self.real:
                raise RuntimeError("no real portal is plugged in")
            if self.dumping:
                raise RuntimeError("a dump is already running")
            self.dumping = True
            self.dump_sink = collections.deque()
            game_active = self.emu.active
        try:
            # No reset: after one, the wireless portal sometimes stops seeing a toy that is
            # already standing on it (status 0 until the toy is lifted and put back). The
            # service activated the portal when it connected and keeps it active, so it is
            # only activated here if it has gone quiet.
            if not game_active and time.monotonic() - self.real_status_time > 1.0:
                self._dump_cmd(b"\x41\x01")
                time.sleep(0.5)
            status = self._dump_status()
            if status is None:
                raise RuntimeError("the portal sent no status")
            bits = struct.unpack("<I", status[1:5])[0]
            slots = [s for s in range(SLOTS) if (bits >> 2 * s) & 1 and s not in self.virtual]
            if not slots:
                raise RuntimeError("no figures on the portal")
            os.makedirs(self.dump_dir, exist_ok=True)
            self._chown(self.dump_dir)
            results = []
            for slot in slots:
                data = bytearray()
                for block in range(64):
                    if progress:
                        progress(slot, block)
                    got = None
                    for _ in range(8):
                        r = self._dump_wait(lambda p, b=block: p[0] == 0x51 and p[2] == b, 0.3,
                                            resend=bytes([0x51, 0x10 | slot, block]))
                        if r is not None and r[1] & 0x10 and len(r) >= 19:
                            got = r[3:19]
                            break
                    if got is None:
                        raise RuntimeError("slot %d: block %d would not read" % (slot, block))
                    data += got
                uid = data[0:4]
                for sector in range(16):  # the portal hides the keys; put key A back
                    t = sector * 64 + 48
                    data[t:t + 6] = key_a(uid, sector)
                    data[t + 10:t + 16] = bytes(6)
                name = figure_name(data)
                path = os.path.join(self.dump_dir, "%s.sky" % name)
                if os.path.exists(path):
                    with open(path, "rb") as f:
                        same = f.read(4) == bytes(uid)
                    if not same:
                        path = os.path.join(self.dump_dir, "%s %s.sky" % (name, bytes(uid).hex().upper()))
                tmp = path + ".tmp"
                with open(tmp, "wb") as f:
                    f.write(data)
                os.replace(tmp, path)
                self._chown(path)
                self._event("dumped %s (slot %d) to %s" % (name, slot, os.path.basename(path)))
                results.append({"name": name, "slot": slot,
                                "file": os.path.relpath(path, self.library_dir).replace(os.sep, "/")})
            return results
        finally:
            with self.lock:
                # The portal stays active (the service keeps it so for the status view).
                self.dump_sink = None
                self.dumping = False
                held, self.held_writes = self.held_writes, []
                for cmd in held:
                    if self.real:
                        self._real_write(cmd)

    def _dump_status(self):
        """The portal's current status, for which slots hold toys. A reset numbers the toys
        again from slot 0 (a toy in slot 1 before it can be in slot 0 after), and the sink
        still holds status from before the reset, so drop what is queued, ask afresh and
        keep the newest status that arrives."""
        with self.lock:
            if self.dump_sink is not None:
                self.dump_sink.clear()
        newest = None
        end = time.monotonic() + 1.0
        self._dump_cmd(b"\x53")
        while time.monotonic() < end:
            with self.lock:
                sink = self.dump_sink
                while sink:
                    p = sink.popleft()
                    if p[0] == 0x53:
                        newest = p
            if newest is not None and time.monotonic() > end - 0.7:
                break  # waited 0.3 s for anything newer
            time.sleep(0.01)
        return newest

    def _dump_cmd(self, cmd):
        with self.lock:
            if not self.real:
                raise RuntimeError("the portal was unplugged")
            self.real.write(cmd)

    def _dump_wait(self, match, timeout, resend=None):
        if resend:
            self._dump_cmd(resend)
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            with self.lock:
                sink = self.dump_sink
                while sink:
                    p = sink.popleft()
                    if match(p):
                        return p
            time.sleep(0.005)
        return None


# --------------------------------------------------------------------------- CLI

def _selftest(library):
    ok = bad = 0
    for p in glob.glob(os.path.join(library, "**", "*"), recursive=True):
        if not p.lower().endswith(DUMP_EXTS):
            continue
        d = open(p, "rb").read()
        for s in range(16):
            if d[s * 64 + 48:s * 64 + 54] == key_a(d[:4], s):
                ok += 1
            else:
                bad += 1
    print("key A check: %d trailers match, %d differ" % (ok, bad))


def main():
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--library", required=True)
    ap.add_argument("--tcp", default=None, help="host:port (default on Windows 127.0.0.1:47150)")
    ap.add_argument("--socket", default="/tmp/skyportal.sock")
    ap.add_argument("--state", default=None)
    ap.add_argument("--no-real", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--load", action="append", default=[], help="figure file to place at start")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    if a.selftest:
        _selftest(a.library)
        return
    tcp = a.tcp or ("127.0.0.1:47150" if os.name == "nt" else None)
    svc = PortalService(a.library, state_file=a.state, socket_path=a.socket, tcp=tcp,
                        use_real=not a.no_real)
    svc.start()
    for p in a.load:
        print("placed in slot", svc.load(p))
    print("commands: list | load <file> [slot] | remove <slot> | clear | dump | state | quit")
    for line in sys.stdin:
        parts = line.strip().split(" ", 1)
        cmd, arg = parts[0], parts[1] if len(parts) > 1 else ""
        try:
            if cmd == "list":
                for entry in svc.library():
                    print("[%s]" % (entry["folder"] or "."), ", ".join(entry["figures"]))
            elif cmd == "load":
                print("slot", svc.load(arg))
            elif cmd == "remove":
                print(svc.remove(int(arg)))
            elif cmd == "clear":
                svc.clear()
            elif cmd == "dump":
                print(svc.dump())
            elif cmd == "state":
                print(json.dumps(svc.state(), indent=1))
            elif cmd in ("quit", "exit"):
                svc.stop()
                return
        except Exception as e:  # noqa: BLE001 - CLI
            print("error:", e)
    try:  # stdin closed (run in the background): keep serving until killed
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass
    svc.stop()


if __name__ == "__main__":
    main()
