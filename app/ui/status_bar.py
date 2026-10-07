"""Status bar text helpers, plus the time formats shared by the tiles and camera rows."""

from __future__ import annotations

import time

from app.services.recording_service import RecordingStatus


def format_duration(seconds: float) -> str:
    """Recording time as HH:MM:SS."""
    total = max(0, int(seconds))
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def format_frame_time(timestamp: float) -> str:
    """Wall-clock time a frame was received (local time, milliseconds): 13:45:21.347."""
    millis = int(round((timestamp % 1) * 1000))
    if millis == 1000:  # rounding up to the next second
        timestamp, millis = timestamp + 1, 0
    return f"{time.strftime('%H:%M:%S', time.localtime(timestamp))}.{millis:03d}"


def split_recording_status(status: RecordingStatus,
                           per_camera: list[tuple[str, float]] | None = None) -> tuple[str, str]:
    """(recording part, free-space part) for the status bar, which shows them separately."""
    text = format_recording_status(status, per_camera)
    free = f"Free {status.free_bytes / 1e9:.0f} GB" if status.free_bytes is not None else ""
    if free:
        text = text.replace(f" | {free}", "")
    return text, free


def format_recording_status(status: RecordingStatus, per_camera: list[tuple[str, float]] | None = None) -> str:
    """Recording summary, plus free space and any recording problem.

    ``per_camera``: (short camera name, elapsed seconds) of each recording camera; each camera's own
    recording time is listed (cameras start and stop recording independently)."""
    free = f" | Free {status.free_bytes / 1e9:.0f} GB" if status.free_bytes is not None else ""
    if not status.active:
        problem = f" | {status.error}" if status.error else ""
        return f"Recording OFF{free}{problem}"
    dropped = f" | {status.dropped} dropped" if status.dropped else ""
    if per_camera:
        timers = "  ·  ".join(f"{name} {format_duration(elapsed)}" for name, elapsed in per_camera)
        head = f"● REC  {timers}"
    else:
        cameras = len(status.cameras)
        head = f"● REC {cameras} camera{'s' if cameras != 1 else ''} {format_duration(status.elapsed_s)}"
    return f"{head} | {status.frames_written:,} frames | {status.bytes_written / 1e9:.2f} GB{dropped}{free}"
