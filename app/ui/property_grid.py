"""Floating, camera-specific Property Grid: browse and edit every feature the camera exposes.

One window per camera (several may be open at once). The feature tree is read through
FeatureService on a background thread (a real camera means hundreds of network reads) and re-read
after every change and whenever the camera's state changes (on/off, streaming started/stopped),
because the camera locks some features while streaming; this keeps access modes truthful.

Actions bar: "Apply to all cameras" copies this camera's settings to every other camera that is on,
"Reset to defaults" restores the camera's factory default settings, and Profiles saves/applies
named profiles. Camera work runs on a background thread; its outcome is shown on the UI thread.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable

import dearpygui.dearpygui as dpg

from app.cameras.camera_device import CameraError
from app.models.camera_state import CameraState
from app.models.features import Feature, FeatureCategory, FeatureKind, Visibility
from app.services.feature_service import FeatureService, matches, summarize_copy
from app.services.profile_service import ProfileService
from app.ui import dialogs
from app.ui.theme import COLORS, THEME_TEXT, bind, nudge, secondary_text, use_font
from app.camera_log import camera_logger

logger = logging.getLogger(__name__)

VISIBILITY_LABELS = {"Beginner": Visibility.BEGINNER, "Expert": Visibility.EXPERT, "Guru": Visibility.GURU}
ACCESS_LABELS = {"RW": "Read/Write", "RO": "Read only", "WO": "Write only", "NA": "Not available", "NI": "Not implemented"}


def format_value(feature: Feature) -> str:
    value = feature.value
    if value is None:
        return ""
    if feature.kind is FeatureKind.FLOAT:
        text = f"{value:.6g}"
    elif feature.kind is FeatureKind.BOOLEAN:
        text = "True" if value else "False"
    else:
        text = str(value)
    return f"{text} {feature.unit}".strip() if feature.unit else text


def feature_tooltip(feature: Feature) -> str:
    lines = [f"{feature.display_name}  ({feature.name})", f"Type: {feature.kind.value}   Access: {ACCESS_LABELS.get(feature.access, feature.access)}"]
    if feature.minimum is not None and feature.maximum is not None:
        step = f", step {feature.increment:g}" if feature.increment else ""
        lines.append(f"Range: {feature.minimum:g} – {feature.maximum:g}{step} {feature.unit}".rstrip())
    if feature.description:
        lines.append(feature.description)
    if feature.error:
        lines.append(f"Read error: {feature.error}")
    return "\n".join(lines)


class PropertyGridWindow:
    def __init__(self, camera_id: str, title: str, features: FeatureService, on_close: Callable[[str], None],
                 state_of: Callable[[str], CameraState], pos: tuple[int, int] = (220, 90),
                 profiles: ProfileService | None = None, camera_names: Callable[[], dict[str, str]] = dict,
                 on_settings_changed: Callable[[list[str]], None] | None = None) -> None:
        self.camera_id = camera_id
        self._title = title
        self._profiles = profiles
        self._camera_names = camera_names  # camera_id -> "Model (Serial)"
        self._on_settings_changed = on_settings_changed
        self._job: str | None = None  # label of the running background action
        self._job_done: Callable[[], None] | None = None  # UI-thread follow-up of a finished action
        self._features = features
        self._log = camera_logger(logger, camera_id)
        self._state_of = state_of
        self._last_state = state_of(camera_id)
        self._on_close = on_close
        self._lock = threading.Lock()
        self._pending: tuple[FeatureCategory | None, str | None] | None = None  # (tree, error) from loader
        self._loading = False
        self._reload_again = False
        self._tree: FeatureCategory | None = None
        self._rows: dict[str, tuple[Feature, int | str]] = {}  # name -> (feature, table row)
        self._categories: list[tuple[int | str, list[str], list]] = []  # (tree_node, feature names, child nodes)
        self._open_categories: set[str] = set()
        self._keep_message = False

        with dpg.window(label=f"{title}  ·  Property Grid", width=700, height=680, pos=pos,
                        on_close=self._close, no_collapse=True) as self.window:
            with dpg.group(horizontal=True):
                self._search = dpg.add_input_text(hint="Search properties", width=300,
                                                  callback=lambda: self._apply_filter())
                dpg.add_spacer(width=4)
                with nudge(1):
                    show = secondary_text("Show")
                self._visibility = dpg.add_combo(list(VISIBILITY_LABELS), default_value="Expert", width=110,
                                                 callback=lambda: self._apply_filter())
                dpg.add_button(label="Refresh", width=84, callback=lambda: self.reload())
            with dpg.group(horizontal=True):
                self._apply_all_button = dpg.add_button(label="Apply to all cameras…", callback=self._confirm_apply_all)
                self._reset_button = dpg.add_button(label="Reset to defaults…", callback=self._confirm_reset)
                self._profiles_button = dpg.add_button(label="Profiles…", show=profiles is not None)
            bind(self._apply_all_button, "primary")
            with dpg.popup(self._profiles_button, mousebutton=dpg.mvMouseButton_Left):
                dpg.add_menu_item(label="Save settings as profile…", callback=self._save_profile)
                dpg.add_menu_item(label="Apply profile…", callback=self._apply_profile)
            bind(self._search, "search")
            use_font(show, "small")
            self._message = secondary_text("Loading features…", wrap=650)
            use_font(self._message, "small")
            with dpg.child_window(auto_resize_y=True, show=self._last_state is CameraState.ACQUIRING,
                                  no_scrollbar=True) as self._streaming_hint:
                hint = dpg.add_text(
                    "Streaming — features the camera locks during acquisition (e.g. Pixel Format, Width, "
                    "Height) are read-only. Stop the stream (the camera stays on) to change them.", wrap=620)
            bind(self._streaming_hint, "callout_warning")
            use_font(hint, "small")
            self._body = dpg.add_child_window(border=True)
            bind(self._body, "surface")
        self.reload()

    # --- lifecycle ------------------------------------------------------------
    def focus(self) -> None:
        dpg.show_item(self.window)
        dpg.focus_item(self.window)

    def close(self) -> None:
        if dpg.does_item_exist(self.window):
            dpg.delete_item(self.window)

    def _close(self) -> None:
        self.close()
        self._on_close(self.camera_id)

    # --- loading (background) -------------------------------------------------
    def reload(self) -> None:
        with self._lock:
            if self._loading:
                # A load is already reading the camera; its result may predate this request (e.g. the
                # stream stopped mid-read), so read once more when it finishes.
                self._reload_again = True
                return
            self._loading = True
        threading.Thread(target=self._load, name=f"features-{self.camera_id}", daemon=True).start()

    def _load(self) -> None:
        while True:
            try:
                result = (self._features.tree(self.camera_id), None)
            except CameraError as exc:
                result = (None, str(exc))
            except Exception as exc:  # noqa: BLE001 - shown in the window, never crashes the app
                self._log.exception("Reading features of %s failed", self.camera_id)
                result = (None, f"Reading features failed: {exc}")
            with self._lock:
                self._pending = result
                if not self._reload_again:
                    self._loading = False
                    return
                self._reload_again = False

    def update(self) -> None:
        """Called each UI frame: reloads on camera state changes; applies finished loads."""
        done, self._job_done = self._job_done, None
        if done is not None:
            self._job = None
            done()
        busy = self._job is not None
        for button in (self._apply_all_button, self._reset_button):
            dpg.configure_item(button, enabled=not busy)
        state = self._state_of(self.camera_id)
        if state is not self._last_state:
            self._last_state = state
            dpg.configure_item(self._streaming_hint, show=state is CameraState.ACQUIRING)
            self.reload()  # access modes change when streaming starts/stops or the camera turns off
        with self._lock:
            pending, self._pending = self._pending, None
        if pending is None:
            return
        tree, error = pending
        if error:
            # Never leave stale editors around (e.g. after the camera was turned off).
            self._tree = None
            dpg.delete_item(self._body, children_only=True)
            self._rows, self._categories = {}, []
            self._show(error, error=True)
            return
        self._tree = tree
        self._build()
        if not self._keep_message:  # keep the result of a write/execute visible after its reload
            self._show(f"{sum(1 for _ in tree.walk())} features", error=False, dim=True)
        self._keep_message = False

    # --- building ---------------------------------------------------------------
    def _build(self) -> None:
        self._remember_open()
        dpg.delete_item(self._body, children_only=True)
        self._rows, self._categories = {}, []
        for feature in self._tree.features:  # features directly under Root, if any
            self._categories.append(self._category_node(FeatureCategory("Root", "General", (feature,)), self._body))
        for category in self._tree.subcategories:
            self._categories.append(self._category_node(category, self._body))
        self._apply_filter()

    def _category_node(self, category: FeatureCategory, parent) -> tuple:
        node = dpg.add_tree_node(label=category.display_name, parent=parent,
                                 default_open=category.name in self._open_categories, user_data=category.name)
        use_font(node, "heading")
        names = []
        if category.features:
            with dpg.table(parent=node, header_row=False, row_background=True, borders_innerH=False,
                           borders_outerH=False, borders_innerV=False, borders_outerV=False,
                           policy=dpg.mvTable_SizingFixedFit) as table:
                dpg.add_table_column(width_fixed=True, init_width_or_weight=240)
                dpg.add_table_column(width_stretch=True)
                dpg.add_table_column(width_fixed=True, init_width_or_weight=96)
                for feature in category.features:
                    self._rows[feature.name] = (feature, self._feature_row(table, feature))
                    names.append(feature.name)
            bind(table, "compact_table")
        children = [self._category_node(sub, node) for sub in category.subcategories]
        return node, names, children

    def _feature_row(self, table, feature: Feature):
        with dpg.table_row(parent=table) as row:
            name = dpg.add_text(feature.display_name)
            with dpg.tooltip(name):
                dpg.add_text(feature_tooltip(feature), wrap=420)
            self._editor(feature)
            access = "Error" if feature.error else ACCESS_LABELS.get(feature.access, feature.access)
            item = secondary_text(access)
            use_font(item, "small")
            if feature.error:
                dpg.configure_item(item, color=COLORS["error"])
        return row

    def _editor(self, feature: Feature) -> None:
        kind, name = feature.kind, feature.name
        if feature.error:
            secondary_text("—")
        elif not feature.available:
            secondary_text("Not available")
        elif kind is FeatureKind.COMMAND:
            dpg.add_button(label="Execute", enabled=feature.writable, callback=lambda: self._execute(name))
        elif kind is FeatureKind.REGISTER:
            secondary_text("(register)")
        elif not feature.writable:
            dpg.add_text(format_value(feature))
        elif kind is FeatureKind.BOOLEAN:
            dpg.add_checkbox(default_value=bool(feature.value), callback=lambda _s, v: self._write(name, v))
        elif kind is FeatureKind.ENUMERATION:
            dpg.add_combo(list(feature.entries), default_value=str(feature.value or ""), width=-1,
                          callback=lambda _s, v: self._write(name, v))
        elif kind is FeatureKind.FLOAT:
            dpg.add_input_double(default_value=float(feature.value or 0.0), width=-1, step=0, on_enter=True,
                                 format="%.6g", callback=lambda _s, v: self._write(name, v))
        else:  # INTEGER (may exceed 32 bits, so edited as text) and STRING
            decimal = kind is FeatureKind.INTEGER
            dpg.add_input_text(default_value="" if feature.value is None else str(feature.value), width=-1,
                               on_enter=True, decimal=decimal, callback=lambda _s, v: self._write(name, v))

    # --- filtering ----------------------------------------------------------------
    def _apply_filter(self) -> None:
        if self._tree is None:
            return
        query = dpg.get_value(self._search)
        level = VISIBILITY_LABELS[dpg.get_value(self._visibility)]
        shown = 0
        for name, (feature, row) in self._rows.items():
            visible = matches(feature, query, level)
            dpg.configure_item(row, show=visible)
            shown += visible
        for category in self._categories:
            self._show_category(category, expand=bool(query.strip()))
        if query.strip() and shown == 0:
            self._show(f"No properties match “{query.strip()}”", error=False, dim=True)

    def _show_category(self, category, expand: bool) -> bool:
        node, names, children = category
        any_child = [self._show_category(child, expand) for child in children]
        visible = any(dpg.is_item_shown(self._rows[n][1]) for n in names) or any(any_child)
        dpg.configure_item(node, show=visible)
        if expand and visible:
            dpg.set_value(node, True)
        return visible

    def _remember_open(self) -> None:
        def walk(category):
            node, _, children = category
            if dpg.does_item_exist(node) and dpg.get_value(node):
                self._open_categories.add(dpg.get_item_user_data(node))
            for child in children:
                walk(child)
        for category in self._categories:
            walk(category)

    # --- actions ------------------------------------------------------------------
    def _write(self, name: str, value) -> None:
        try:
            self._features.write(self.camera_id, name, value)
        except CameraError as exc:
            self._show(str(exc), error=True)
        else:
            self._show(f"{name} set to {value}", error=False)
        self.reload()  # re-read: the write may change other values, ranges or access modes

    def _execute(self, name: str) -> None:
        try:
            self._features.execute(self.camera_id, name)
        except CameraError as exc:
            self._show(str(exc), error=True)
        else:
            self._show(f"{name} executed", error=False)
        self.reload()

    # --- apply to all / reset / profiles ---------------------------------------------
    def _confirm_apply_all(self) -> None:
        others = [name for cid, name in self._camera_names().items() if cid != self.camera_id]
        if not others:
            dialogs.message("Apply to all cameras", "There are no other cameras to apply the settings to.")
            return
        dialogs.confirm(
            "Apply to all cameras",
            f"Copy the current settings of {self._title} to {len(others)} other camera(s)?\n\n"
            "Cameras that are off or recording are skipped. A streaming camera pauses briefly while its "
            "settings change. Network settings (IP) and camera names are not copied.",
            "Apply to all", self.apply_to_all)

    def apply_to_all(self) -> None:
        def work():
            try:
                results = self._features.copy_to_all(self.camera_id)
            except CameraError as exc:
                message = str(exc)
                return lambda: (self._show(message, error=True), dialogs.message("Apply to all cameras", message))
            text, failed = summarize_copy(results, self._camera_names())
            changed = [r.camera_id for r in results if r.applied]

            def finish():
                if self._on_settings_changed is not None and changed:
                    self._on_settings_changed(changed)
                self._show(text.splitlines()[0], error=failed)
                dialogs.message("Apply to all cameras", text)
            return finish
        self._start_job("Applying settings to all cameras…", work)

    def _confirm_reset(self) -> None:
        dialogs.confirm(
            "Reset to defaults",
            f"Restore the factory default settings of {self._title}?\n\n"
            "Every change made to this camera's settings is lost. A streaming camera pauses briefly.",
            "Reset", self.reset_to_defaults, danger=True)

    def reset_to_defaults(self) -> None:
        def work():
            try:
                self._features.reset(self.camera_id)
            except CameraError as exc:
                message = str(exc)
                return lambda: self._show(message, error=True)
            return lambda: self._show("Settings reset to the camera's defaults", error=False)
        self._start_job("Resetting to default settings…", work)

    @property
    def busy(self) -> bool:
        return self._job is not None

    def _start_job(self, label: str, work: Callable[[], Callable[[], None]]) -> None:
        if self._job is not None:
            return
        self._job = label
        self._show(label, error=False, dim=True)

        def run():
            try:
                done = work()
            except Exception as exc:  # noqa: BLE001 - shown in the window, never crashes the app
                self._log.exception("%s failed", label)
                message = f"{label.rstrip('…')} failed: {exc}"
                done = (lambda: self._show(message, error=True))

            def finish():
                done()
                self.reload()
            self._job_done = finish
        threading.Thread(target=run, name=f"settings-{self.camera_id}", daemon=True).start()

    def _save_profile(self) -> None:
        def save(name: str) -> None:
            try:
                path = self._profiles.save(self.camera_id, name)
            except (CameraError, OSError, ValueError) as exc:
                dialogs.message("Save profile", str(exc))
                return
            dialogs.message("Save profile", f"Profile “{name.strip()}” saved to {path}")
        dialogs.prompt_text("Save profile", "Profile name", save)

    def _apply_profile(self) -> None:
        def apply(name: str) -> None:
            try:
                warnings = self._profiles.apply(self.camera_id, name)
            except CameraError as exc:
                dialogs.message("Apply profile", str(exc))
                return
            text = f"Profile “{name}” applied."
            if warnings:
                text += "\n\nWarnings:\n" + "\n".join(f"- {w}" for w in warnings)
            dialogs.message("Apply profile", text)
            self.reload()
        dialogs.choose("Apply profile", "Profile", self._profiles.list_profiles(), apply,
                       empty_text="No saved profiles yet. Use “Save settings as profile” first.")

    def _show(self, text: str, error: bool, dim: bool = False) -> None:
        self._keep_message = not dim
        color = COLORS["error"] if error else (THEME_TEXT if dim else COLORS["success"])
        dpg.set_value(self._message, text)
        dpg.configure_item(self._message, color=color)
