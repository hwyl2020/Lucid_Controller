"""Camera feature browsing/editing for the Property Grid (one camera per call, by camera_id).

The UI never touches node maps: it asks this service for an app-level FeatureCategory tree and
writes values by feature name. Reading a whole tree can take a while on real cameras (one network
read per node), so ``tree()`` is meant to be called off the UI thread.
"""

from __future__ import annotations

import logging

from app.cameras.camera_device import CameraNotConnectedError, UnsupportedFeatureError
from app.cameras.camera_manager import CameraManager
from app.models.features import Feature, FeatureCategory, Visibility
from app.camera_log import for_camera

logger = logging.getLogger(__name__)


class FeatureService:
    def __init__(self, manager: CameraManager) -> None:
        self._manager = manager

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


def matches(feature: Feature, query: str, max_visibility: Visibility = Visibility.GURU) -> bool:
    """Search filter for the Property Grid: name/display name contains ``query`` (case-insensitive)."""
    if feature.visibility.value > max_visibility.value:
        return False
    query = query.strip().lower()
    return not query or query in feature.name.lower() or query in feature.display_name.lower()
