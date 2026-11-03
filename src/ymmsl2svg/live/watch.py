"""Watch a yMMSL file for changes, using watchfiles.

watchfiles uses the operating system's file notifications (inotify on Linux,
FSEvents on macOS, ReadDirectoryChangesW on Windows), and decides by itself
when to poll instead, e.g. on WSL, where notifications miss changes made from
Windows. Polling can be forced with ``WATCHFILES_FORCE_POLLING=1``.

The file's parent directory is watched rather than the file itself, so saves
that replace the file (writing a temporary file and renaming it over the
original, as Vim, Emacs and VS Code do) are seen too.
"""

import logging
import threading
from collections.abc import Callable
from pathlib import Path

import watchfiles

logger = logging.getLogger(__name__)


class FileWatcher:
    """Calls ``on_change`` whenever the watched file changes (or disappears)."""

    def __init__(self, path: Path, on_change: Callable[[], None]) -> None:
        """Create the watcher; call :meth:`start` to begin watching.

        Args:
            path: The file to watch.
            on_change: Called from a watcher thread after each change.
        """
        self.path = path.absolute()
        self.on_change = on_change
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._run, name="ymmsl-watch", daemon=True
        )

    def start(self) -> None:
        """Start watching."""
        logger.info("Watching %s", self.path)
        self._thread.start()

    def stop(self) -> None:
        """Stop watching and wait for the watcher thread to finish."""
        self._stop.set()
        self._thread.join()

    def _is_watched_file(self, change: watchfiles.Change, path: str) -> bool:
        return Path(path).name == self.path.name

    def _run(self) -> None:
        # Changes from one save are grouped, so this calls on_change once per save
        for _ in watchfiles.watch(
            self.path.parent,
            watch_filter=self._is_watched_file,
            stop_event=self._stop,
            recursive=False,
        ):
            try:
                self.on_change()
            except Exception:
                logger.exception("Error in file change callback")
