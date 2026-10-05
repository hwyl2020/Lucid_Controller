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
.venv\Scripts\python -m app.main [--config config.json] [--log-level DEBUG] [--simulators N] [--no-arena]   # run the app
.venv\Scripts\python -m scripts.arena_hardware_check [--frames 100] [--serial S]   # real-camera check (CLI, no UI)
.venv\Scripts\python -m pytest                                               # all tests
.venv\Scripts\python -m pytest tests/test_configuration.py::test_name        # single test
```

- `config.json` (gitignored) is optional. It is merged over `DEFAULT_CONFIG` in `app/services/configuration.py`, so only overrides need to be written.
- Logs go to `logs/lucid_camera_studio.log`, a rotating file.
- pytest runs from the repo root, with `pythonpath = .` set in `pyproject.toml`.
- **Startup:** the app discovers Arena cameras first. If none are found and `--simulators` is not given, it adds 4 simulators. Discovery takes about 1 s at startup.
- **Tests without hardware:**
  - `tests/fake_arena.py` is an in-memory fake of the `arena_api` surface that `ArenaCamera` uses.
  - `tests/test_arena_sdk_buffers.py` exercises the **real** installed SDK through `BufferFactory.create`, with no camera needed. It is skipped if the SDK is absent.

## Architecture

### Strict layering (the core rule)

```
Dear PyGui UI → Application Services → CameraManager → CameraDevice (interface) → ArenaCamera → Arena SDK
```

- UI modules (`app/ui/`) must **never** import `arena_api` or touch Arena objects, buffers or nodes.
- All Arena-specific code stays in `app/cameras/arena_camera.py`, `camera_discovery.py` and `arena_sdk.py`. `arena_sdk.load()` is the only place `arena_api` is imported, and the import is deferred because importing `arena_api.system` opens the SDK.
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

### Arena SDK facts (verified against arena_api 2.7.1 source)

- **Timeouts:** `device.get_buffer(timeout=...)` takes an **int in ms** and raises the builtin `TimeoutError`. Other SDK failures raise plain `Exception`s whose message contains the ArenaC error name (e.g. `ACCESS_DENIED -1005`). `ArenaCamera._translate` maps these to `CameraError` subclasses.
- **Device list:** the order of `system.device_infos` is not stable, so match devices by MAC. `create_device(info)` returns a list. Every device must be destroyed with `system.destroy_device`.
- **Reading pixels:**
  - Never use `buffer.data`: it builds a Python list.
  - Copy from `buffer.pdata` (`POINTER(c_uint8)`) with `np.ctypeslib.as_array`, honour `padding_x`, and requeue in `finally`.
- **Bayer stays raw** (BayerXX8 as uint8, BayerXX10/12/16 as uint16) in `Frame`:
  - On a TRI122S-C (12 MP), SDK `BufferFactory.convert` to RGB8 took about 70 ms per frame plus an 18 ms copy, which starved the stream (3 FPS, dropped frames). A raw copy takes about 4 ms.
  - Display uses `processing.bayer_preview`, a 2×2 binning with the phase from the PFNC name. It was checked against the SDK's demosaic on real frames (channel correlation ≥ 0.999).
- **Other formats** (packed, BGR, YUV) still go through `BufferFactory.convert`, which is slow at high resolution.
- **Stream settings:** `StreamBufferHandlingMode=OldestFirst`, so drops show up as frame-id gaps (`ArenaCamera.missed_frames`) instead of being replaced silently.
- **Setter prerequisites:** `ExposureAuto` and `GainAuto` must be set to `Off`, and `AcquisitionFrameRateEnable` to `True`, before writing the corresponding values.
- **Disconnects:** a pulled cable surfaces as get_buffer timeouts. `get_frame` checks `is_connected()` on each timeout and raises `CameraDisconnectedError`.

### Hardware notes (this dev PC)

- **Camera:** TRI122S-C, S/N 263401242, 4024×3036, BayerRG8 by default, max about 9.1 FPS at 109.5 ms exposure.
- **Network:** on `Ethernet` (Realtek PCIe GbE, 172.16.1.52/24). Jumbo frames are 9014; Receive Buffers are 512, and LUCID recommends the adapter maximum.
- **Bandwidth:** 12 MP × 9.1 FPS is about 880 Mbit/s, close to the 1 GbE limit, so host CPU stalls show up as missed frames.
- **Mitigations in code:** the UI loop is capped at 60 FPS (`main.UI_MAX_FPS`), and there are 20 stream buffers (`DEFAULT_NUM_BUFFERS`).
- **Results:** CLI check with no UI: 150 frames, 0 missed. Full UI: about 176 frames per 20 s, 0–1 missed.
- **Open issue:** Dear PyGui's `render_dearpygui_frame()` occasionally blocks for about 1 s. The camera thread is unaffected because the GIL is released.

### Acquisition pipeline

```
Camera → AcquisitionWorker (thread per camera) → Frame → ┬→ display queue → GUI (render loop polls, uploads texture)
                                                       └→ recording queue → Recorder (own thread)
