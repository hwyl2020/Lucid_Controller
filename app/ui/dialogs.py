"""Small modal dialogs: text prompt, pick-from-list, message.

Layout: generous padding, prompt text, field, then right-aligned buttons with the default action
(OK) as the accent-filled primary button on the right.
"""

from __future__ import annotations

from collections.abc import Callable

import dearpygui.dearpygui as dpg

from app.ui.theme import bind, secondary_text

WIDTH = 440
BUTTON_WIDTH = 92


def _center(width: int = WIDTH) -> tuple[int, int]:
    return (max(0, dpg.get_viewport_client_width() // 2 - width // 2), max(0, dpg.get_viewport_client_height() // 3))


def _window(title: str, width: int = WIDTH):
    window = dpg.window(label=title, modal=True, width=width, no_resize=True, no_collapse=True, pos=_center(width))
    return window


def _buttons(window, primary: tuple[str, Callable[[], None]] | None, cancel: str | None = "Cancel") -> None:
    """Right-aligned button row: [Cancel] [Primary]."""
    with dpg.table(header_row=False, policy=dpg.mvTable_SizingFixedFit, borders_innerH=False, borders_outerH=False,
                   borders_innerV=False, borders_outerV=False):
        dpg.add_table_column(width_stretch=True)
        dpg.add_table_column(width_fixed=True)
        with dpg.table_row():
            dpg.add_spacer(width=1)
            with dpg.group(horizontal=True):
                if cancel:
                    dpg.add_button(label=cancel, width=BUTTON_WIDTH, callback=lambda: dpg.delete_item(window))
                if primary:
                    label, action = primary
                    ok = dpg.add_button(label=label, width=BUTTON_WIDTH, callback=action)
                    bind(ok, "primary")


def prompt_text(title: str, label: str, on_ok: Callable[[str], None], default: str = "") -> None:
    with _window(title) as window:
        dpg.add_text(label)
        field = dpg.add_input_text(default_value=default, width=-1, on_enter=True,
                                   callback=lambda: _finish(window, lambda: on_ok(dpg.get_value(field))))
        dpg.add_spacer(height=2)
        _buttons(window, ("OK", lambda: _finish(window, lambda: on_ok(dpg.get_value(field)))))
    bind(window, "dialog")
    dpg.focus_item(field)


def choose(title: str, label: str, items: list[str], on_ok: Callable[[str], None], empty_text: str) -> None:
    with _window(title) as window:
        if not items:
            secondary_text(empty_text, wrap=WIDTH - 40)
            _buttons(window, ("Close", lambda: dpg.delete_item(window)), cancel=None)
        else:
            dpg.add_text(label, wrap=WIDTH - 40)
            listbox = dpg.add_listbox(items, default_value=items[0], width=-1, num_items=min(8, len(items)))
            dpg.add_spacer(height=2)
            _buttons(window, ("OK", lambda: _finish(window, lambda: on_ok(dpg.get_value(listbox)))))
    bind(window, "dialog")


def message(title: str, text: str) -> None:
    width = WIDTH + 120
    with _window(title, width) as window:
        dpg.add_text(text, wrap=width - 40)
        dpg.add_spacer(height=2)
        _buttons(window, ("OK", lambda: dpg.delete_item(window)), cancel=None)
    bind(window, "dialog")


def _finish(window: int | str, action: Callable[[], None]) -> None:
    dpg.delete_item(window)
    action()
