"""Native "choose a folder" dialog for Dear PyGui (which has no native dialogs).

Uses tkinter's ``askdirectory`` (on Windows the standard Explorer-style folder picker) on its own
thread with its own hidden Tk root, so the UI keeps rendering and the cameras keep streaming while the
dialog is open. The result is handed back on the UI thread through ``poll()``.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from pathlib import Path

logger = logging.getLogger(__name__)


def _ask_directory(title: str, initial: str) -> str:
    import tkinter  # noqa: PLC0415 - only needed when a dialog is opened
    from tkinter import filedialog  # noqa: PLC0415

    root = tkinter.Tk()
    root.withdraw()
    root.attributes("-topmost", True)  # the dialog must not open behind the app window
    try:
        return filedialog.askdirectory(parent=root, title=title, initialdir=initial or None, mustexist=False) or ""
    finally:
        root.destroy()


class FolderPicker:
    def __init__(self, ask: Callable[[str, str], str] = _ask_directory) -> None:
        self._ask = ask
        self._lock = threading.Lock()
        self._result: tuple[Callable[[str], None], str] | None = None
        self._open = False

    @property
    def is_open(self) -> bool:
        return self._open

    def choose(self, title: str, initial: str, on_chosen: Callable[[str], None]) -> None:
        """Open the dialog (one at a time); ``on_chosen(path)`` runs on the UI thread from ``poll()``,
        only if a folder was chosen."""
        if self._open:
            return
        self._open = True
        initial_dir = str(Path(initial).resolve()) if initial else ""

        def run() -> None:
            try:
                path = self._ask(title, initial_dir)
            except Exception:  # noqa: BLE001 - a broken dialog must not take the app down
                logger.exception("Folder dialog failed")
                path = ""
            with self._lock:
                self._result = (on_chosen, path)

        threading.Thread(target=run, name="folder-dialog", daemon=True).start()

    def poll(self) -> None:
        """Call every UI frame."""
        with self._lock:
            result, self._result = self._result, None
        if result is None:
            return
        self._open = False
        on_chosen, path = result
        if path:
            on_chosen(str(Path(path)))