```

- Never call blocking acquisition (e.g. a buffer wait) from the Dear PyGui render loop.
- **Settings changes** go through `app/services/camera_control_service.py` (`CameraControlService`):
  - `snapshot()` reads values and capabilities. It touches the device, so call it on selection, after a change, or every 2 s, never every frame.
  - The setters clamp and snap values with `NumericRange.clamp` / `RoiLimits.clamp`, and return the value actually applied.
  - Pixel-format and ROI changes stop the stream, apply, then restart it, so the UI doesn't need to know about the acquiring lock.
- The UI uses only `CameraManager` (and the control service for settings):
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
- **Bayer:** `processing.py` has no display conversion for Bayer. `ArenaCamera` already delivers RGB8 via the SDK, and doing it in OpenCV risks a wrong Bayer-phase mapping.
- **Fonts:** Dear PyGui's default font has no `●` glyph, so `theme.load_font()` loads Segoe UI. Glyph ranges are automatic in DPG 2.x; `add_font_range*` is a deprecated no-op.
- **GIL contention:** with 4 simulators and a fast UI loop, the simulators drop to about 24 FPS because they share the GIL with the render loop (they spend CPU drawing test patterns). Measure real-camera throughput before optimising; if needed, move display conversion off the UI thread or into worker processes.
- **Headless screenshots:** `dpg.output_frame_buffer()` can occasionally capture an all-black frame if the window is occluded. Re-run before concluding that rendering broke.

### Recording and snapshots

- **`RecordingService`** (`app/services/recording_service.py`) is the UI's entry point. It creates `recordings/YYYY-MM-DD/Session_YYYYMMDD_HHMMSS/Camera_NN/` plus `session.json` (app version, mode, start/stop times, per-camera metadata and frame/drop counts).
- **Disk space:** `status()` is polled every UI frame. It checks free space at most once a second and auto-stops below `recording.min_free_gb`.
- **Per-camera pipeline:** `AcquisitionWorker` puts frames on a bounded `RecordingQueue`, and a `CameraRecorder` thread drains it into a writer.
  - Queues are registered on `CameraManager` (`set_recording_queue`), not just on the worker, so recording survives the stream restarts done by pixel-format/ROI changes.
  - `stop()` detaches the queues first, then drains and flushes everything already queued.
  - Drops are counted, never silent: `queue_overflows` (app couldn't keep up) and `frame_gaps` (camera/transport drops, from frame-id jumps).
- **Choosing a mode:** measured on this PC, disk writes reach about 1 GB/s against about 110 MB/s needed. mp4v encoding takes 92 ms per 12 MP frame (too slow for 9 FPS) or 22 ms at half resolution. Hence:
  - **Raw** (default): lossless, native format (raw Bayer). `frames.raw` holds the data and `frames.csv` the index (offset, size, dtype, shape, id, timestamp). Read it back with `recorder.read_raw_sequence()`.
  - **Video:** half-resolution MP4 (mp4v) plus `frames.csv`. It fails with a clear error if the frame size changes mid-recording.
- **Snapshots** (`snapshot.py`) save a lossless raw PNG (16-bit for >8-bit formats), a full-resolution demosaiced RGB PNG, and a JSON sidecar. The source is `CameraManager.snapshot_frame()` (the worker's `last_frame`, which doesn't consume the display queue).
- **Bayer naming:** OpenCV demosaic codes are swapped relative to GenICam: GenICam RG = OpenCV BG, and GR = GB. `processing._CV_DEMOSAIC` holds the mapping, verified against the SDK in `test_arena_sdk_buffers.py`.
- **Verified on the TRI122S-C:** a 6 s raw recording gave 58 × 12 MP BayerRG8 frames, 0 gaps and 0 overflows. Snapshot colours were correct.

### Application services (Milestone 7)

- **`AppServices`** (`app/services/app_services.py`) builds and owns every service. `MainWindow(services)` receives this one object.
  - Shutdown order is reconnect → recording → cameras.
  - New services go here, not into ad-hoc constructor arguments.
- **`ReconnectService`:**
  - A background thread retries `CameraManager.reconnect()` with exponential backoff (2 s → 30 s).
  - It only acts on cameras in ERROR because of a `CameraDisconnectedError` that the user still wants streaming (`CameraManager.wants_streaming`; `stop_streaming` clears it).
  - Recording resumes automatically because queues persist on the manager.
  - It can be toggled from Cameras ▸ Auto-reconnect or in Settings.
- **`PerformanceMonitor`:**
  - Per-camera FPS, MB/s, frames missed (frame-id gaps, now counted generically in `AcquisitionWorker`), timeouts, the camera's NIC (matched by subnet) and recording queue/drops.
  - Host CPU, RAM, per-NIC receive rate and disk write via `psutil` deltas, sampled at most once a second.
  - The Performance window (View menu) costs about 1 ms per frame.
- **Profiles** (`profiles/*.json`) and **sessions** (`sessions/*.json`, gitignored):
  - `SettingsApplier` captures and applies settings in the order format → ROI → exposure → gain → frame rate, through `CameraControlService`, so values are clamped to the target camera.
  - Failures become warnings rather than exceptions.
- **Settings window** (File ▸ Settings): edits and saves `config.json`, and applies the theme, log level, auto-reconnect and recording settings live. Recording settings take effect on the next recording.
- **Diagnostics** (File ▸ Export diagnostics): writes `diagnostics/diagnostics_*.zip` with system, network, camera and config info plus the logs. It never opens the SDK itself.
- **Theming:** light/dark palettes in `theme.py` must set the bar, table and popup colours too, otherwise Dear PyGui keeps its dark defaults. Use `(-255, 0, 0, 255)` as a text colour to mean "theme default".
- **Verified on the TRI122S-C:** the performance monitor showed 9.1 FPS, 111 MB/s, 0 missed frames, NIC `Ethernet`. Profile save/apply worked with no warnings.

### UI layout and conventions (UI polish pass)

- **Tiles:** the image is fitted to the whole tile (aspect kept, centred; no crop or stretch). Name/state and FPS sit on translucent overlay bars, which are child windows because drawlists ignore `pos` in Dear PyGui.
- **Camera Status:** one line per camera (fixed columns plus a stretch filler); it grows up to 6 rows, then scrolls.
- **Layout, top to bottom:**
  - toolbar;
  - sidebar + multiview;
  - collapsible **Camera Status** section (`status_panel.py`);
  - collapsible **Logs** section (`log_panel.py`);
  - status bar.
- **Fitting the stream area:** `MainWindow._fit_main_area()` measures the two sections each frame and sets the sidebar/multiview height to `-(sections + status bar)`. Collapsing a section gives the stream its space immediately.
- **Camera names:** shown everywhere as **`Model (Serial)`** via `camera_display_name()` / `CameraStatus.display_name`. The sidebar rows expand to show the IP address and state from `CameraStatusService`, never hard-coded.
- **Per-camera status:** `CameraStatusService` builds `CameraStatus` (identity, IP, state, `bandwidth_mbps`, fps, `frame_count`, missed, timeouts). It is the single source for the sidebar, the status cards and the Performance window. Don't recompute these figures elsewhere.
- **Units:** data rates are **Mb/s (megabits) everywhere**, via `models/units.py` (`bytes/s × 8 / 1e6`). Never display `MB/s`.
- **Logs:**
  - **Per-camera tagging** (`app/camera_log.py`): records about a camera carry a `camera_id` attribute.
    - Per-camera objects log through `self._log = camera_logger(logger, camera_id)`: ArenaCamera, SimulatorCamera, AcquisitionWorker, CameraRecorder, CameraRow, PropertyGridWindow.
    - Functions that receive a camera id pass `extra=for_camera(camera_id)`.
    - Untagged records are application-wide ("System"). **New camera-related log calls must be tagged.**
    - The log file format includes `[%(camera_id)s]` (`-` when untagged), via `CameraFieldFilter` on the handlers.
  - The Logs panel shows a Camera column (`Model (Serial)`, or "System") and a camera filter (All cameras / System / one camera).
  - `services/log_buffer.LogBuffer` is a thread-safe bounded `logging.Handler`, installed in `main.py` right after `setup_logging`.
  - The panel pulls entries incrementally (`since(seq)`) at `ui.stats_refresh_hz`, keeps at most 1000 rows, and uses a table clipper.
  - Filter segments: All, Debug, Info, Warning, Error. Each shows that exact level; Error also includes Critical.
  - Auto-scroll pins to the bottom every frame while it is enabled.
- **Refresh rates:** the panels refresh at `ui.stats_refresh_hz` (default 5) and do no work while collapsed. Collapsed/expanded state lasts for the session; the defaults come from `config.ui.*_panel_open`.
- **Fonts and themes:**
  - `theme.load_fonts()` must run before widgets are built. It loads Segoe UI (body), Segoe UI Semibold (headings) and Consolas (log lines); `use_font(item, role)` applies them.
  - `compact_table_theme()` is for dense tables, `plain_button_theme()` for disclosure arrows, and `segment_selected_theme()` for the active segment.
- **Texture sizing:** textures are sized from the image as displayed (`camera_view.texture_side_for`), not from the tile's longest side. The old rule converted about 20× more pixels than shown in short tiles. With 4 simulators and both sections open, multiview cost went from 52 ms to 4 ms per frame.

### Per-camera rows and Property Grid

- **Sidebar rows:** `CameraSidebar` holds one `CameraRow` (`app/ui/camera_row.py`) per camera.
  - The header shows disclosure arrow · state dot · `Model (Serial)` · IP · `···` menu.
  - The expandable panel holds that camera's **power toggle (ON/OFF = open/close the camera)** with a separate **► / ■ stream button** in the same row, video recording (format + record), image capture (format + capture), Property Grid button and stats.
  - Power and streaming are deliberately separate. With the camera ON but not streaming, settings the camera locks during acquisition (pixel format, ROI, ...) can be changed in the Property Grid.
  - Every control acts on its own camera only and shows the real state from `CameraManager` every frame. Open/close/start/stop run on a worker thread, so one camera never blocks another.
  - Stopping the stream or turning the camera off finishes that camera's recording first. Record and Capture need a running stream.
  - The `···` menu also has Save settings as profile / Apply profile.
- **Removed on request:** the old "Camera Settings" sidebar panel (exposure/gain/FPS/format/ROI) and Start all / Stop all (buttons and menu items). The Property Grid covers those settings. `CameraControlService` stays, because profiles and sessions use it.
- **Per-camera recording:**
  - `RecordingService` runs several sessions at once. `start(mode, [id])` and `stop([id])` affect only those cameras, and a camera is in at most one active session.
  - `camera_recording(id)` gives the per-row state. `start()` / `stop()` without ids still mean "all streaming cameras not already recording" / "everything" (used by sessions and shutdown).
  - **There are no global record/snapshot controls:** the header toolbar (Record, format, Snapshot) was removed on request, because each camera row has its own. `MainWindow.update()` calls `recording.status()` every frame, which also enforces the low-disk auto-stop. The status bar (`ui/status_bar.py`) shows the recording summary or the last recording error.
  - A session's `session.json` is finalised when its last camera stops.
- **Video formats:**
  - Raw, MP4 (mp4v), AVI (MJPG), MOV (mp4v) and MKV (XVID), each verified to write and read back with the bundled OpenCV/FFmpeg.
  - Each encodes 2012×1518 at 45–64 FPS on the dev PC.
  - Only verified formats are offered (`RecordingMode`, `video_writer.CONTAINERS`).
- **Image formats:**
  - PNG, JPEG, BMP and TIFF apply to the processed image.
  - The raw copy is always lossless: PNG, or TIFF when TIFF is chosen. BMP can't hold 16-bit and JPEG is lossy.
- **Feature model:**
  - `models/features.py` defines `Feature` / `FeatureCategory`.
  - `CameraDevice.feature_tree / write_feature / execute_feature` are optional (the defaults mean unsupported).
  - `ArenaCamera` walks the device node map from the GenICam `Root` category (`NodeCategory.features`), keeping the camera's own hierarchy, kinds, access modes, visibility, ranges and enum entries.
  - Per-node read errors are recorded, never raised. Writes are validated against live min/max/inc/entries, and read-only nodes are refused with a reason.
  - `SimulatorCamera` exposes a small SFNC-named set for UI tests.
- **`FeatureService`:** the only path from the UI to features. `tree()` is slow on real cameras (one read per node), so `PropertyGridWindow` loads it on a background thread.
- **Property Grid window** (`app/ui/property_grid.py`):
  - One floating window per camera, held in `MainWindow._property_grids`; several can be open.
  - Editors by kind: bool → checkbox, enum → combo, float → input_double, integer/string → input_text (integers can exceed 32 bits), command → Execute. Read-only values are plain text.
  - Search and a visibility level (Beginner/Expert/Guru).
  - The whole tree is re-read after every write, keeping expanded categories, because writes change other nodes' values and access.
- **Hardware status:** the real-camera tree walk is not verified yet. The camera came back on 169.254.92.34 (link-local) while the PC is on 172.16.1.52/24, and connecting fails with `INVALID_ADDRESS`, which `_translate` now explains as a subnet mismatch.

### Force IP and stream-locked features

- **Stream-locked features:** on LUCID cameras `TLParamsLocked` is 1 while streaming, so PixelFormat, Width, Height etc. are RO. After the stream stops they are RW again (verified on the TRI122S-C).
  - `CameraManager.stop_streaming` returns only after the worker has fully stopped the camera, and keeps reporting ACQUIRING until then. Before this fix, observers saw CONNECTED while the stream was still locked.
  - `PropertyGridWindow` reloads on every state change of its camera. A change that arrives mid-load queues one more read. A banner shows while streaming.
- **Force IP** (`cameras/network.py` holds the pure rules; the SDK calls live in `camera_discovery.host_interfaces/all_device_infos/force_ip`):
  - `ArenaCamera.network_check()` re-discovers the camera by MAC and compares its subnet with `system.interface_infos`.
  - `force_ip(plan)` calls `system.force_ip({mac, ip, subnetmask, defaultgateway})` and waits until the camera re-announces itself with the new IP. The address is temporary and lasts until the camera reboots; it is refused while the camera is open.
  - `plan_force_ip` keeps the camera's host number when it is free (169.254.92.34 → 172.16.1.34) and never takes the adapter's own, the network, the broadcast or another camera's address. The gateway is 0.0.0.0.
  - `NetworkService` prefers wired adapters (Wi-Fi is detected by adapter name via psutil). If several candidates remain, the row asks the user in a dialog.
  - **ON button:** an unreachable camera gets its IP forced on the first click (the camera stays off, with an amber notice). The next click opens it. A reachable camera opens on the first click.
  - Verified on the TRI122S-C: forced to 169.254.0.41/16, the app chose `Ethernet` over Wi-Fi, moved it back to 172.16.1.41, the second click opened it, and it streamed.

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
