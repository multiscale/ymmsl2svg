"""Watch a yMMSL file for changes.

Two mechanisms run side by side and feed the same callback:

* inotify (Linux) on the file's *parent directory*, for near-instant updates.
  Watching the directory rather than the file also catches editors that save by
  writing a temporary file and renaming it over the original (Vim, Emacs,
  VS Code): a watch on the file itself would go stale after the first save.
* polling of the file's ``(mtime, size, inode)`` as a backstop. inotify only
  sees changes made through this machine's kernel, so it misses edits made from
  elsewhere (Windows editors on WSL's ``/mnt/c``, another NFS/GPFS client, ...),
  and it silently drops events when its queue overflows. The backstop polls
  every second, or every 0.3 s where inotify is unavailable or known to miss
  changes.

The callback only fires when the file's signature actually changed, so the two
mechanisms never report the same save twice.
"""

import ctypes
import ctypes.util
import errno
import logging
import os
import re
import select
import struct
import sys
import threading
from collections.abc import Callable
from pathlib import Path

logger = logging.getLogger(__name__)

#: Backstop poll interval while inotify is active on a local filesystem.
BACKSTOP_INTERVAL_SECONDS = 1.0
#: Poll interval when polling is the only reliable mechanism.
POLL_INTERVAL_SECONDS = 0.3
#: Events arriving within this window are coalesced into one change.
DEBOUNCE_SECONDS = 0.1

#: Filesystems where inotify misses changes made from other machines, so the
#: backstop polls at the fast interval. FUSE types are matched by prefix.
REMOTE_FILESYSTEMS = {
    "9p",  # WSL2 /mnt/c (drvfs)
    "drvfs",
    "nfs",
    "nfs4",
    "cifs",
    "smb3",
    "smbfs",
    "gpfs",
    "lustre",
    "ceph",
    "afs",
    "vboxsf",
}

# inotify constants, see inotify(7)
_IN_CLOSE_WRITE = 0x00000008
_IN_MOVED_FROM = 0x00000040
_IN_MOVED_TO = 0x00000080
_IN_CREATE = 0x00000100
_IN_DELETE = 0x00000200
_IN_DELETE_SELF = 0x00000400
_IN_MOVE_SELF = 0x00000800
_IN_UNMOUNT = 0x00002000
_IN_Q_OVERFLOW = 0x00004000
_IN_IGNORED = 0x00008000
_WATCH_MASK = (
    _IN_CLOSE_WRITE
    | _IN_MOVED_FROM
    | _IN_MOVED_TO
    | _IN_CREATE
    | _IN_DELETE
    | _IN_DELETE_SELF
    | _IN_MOVE_SELF
)
#: The watch on the directory is gone: inotify can no longer report anything.
_WATCH_LOST = _IN_DELETE_SELF | _IN_MOVE_SELF | _IN_UNMOUNT | _IN_IGNORED
_EVENT_HEADER = struct.Struct("iIII")  # wd, mask, cookie, len

Signature = tuple[int, int, int] | None


def file_signature(path: Path) -> Signature:
    """(mtime, size, inode) of the file, or None if it doesn't exist."""
    try:
        stat = path.stat()
    except OSError:
        return None
    return (stat.st_mtime_ns, stat.st_size, stat.st_ino)


def _unescape_mount_path(field: str) -> str:
    r"""Decode the octal escapes (``\040`` for a space, ...) in /proc/mounts."""
    return re.sub(r"\\([0-7]{3})", lambda m: chr(int(m.group(1), 8)), field)


def filesystem_type(path: Path, mounts: Path = Path("/proc/mounts")) -> str | None:
    """Type of the filesystem holding ``path`` (e.g. ``ext4``, ``9p``).

    Returns None when it can't be determined (no /proc/mounts outside Linux).
    """
    try:
        lines = mounts.read_text().splitlines()
    except OSError:
        return None
    path = path.resolve()
    best: tuple[int, str] | None = None
    for line in lines:
        fields = line.split()
        if len(fields) < 3:
            continue
        mount_point = Path(_unescape_mount_path(fields[1]))
        if path == mount_point or mount_point in path.parents:
            depth = len(mount_point.parts)
            if best is None or depth >= best[0]:  # later mounts shadow earlier
                best = (depth, fields[2])
    return best[1] if best else None


def _is_remote(fs_type: str | None) -> bool:
    return fs_type is not None and (
        fs_type in REMOTE_FILESYSTEMS or fs_type.startswith("fuse")
    )


