"""Camera feature browsing/editing for the Property Grid (one camera per call, by camera_id).

The UI never touches node maps: it asks this service for an app-level FeatureCategory tree and
writes values by feature name. Reading a whole tree can take a while on real cameras (one network
read per node), so ``tree()`` is meant to be called off the UI thread.

It also copies one camera's settings to the other cameras ("Apply to all cameras") and restores a
camera's default settings. Both pause a running stream (the camera locks many settings while
acquiring) and resume it afterwards; cameras that are recording are left alone. They talk to the
cameras over the network, so call them off the UI thread too.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass

from app.cameras.camera_device import CameraError, CameraNotConnectedError, InvalidStateError, UnsupportedFeatureError
from app.cameras.camera_manager import CameraManager
from app.models.camera_state import CameraState
from app.models.features import Feature, FeatureCategory, Visibility
from app.camera_log import for_camera

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CopyResult:
    camera_id: str
    applied: bool
    message: str  # why it was skipped / failed; "" when applied


class FeatureService:
    def __init__(self, manager: CameraManager, is_recording: Callable[[str], bool] = lambda _cid: False) -> None:
        self._manager = manager
        self._is_recording = is_recording

    def tree(self, camera_id: str) -> FeatureCategory:
        camera = self._manager.camera(camera_id)
        if not camera.connected:
            raise CameraNotConnectedError("The camera is off. Turn it on (ON) to read and change its features")
        tree = camera.feature_tree()
        if tree is None:
            raise UnsupportedFeatureError(f"{camera_id}: this camera does not expose a feature tree")
        return tree

    def write(self, camera_id: str, name: str, value: object) -> None:
        self._manager.camera(camera_id).write_feature(name, value)
        logger.info("%s: %s = %r", camera_id, name, value, extra=for_camera(camera_id))

    def execute(self, camera_id: str, name: str) -> None:
        self._manager.camera(camera_id).execute_feature(name)
        logger.info("%s: executed %s", camera_id, name, extra=for_camera(camera_id))


    def copy_to_all(self, source_id: str, target_ids: list[str] | None = None) -> list[CopyResult]:
        """Apply ``source_id``'s settings to every other camera (or ``target_ids``).

        Cameras that are off or recording are skipped; a failure on one camera never stops the
        others. Identity/network settings (IP, user name) are never copied."""
        source = self._manager.camera(source_id)
        if not source.connected:
            raise CameraNotConnectedError("Turn this camera on first: its settings are read from the camera")
        settings = source.export_settings()
        targets = [cid for cid in (target_ids if target_ids is not None else self._manager.camera_ids)
                   if cid != source_id]
        results = []
        for cid in targets:
            camera = self._manager.camera(cid)
            if not camera.connected:
                results.append(CopyResult(cid, False, "off"))
            elif self._is_recording(cid):
                results.append(CopyResult(cid, False, "recording"))
            else:
                try:
                    self._with_stream_paused(cid, lambda c=camera: c.import_settings(settings))
                except CameraError as exc:
                    logger.warning("Copying settings from %s failed: %s", source_id, exc, extra=for_camera(cid))
                    results.append(CopyResult(cid, False, str(exc)))
                    continue
                logger.info("Settings copied from %s", source_id, extra=for_camera(cid))
                results.append(CopyResult(cid, True, ""))
        logger.info("Applied settings to %d of %d other camera(s)", sum(r.applied for r in results), len(results),
                    extra=for_camera(source_id))
        return results

    def reset(self, camera_id: str) -> None:
        """Restore the camera's default (factory) settings."""
        camera = self._manager.camera(camera_id)
        if not camera.connected:
            raise CameraNotConnectedError("The camera is off. Turn it on (ON) to reset its settings")
        if self._is_recording(camera_id):
            raise InvalidStateError("Stop recording before resetting the camera's settings")
        self._with_stream_paused(camera_id, camera.reset_settings)
        logger.info("%s: settings reset to defaults", camera_id, extra=for_camera(camera_id))

    def _with_stream_paused(self, camera_id: str, action: Callable[[], None]) -> None:
        was_streaming = self._manager.state(camera_id) is CameraState.ACQUIRING
        if was_streaming:
            self._manager.stop_streaming(camera_id)
        try:
            action()
        finally:
            if was_streaming:
                self._manager.start_streaming(camera_id)


def summarize_copy(results: list[CopyResult], names: dict[str, str]) -> tuple[str, bool]:
    """One-paragraph outcome of ``copy_to_all`` for the UI; returns (text, any_failure)."""
    if not results:
        return "There are no other cameras to apply the settings to.", False
    applied = [names.get(r.camera_id, r.camera_id) for r in results if r.applied]
    lines = [f"Applied to {len(applied)} of {len(results)} other camera(s)" + (f": {', '.join(applied)}." if applied else ".")]
    failed = False
    for r in results:
        if r.applied:
            continue
        name = names.get(r.camera_id, r.camera_id)
        if r.message == "off":
            lines.append(f"Skipped {name}: camera is off.")
        elif r.message == "recording":
            lines.append(f"Skipped {name}: recording in progress.")
        else:
            failed = True
            lines.append(f"Failed on {name}: {r.message}")
    return "\n".join(lines), failed


def matches(feature: Feature, query: str, max_visibility: Visibility = Visibility.GURU) -> bool:
    """Search filter for the Property Grid: name/display name contains ``query`` (case-insensitive)."""
    if feature.visibility.value > max_visibility.value:
        return False
    query = query.strip().lower()
    return not query or query in feature.name.lower() or query in feature.display_name.lower()
