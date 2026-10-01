"""Small modal dialogs: text prompt, pick-from-list, message."""

from __future__ import annotations

from collections.abc import Callable

import dearpygui.dearpygui as dpg

from app.ui.theme import TEXT_DIM

WIDTH = 420


def _center() -> tuple[int, int]:
    return (max(0, dpg.get_viewport_client_width() // 2 - WIDTH // 2), max(0, dpg.get_viewport_client_height() // 3))


def prompt_text(title: str, label: str, on_ok: Callable[[str], None], default: str = "") -> None:
    with dpg.window(label=title, modal=True, width=WIDTH, no_resize=True, pos=_center()) as window:
        dpg.add_text(label)
        field = dpg.add_input_text(default_value=default, width=-1, on_enter=True,
                                   callback=lambda: _finish(window, lambda: on_ok(dpg.get_value(field))))
        with dpg.group(horizontal=True):
            dpg.add_button(label="OK", width=80, callback=lambda: _finish(window, lambda: on_ok(dpg.get_value(field))))
            dpg.add_button(label="Cancel", width=80, callback=lambda: dpg.delete_item(window))
    dpg.focus_item(field)


def choose(title: str, label: str, items: list[str], on_ok: Callable[[str], None], empty_text: str) -> None:
    with dpg.window(label=title, modal=True, width=WIDTH, no_resize=True, pos=_center()) as window:
        if not items:
            dpg.add_text(empty_text, color=TEXT_DIM, wrap=WIDTH - 20)
            dpg.add_button(label="Close", width=80, callback=lambda: dpg.delete_item(window))
            return
        dpg.add_text(label)
        listbox = dpg.add_listbox(items, default_value=items[0], width=-1, num_items=min(8, len(items)))
        with dpg.group(horizontal=True):
            dpg.add_button(label="OK", width=80, callback=lambda: _finish(window, lambda: on_ok(dpg.get_value(listbox))))
            dpg.add_button(label="Cancel", width=80, callback=lambda: dpg.delete_item(window))


def message(title: str, text: str) -> None:
    with dpg.window(label=title, modal=True, width=WIDTH + 120, no_resize=True, pos=_center()) as window:
        dpg.add_text(text, wrap=WIDTH + 100)
        dpg.add_button(label="OK", width=80, callback=lambda: dpg.delete_item(window))


def _finish(window: int | str, action: Callable[[], None]) -> None:
    dpg.delete_item(window)
    action()
