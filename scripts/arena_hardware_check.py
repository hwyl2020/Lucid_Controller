"""Hardware check for LUCID cameras: discovery, connect, capabilities, acquisition rate.

Usage (from the repo root, camera connected):
    .venv\\Scripts\\python -m scripts.arena_hardware_check [--frames 100] [--serial SERIAL]
"""

from __future__ import annotations

import argparse
import logging
import sys
import time

from app.cameras.arena_camera import ArenaCamera
from app.cameras.camera_device import CameraError, FrameTimeoutError
from app.cameras.camera_discovery import discover_arena_cameras


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--frames", type=int, default=100)
    parser.add_argument("--serial", help="Camera serial to test (default: first found)")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(name)s: %(message)s")

    infos = discover_arena_cameras()
    print(f"\nDiscovered {len(infos)} camera(s)")
    for info in infos:
        print(f"  {info.model:12s} S/N {info.serial}  IP {info.ip}  MAC {info.mac}  FW {info.firmware}")
    if not infos:
        print("No camera found. Check cable/PoE, NIC IP settings and that ArenaView is closed.")
        return 1
    info = next((i for i in infos if i.serial == args.serial), infos[0]) if args.serial else infos[0]

    camera = ArenaCamera(info)
    try:
        camera.connect()
        print(f"\nConnected to {camera.model} S/N {camera.serial_number}")
        print(f"  pixel format : {camera.pixel_format}   available: {camera.pixel_formats()}")
        print(f"  ROI          : {camera.roi}   limits: {camera.roi_limits()}")
        print(f"  exposure     : {camera.exposure} us   range: {camera.exposure_range()}")
        print(f"  gain         : {camera.gain} dB   range: {camera.gain_range()}")
        print(f"  frame rate   : {camera.frame_rate} Hz   range: {camera.frame_rate_range()}")

        camera.start_acquisition()
        received = timeouts = 0
        first = last = None
        start = time.perf_counter()
        while received < args.frames and time.perf_counter() - start < 30:
            try:
                frame = camera.get_frame(timeout=2.0)
            except FrameTimeoutError:
                timeouts += 1
                continue
            received += 1
            first = first or frame
            last = frame
        elapsed = time.perf_counter() - start
        camera.stop_acquisition()

        print(f"\nAcquired {received} frames in {elapsed:.2f}s = {received / elapsed:.1f} FPS")
        if last is not None:
            print(f"  frame        : {last.width}x{last.height} {last.pixel_format} dtype={last.data.dtype}")
            print(f"  frame ids    : {first.frame_id} .. {last.frame_id}")
        print(f"  missed (id gaps): {camera.missed_frames}   incomplete: {camera.incomplete_frames}   timeouts: {timeouts}")
        return 0 if received == args.frames and camera.missed_frames == 0 else 2
    except CameraError as exc:
        print(f"\nFAILED: {exc}")
        return 1
    finally:
        camera.disconnect()


if __name__ == "__main__":
    sys.exit(main())
