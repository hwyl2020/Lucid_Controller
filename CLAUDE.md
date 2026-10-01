# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

**LUCID Camera Studio**: a Python desktop app for discovering, controlling, viewing (multiview) and recording multiple LUCID Vision Labs cameras through the Arena SDK. It's for engineering and industrial use, with a polished dark-first "camera workstation" UI.

The full baseline spec (requirements, milestones, two-developer split) is the original project brief. This file keeps the parts needed to work in the code. Major architectural changes must be discussed and documented before implementation.

## Stack and environment

- Python, Dear PyGui (GUI), Arena SDK Python bindings (`arena_api`), NumPy, OpenCV, FFmpeg (recording, where needed), stdlib `logging`, pytest.
- **Do not introduce Qt/PySide/PyQt.** Dear PyGui is the decided GUI toolkit.
- On this machine: Python 3.13 with `arena_api` 2.7.1 installed globally. The Arena SDK (C++ headers, docs, GenICam) is at `C:\Program Files\Lucid Vision Labs\Arena SDK`. The spec originally targeted 3.11/3.12; confirm `arena_api` compatibility before changing the interpreter version.
- **Never assume an Arena SDK API.** Before using any class, method, enum, constant or GenICam node, verify it against the installed `arena_api` package (`site-packages/arena_api`), the SDK `docs` folder, or existing working code. Do not fabricate camera features or node names.

## Commands

The venv is created with `--system-site-packages` so it can see the globally installed `arena_api`, which is not on PyPI and ships as a wheel with the SDK.

```bash
python -m venv --system-site-packages .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m app.main [--config config.json] [--log-level DEBUG] [--simulators N]   # run the app (default: 4 simulators)
.venv\Scripts\python -m pytest                                               # all tests
.venv\Scripts\python -m pytest tests/test_configuration.py::test_name        # single test
```

- `config.json` (gitignored) is optional. It is merged over `DEFAULT_CONFIG` in `app/services/configuration.py`, so only overrides need to be written.
- Logs go to `logs/lucid_camera_studio.log`, a rotating file.
- pytest runs from the repo root, with `pythonpath = .` set in `pyproject.toml`.

## Architecture

### Strict layering (the core rule)

```
Dear PyGui UI → Application Services → CameraManager → CameraDevice (interface) → ArenaCamera → Arena SDK
```

- UI modules (`app/ui/`) must **never** import `arena_api` or touch Arena objects, buffers or nodes.
- All Arena-specific code stays in `app/cameras/arena_camera.py` and the related discovery code.
- `CameraDevice` is the shared interface between the two developers' areas of work (camera engine and UI). Change it only on purpose, and document the change.

### `CameraDevice` conventions (`app/cameras/camera_device.py`)

- **Units:** exposure in µs (matches GenICam `ExposureTime`), gain in dB, frame rate in Hz.
- **Capability queries** (`exposure_range()`, `gain_range()`, `frame_rate_range()`, `pixel_formats()`, `roi_limits()`) return `None` or `[]` when the feature is unsupported. Values are validated with `NumericRange.validate` / `RoiLimits.validate`, including increments.
- **Errors:** all expected failures raise a `CameraError` subclass (`CameraNotConnectedError`, `CameraDisconnectedError`, `FrameTimeoutError`, `InvalidValueError`, `InvalidStateError`, `UnsupportedFeatureError`). `ArenaCamera` must translate SDK exceptions into these.
- **Locked while acquiring:** `set_pixel_format` and `set_roi` raise `InvalidStateError` during acquisition, as real GenICam nodes do.
- **`get_frame(timeout)`** is only called from the acquisition worker thread.

### Camera implementations

- `ArenaCamera` is the production backend. It handles init, discovery, connect/disconnect, stream setup, buffer requeueing, node access and cleanup.
- `SimulatorCamera` is a first-class backend, used for UI development, tests and multiview/recording testing without hardware. It has configurable resolution, FPS, test pattern and camera ID, plus simulated disconnects. The UI must work with it without any simulator-specific code.

### Acquisition pipeline

```
Camera → AcquisitionWorker (thread per camera) → Frame → ┬→ display queue → GUI (render loop polls, uploads texture)
                                                       └→ recording queue → Recorder (own thread)
```

- Never call blocking acquisition (e.g. a buffer wait) from the Dear PyGui render loop.
- The UI uses only `CameraManager`:
  - `latest_frame(id)`, `state(id)`, `stats(id)` and `last_error(id)` are cheap, so they can be polled every render frame.
  - Worker errors never raise into the UI. They show up as `CameraState.ERROR` plus `last_error()`.
- Frames cross layers as the app-level `Frame` model (`camera_id, frame_id, timestamp, width, height, pixel_format, data` as a NumPy array). Never pass Arena buffer objects upward. Copy the data out and requeue the buffer inside the backend.
- Queue policy, bounded in both cases:
  - **Display:** newest frame wins, and old frames may be dropped.
  - **Recording:** never drop silently. Surface drops, overflow, write errors and disk-full conditions.

### UI rendering (`app/ui/`)

- `main.py` runs a manual Dear PyGui loop that calls `MainWindow.update()` and then `dpg.render_dearpygui_frame()` each frame.
- `MultiView` lays out `CameraView` tiles. Each tile polls `latest_frame()`, converts it with `processing.to_display_rgba` into a **reused** float32 buffer, and pushes it to a raw texture.
- Textures are sized to the tile, rounded up to one of the `TEXTURE_SIDES` sizes.
- **Performance:** allocating a new float buffer per frame cost about 18 ms per tile. Reusing the buffer (`out=`) and sizing textures to the tile took four 720p simulators from 13 to 39 render FPS. Keep both.
- **Bayer:** display conversion is intentionally absent until it is verified against a real camera, because GenICam and OpenCV name the Bayer phases differently.
- **Fonts:** Dear PyGui's default font has no `●` glyph, so `theme.load_font()` loads Segoe UI.

### Capability-driven GenICam

Don't hard-code an exhaustive node list. Query what the connected camera supports:
- Pixel formats: build the list from the camera's enum entries.
- Ranges and increments for exposure, gain, FPS and ROI: read them from the camera.
- Trigger features: only where present.

A missing optional node must hide or disable the control, never crash. Expected camera errors (disconnect, timeout, device in use, invalid value) must not terminate the app.

### Target layout

`app/{ui,cameras,acquisition,recording,services,models,resources}`, plus `tests/`, `profiles/`, `docs/`, `scripts/`. Runtime output goes in `recordings/`, `snapshots/` and `logs/`, which should be gitignored. Once a structure exists, follow it rather than the spec, and don't create placeholder files ahead of need.

Recording output layout: `Recordings/YYYY-MM-DD/Session_YYYYMMDD_HHMMSS/Camera_NN/` plus `session.json`. The JSON holds camera model, serial, IP, resolution, pixel format, FPS, exposure, gain, trigger and app version.

## Working rules specific to this project

- **Measure before optimizing:** camera FPS, display FPS, drops, queue depth and disk throughput on the real cameras and PC. Pick a recording format only after measuring actual data rates.
- Build in this order: simulator-backed UI first, then one Arena camera live in a Dear PyGui texture, then 2 cameras, then 4, then 4 plus recording.
- **Git workflow:**
  - Branches are `main`, then `develop`, then `feature/*` branches such as `feature/camera-engine` and `feature/ui`.
  - Two developers may run Claude concurrently, so use separate branches or worktrees and merge through PRs.
  - Never push Claude-generated code directly to `main`.
- **Dependencies:** don't add one without a clear reason. Arena SDK redistribution and licensing must be checked before release.
