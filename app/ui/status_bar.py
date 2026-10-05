"""Status bar text helpers."""

from __future__ import annotations

from app.services.recording_service import RecordingStatus


def format_recording_status(status: RecordingStatus) -> str:
    """Recording summary across all cameras, plus free space and any recording problem."""
    free = f" | Free {status.free_bytes / 1e9:.0f} GB" if status.free_bytes is not None else ""
    if not status.active:
        problem = f" | {status.error}" if status.error else ""
        return f"Recording OFF{free}{problem}"
    minutes, seconds = divmod(int(status.elapsed_s), 60)
    dropped = f" | {status.dropped} dropped" if status.dropped else ""
    cameras = len(status.cameras)
    return (
        f"● REC {cameras} camera{'s' if cameras != 1 else ''} {minutes:02d}:{seconds:02d} | "
        f"{status.frames_written} frames | {status.bytes_written / 1e9:.2f} GB{dropped}{free}"
    )