class _Inotify:
    """Minimal ctypes binding to Linux inotify, watching one directory."""

    def __init__(self, directory: Path) -> None:
        libc_name = ctypes.util.find_library("c") or "libc.so.6"
        libc = ctypes.CDLL(libc_name, use_errno=True)
        self.fd = libc.inotify_init1(os.O_NONBLOCK | os.O_CLOEXEC)
        if self.fd < 0:
            err = ctypes.get_errno()
            raise OSError(err, f"inotify_init1: {os.strerror(err)}")
        wd = libc.inotify_add_watch(self.fd, os.fsencode(directory), _WATCH_MASK)
        if wd < 0:
            err = ctypes.get_errno()
            os.close(self.fd)
            hint = " (raise fs.inotify.max_user_watches)" if err == errno.ENOSPC else ""
            raise OSError(err, f"inotify_add_watch: {os.strerror(err)}{hint}")

    def read(self, timeout: float) -> list[tuple[int, str]] | None:
        """(mask, name) of pending events, or None if none arrived in time."""
        readable, _, _ = select.select([self.fd], [], [], timeout)
        if not readable:
            return None
        try:
            data = os.read(self.fd, 64 * 1024)
        except BlockingIOError:
            return []
        events = []
        offset = 0
        while offset + _EVENT_HEADER.size <= len(data):
            _, mask, _, length = _EVENT_HEADER.unpack_from(data, offset)
            offset += _EVENT_HEADER.size
            name = data[offset : offset + length].rstrip(b"\0")
            offset += length
            events.append((mask, os.fsdecode(name)))
        return events

    def close(self) -> None:
        os.close(self.fd)


class FileWatcher:
    """Calls ``on_change`` whenever the watched file changes (or disappears).

    Runs inotify (where available) and a polling backstop side by side, see the
    module docstring. ``method`` describes what is in use, for logging.
    """

    def __init__(
        self,
        path: Path,
        on_change: Callable[[], None],
        poll: bool = False,
        mounts: Path = Path("/proc/mounts"),
        backstop_interval: float = BACKSTOP_INTERVAL_SECONDS,
    ) -> None:
        """Create the watcher; call :meth:`start` to begin watching.

        Args:
            path: The file to watch.
            on_change: Called from a watcher thread after each change.
            poll: Only poll, don't use inotify.
            mounts: Mount table used to detect remote filesystems.
            backstop_interval: Poll interval while inotify is active on a
                local filesystem.
        """
        self.path = path.absolute()
        self.on_change = on_change
        self.method = ""
        self._poll_only = poll
        self._mounts = mounts
        self._backstop_interval = backstop_interval
        self._interval = POLL_INTERVAL_SECONDS
        self._signature: Signature = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._inotify: _Inotify | None = None
        self._threads: list[threading.Thread] = []

    def start(self) -> None:
        """Start watching. The file's current state counts as unchanged."""
        self._signature = file_signature(self.path)
        reason = self._start_inotify()
        fs_type = filesystem_type(self.path.parent, self._mounts)
        if self._inotify is None:
            self.method = f"polling every {POLL_INTERVAL_SECONDS:g} s ({reason})"
        elif _is_remote(fs_type):
            # inotify still reports edits made on this machine instantly; the
            # fast backstop catches edits made from elsewhere.
            self.method = (
                f"inotify + polling every {POLL_INTERVAL_SECONDS:g} s "
                f"({fs_type} filesystem: changes made from other machines are "
                "only seen by polling)"
            )
        else:
            self._interval = self._backstop_interval
            self.method = f"inotify + polling backstop every {self._interval:g} s"
        logger.info("Watching %s: %s", self.path, self.method)

        self._threads.append(
            threading.Thread(target=self._poll_loop, name="ymmsl-poll", daemon=True)
        )
        if self._inotify is not None:
            self._threads.append(
                threading.Thread(
                    target=self._inotify_loop, name="ymmsl-inotify", daemon=True
                )
            )
        for thread in self._threads:
            thread.start()

    def stop(self) -> None:
        """Stop watching and wait for the watcher threads to finish."""
        self._stop.set()
        for thread in self._threads:
            thread.join()
        if self._inotify is not None:
            self._inotify.close()
            self._inotify = None

    def _start_inotify(self) -> str:
        """Try to set up inotify; return the reason when that's not possible."""
        if self._poll_only:
            return "--poll"
        if not sys.platform.startswith("linux"):
            return f"inotify is not available on {sys.platform}"
        try:
            self._inotify = _Inotify(self.path.parent)
        except OSError as exc:
            logger.warning("Falling back to polling: %s", exc)
            return f"inotify failed: {exc.strerror}"
        return ""

    def _check(self) -> None:
        """Fire ``on_change`` if the file's signature changed since last time."""
        with self._lock:
            signature = file_signature(self.path)
            if signature == self._signature:
                return
            self._signature = signature
        try:
            self.on_change()
        except Exception:
            logger.exception("Error in file change callback")

    def _poll_loop(self) -> None:
        while not self._stop.wait(self._interval):
            self._check()

    def _inotify_loop(self) -> None:
        assert self._inotify is not None
        pending = False
        while not self._stop.is_set():
            # Short timeout while a change is pending, to debounce bursts of
            # events from one save; otherwise wake up regularly to see _stop.
            events = self._inotify.read(DEBOUNCE_SECONDS if pending else 0.5)
            if events is None:
                if pending:
                    pending = False
                    self._check()
                continue
            for mask, name in events:
                if mask & _IN_Q_OVERFLOW or name == self.path.name:
                    pending = True
                if mask & _WATCH_LOST and not name:
                    logger.warning(
                        "Lost inotify watch on %s, continuing with polling only",
                        self.path.parent,
                    )
                    self._interval = POLL_INTERVAL_SECONDS
                    self.method = (
                        f"polling every {POLL_INTERVAL_SECONDS:g} s "
                        "(inotify watch lost)"
                    )
                    self._check()
                    return
