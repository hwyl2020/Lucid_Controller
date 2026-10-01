# LUCID Camera Studio — Claude Project Instructions

## 1. Project Purpose

Build a professional desktop application for controlling, monitoring, viewing, and recording multiple LUCID Vision Labs cameras using the LUCID Arena SDK.

The application is intended for engineering/industrial use and should provide a polished, modern camera-workstation experience.

## 2. Technology Stack

- Python 3.11/3.12 initially, subject to Arena SDK compatibility
- Dear PyGui for GUI
- LUCID Vision Labs Arena SDK for camera control/acquisition
- NumPy for image/frame data
- OpenCV for image processing/conversion where required
- FFmpeg and/or an appropriate recording backend where required
- Python `logging` for logging
- pytest for tests
- Git for source control

### GUI decision

Use Dear PyGui. Do not introduce Qt, PySide, or PyQt unless the project architecture is explicitly revisited and approved.

## 3. Product Requirements

The application should eventually support:

- Automatic LUCID camera discovery
- Connect/disconnect
- Multiple simultaneous cameras
- Live multiview
- 1x1, 2x1, 2x2, 3x3, and 4x4 layouts
- Camera selection
- Exposure control
- Gain control
- FPS control where supported
- Pixel-format selection where supported
- ROI configuration
- Trigger configuration where supported
- Software trigger where supported
- Snapshot capture
- Video recording
- Raw-frame recording where practical
- Camera configuration profiles
- Session save/load
- Camera/network information
- Performance monitoring
- Error handling
- Logging
- Automatic reconnect where practical
- Dark and light themes

Do not implement every Arena/GenICam feature in the first version. Build incrementally.

## 4. Critical Architecture Rule

The GUI must NEVER directly access Arena SDK objects.

Required dependency direction:

```text
Dear PyGui UI
      ↓
Application Services
      ↓
Camera Manager
      ↓
CameraDevice abstraction
      ↓
ArenaCamera
      ↓
Arena SDK
      ↓
LUCID Camera
```

Do not put Arena SDK calls directly inside UI modules.

## 5. Camera Abstraction

Create a common camera interface, for example:

```python
class CameraDevice:
    @property
    def camera_id(self) -> str: ...

    @property
    def model(self) -> str: ...

    @property
    def serial_number(self) -> str: ...

    @property
    def ip_address(self) -> str | None: ...

    @property
    def connected(self) -> bool: ...

    def connect(self) -> None: ...
    def disconnect(self) -> None: ...
    def start_acquisition(self) -> None: ...
    def stop_acquisition(self) -> None: ...
    def get_frame(self): ...
    def set_exposure(self, value: float) -> None: ...
    def set_gain(self, value: float) -> None: ...
    def set_pixel_format(self, value: str) -> None: ...
    def set_roi(self, x: int, y: int, width: int, height: int) -> None: ...
```

The interface can evolve, but changes must be deliberate and documented.

## 6. ArenaCamera

`ArenaCamera` is the production implementation.

Responsibilities:

- Arena initialization
- Device discovery
- Device connection/disconnection
- Device information
- Stream initialization
- Frame acquisition
- Buffer handling
- GenICam node access
- Exposure/gain/FPS/pixel format/ROI
- Trigger configuration
- Error handling
- Resource cleanup

Keep Arena-specific objects and implementation details inside the camera/backend layer.

## 7. SimulatorCamera

Implement `SimulatorCamera` early.

Purpose:

- UI development without hardware
- Automated tests
- Multiview testing
- Recording testing
- Development when Arena SDK/cameras are unavailable

Simulator should provide configurable:

- Resolution
- FPS
- Frame number
- Timestamp
- Test pattern
- Camera ID
- Simulated connection/disconnection where useful

The UI must work with SimulatorCamera without special-case UI code.

## 8. Acquisition Architecture

Never perform blocking camera acquisition in the Dear PyGui render/UI loop.

Preferred architecture:

```text
LUCID Camera
     ↓
Acquisition Worker
     ↓
Frame Queue
   /       \
  /         \
Display     Recording
Queue       Queue
  ↓           ↓
GUI        Recorder
```

The GUI must remain responsive while multiple cameras stream or record.

## 9. Frame Model

Use an application-level frame representation. Do not expose Arena SDK buffer objects to the GUI.

Example:

```python
class Frame:
    camera_id: str
    frame_id: int
    timestamp: float
    width: int
    height: int
    pixel_format: str
    data: object
```

Add metadata only when required.

## 10. Queue Policy

Display pipeline:

- Prefer newest frame.
- Old display frames may be dropped when the UI cannot keep up.

Recording pipeline:

- Do not silently drop frames.
- Report dropped frames, queue overflow, write errors, and storage problems.

Use bounded queues to prevent uncontrolled memory growth.

## 11. Camera Controls

Initial common controls:

### Acquisition

- Start
- Stop
- Continuous mode
- Triggered mode where supported

### Image

- Exposure
- Auto exposure where supported
- Gain
- Auto gain where supported
- FPS where supported
- Pixel format

### ROI

- Width
- Height
- Offset X
- Offset Y

Validate all values against the actual camera's capabilities and increment constraints.

## 12. Pixel Format

Do not assume all cameras support the same pixel formats.

Examples include:

- Mono8
- Mono10
- Mono12
- BayerRG8
- BayerRG10
- BayerRG12
- RGB8

Populate available formats dynamically from the camera.

## 13. Trigger

Where supported, expose:

- Trigger mode
- Trigger source
- Trigger activation
- Trigger selector
- Trigger delay
- Line selection
- Software trigger

Only show/enable features actually supported by the connected camera.

## 14. GenICam Strategy

Do not hard-code every possible GenICam node.

Use a capability-driven approach.

If a feature is unsupported:

- Hide it, or
- Disable it, or
- Clearly indicate that it is unsupported.

Never crash because an optional node is missing.

## 15. Multiview UI

Initial layouts:

- 1x1
- 2x1
- 2x2
- 3x3
- 4x4

Each viewport should support, where practical:

- Live image
- Camera name
- Serial number
- FPS
- Frame number
- Connection state
- Acquisition state
- Recording state
- Timestamp
- Optional crosshair
- Optional image statistics

Future features may include custom layouts, drag/drop placement, fullscreen, maximize, detachable views, and picture-in-picture.

## 16. UI Design

The UI should be:

- Professional
- Minimal
- Modern
- Apple/macOS-inspired without copying proprietary UI designs
- Suitable for engineering use
- Image-focused
- Dark/light themed

Default theme should be dark.

Suggested structure:

```text
┌──────────────────────────────────────────────────────────────┐
│ LUCID Camera Studio                     ● Connected      ⚙   │
├──────────────┬───────────────────────────────────────────────┤
│ CAMERAS      │                  MULTIVIEW                    │
│              │                                               │
│ ● Camera 01  │      ┌────────────────┬────────────────┐     │
│ ● Camera 02  │      │    Camera 1    │    Camera 2    │     │
│ ● Camera 03  │      ├────────────────┼────────────────┤     │
│ ● Camera 04  │      │    Camera 3    │    Camera 4    │     │
│              │      └────────────────┴────────────────┘     │
├──────────────┴───────────────────────────────────────────────┤
│ 4 Cameras | 119.8 FPS | Recording OFF | Storage: 1.2 TB     │
└──────────────────────────────────────────────────────────────┘
```

## 17. Recording

Recording must be independent from the GUI.

Initial features:

- Start/stop recording
- Duration
- Frame count
- Recording path
- File naming
- Per-camera recording
- Recording status
- Storage status

Possible modes:

1. Processed video
2. Raw frame sequence
3. Future high-speed/lossless recording

Do not choose a recording architecture based only on assumptions. Measure actual camera data rates and disk performance first.

Recommended directory:

```text
Recordings/
└── YYYY-MM-DD/
    └── Session_YYYYMMDD_HHMMSS/
        ├── Camera_01/
        ├── Camera_02/
        └── session.json
```

Metadata should include camera model, serial number, IP, resolution, pixel format, FPS, exposure, gain, trigger settings, recording format, and application version where available.

## 18. Snapshot

Provide a snapshot function.

Where practical support:

- Current displayed image
- Raw frame
- Processed frame
- Metadata sidecar

## 19. Camera Discovery

Display, where available:

- Model
- Serial number
- Device ID
- MAC
- IP
- Firmware
- Interface
- Connection status

Discovery errors must be handled gracefully.

## 20. Logging

Use Python's standard `logging` module.

Levels:

- DEBUG
- INFO
- WARNING
- ERROR
- CRITICAL

Log important hardware/application events, but do not log secrets or unnecessary sensitive information.

## 21. Error Handling

Handle gracefully:

- Camera disconnect
- Network timeout
- Camera already in use
- Acquisition failure
- Invalid node value
- Unsupported feature
- Invalid ROI
- Recording failure
- Disk full
- Arena initialization failure
- Pixel conversion failure

Expected camera errors must not terminate the entire application.

## 22. Configuration

Use JSON or TOML for application configuration.

Example:

```json
{
  "application": {
    "theme": "dark",
    "default_layout": "2x2"
  },
  "recording": {
    "directory": "Recordings",
    "format": "mp4"
  },
  "cameras": {}
}
```

## 23. Project Structure

Use this as the target structure:

```text
lucid-camera-studio/
│
├── app/
│   ├── main.py
│   ├── ui/
│   │   ├── main_window.py
│   │   ├── camera_view.py
│   │   ├── camera_sidebar.py
│   │   ├── camera_controls.py
│   │   ├── toolbar.py
│   │   ├── status_bar.py
│   │   └── settings_window.py
│   │
│   ├── cameras/
│   │   ├── camera_device.py
│   │   ├── arena_camera.py
│   │   ├── camera_manager.py
│   │   ├── camera_discovery.py
│   │   └── simulator_camera.py
│   │
│   ├── acquisition/
│   │   ├── acquisition_worker.py
│   │   ├── frame.py
│   │   ├── frame_queue.py
│   │   └── processing.py
│   │
│   ├── recording/
│   │   ├── recorder.py
│   │   ├── video_writer.py
│   │   └── snapshot.py
│   │
│   ├── services/
│   │   ├── configuration.py
│   │   ├── logging_service.py
│   │   ├── session_manager.py
│   │   └── performance_monitor.py
│   │
│   ├── models/
│   │   ├── camera_model.py
│   │   ├── camera_state.py
│   │   └── application_state.py
│   │
│   └── resources/
│       ├── icons/
│       ├── themes/
│       └── images/
│
├── tests/
├── profiles/
├── docs/
├── scripts/
├── recordings/
├── snapshots/
├── logs/
├── CLAUDE.md
├── README.md
├── requirements.txt
└── .gitignore
```

Do not create unnecessary files. Follow the existing structure once established.

## 24. Two-Person Development

Use Git as the source of truth.

Recommended branches:

```text
main
  └── develop
       ├── feature/camera-engine
       ├── feature/acquisition
       ├── feature/recording
       └── feature/ui
```

Developer 1 primarily owns:

- Arena SDK integration
- Camera discovery
- Camera abstraction
- Acquisition
- Buffer management
- Camera controls
- Trigger
- Recording backend
- Hardware error handling

Developer 2 primarily owns:

- Dear PyGui
- Main window
- Multiview
- Camera sidebar
- Camera controls UI
- Toolbar
- Settings
- Themes
- Status information

Both developers must respect shared interfaces.

## 25. Claude Code Rules

Before changing code:

1. Read this `CLAUDE.md`.
2. Inspect the existing implementation.
3. Identify the correct architecture layer.
4. Make the smallest reasonable change.
5. Do not rewrite unrelated files.
6. Do not create duplicate functionality.
7. Preserve public interfaces unless the change is intentional.
8. Add or update tests where appropriate.
9. Run relevant tests/checks.
10. Summarize changed files and validation results.

Never assume an Arena SDK API. Check the installed SDK/API documentation or existing working code before using a method, class, enum, node, or constant.

Do not fabricate camera features or GenICam nodes.

## 26. Git Worktrees / Parallel Claude Sessions

Two developers may run Claude Code concurrently.

Use separate Git branches/worktrees rather than editing the same working directory simultaneously.

Example:

```text
Repository
├── Developer 1 worktree → feature/camera-engine
└── Developer 2 worktree → feature/ui
```

Merge through pull requests/code review.

Do not directly push experimental Claude-generated code to `main`.

## 27. Testing

### Unit tests

Hardware-independent tests should cover:

- Camera state
- SimulatorCamera
- Configuration
- Frame queues
- ROI validation
- Recording filename generation
- Session management
- Error handling

### Integration tests

With Arena SDK/physical hardware:

- Discovery
- Connection
- Acquisition
- Camera controls
- Trigger
- Disconnect/reconnect

### Hardware validation

Maintain a documented hardware test checklist.

## 28. Performance Requirements

The application must remain responsive during multi-camera streaming and recording.

Measure actual:

- Camera FPS
- Display FPS
- Recorded FPS
- Frame drops
- Acquisition latency
- Queue depth
- CPU
- RAM
- GPU
- Network throughput
- Disk throughput

Test using the actual target camera models and PC hardware.

## 29. Initial Prototype

Before implementing the full product, create a minimal prototype:

```text
Arena SDK
   ↓
One LUCID camera
   ↓
Acquire frame
   ↓
Convert if required
   ↓
Dear PyGui texture
   ↓
Live display
```

Then validate:

1. One camera at target FPS.
2. Two cameras.
3. Four cameras.
4. Four cameras plus recording.
5. Target resolutions.
6. Target pixel formats.
7. Target PC/network/storage hardware.

Do not optimize based on assumptions. Measure first.

## 30. Development Milestones

### Milestone 1 — Foundation

- Repository
- Structure
- Python environment
- Dear PyGui startup
- Logging
- Configuration
- CLAUDE.md

### Milestone 2 — UI Prototype

- Main window
- Dark theme
- Camera sidebar
- 2x2 multiview
- SimulatorCamera

### Milestone 3 — Arena Integration

- Arena initialization
- Discovery
- Connection
- Disconnect
- Camera information
- Single-camera streaming

### Milestone 4 — Multi-Camera

- Two cameras
- Four cameras
- Multiview
- Independent camera states

### Milestone 5 — Controls

- Exposure
- Gain
- FPS
- Pixel format
- ROI
- Trigger

### Milestone 6 — Recording

- Snapshot
- Video recording
- Metadata
- Recording status
- Error handling

### Milestone 7 — Professionalization

- Settings
- Profiles
- Sessions
- Performance monitoring
- Automatic reconnect
- Diagnostics

### Milestone 8 — Release

- Packaging
- Installer
- Versioning
- Documentation
- Hardware validation
- Release checklist

## 31. Dependency/Licensing Rule

Do not add a dependency without a clear reason.

Before commercial distribution, maintain a dependency/license inventory for all direct and transitive dependencies as appropriate.

Arena SDK/LUCID Vision Labs licensing and redistribution requirements must be checked against the applicable current LUCID documentation/agreement before release.

## 32. Definition of Done

A feature is complete only when:

- It is implemented.
- It follows the architecture.
- Existing functionality remains intact.
- Relevant tests are added/updated.
- Errors are handled.
- Logging is present where appropriate.
- Documentation is updated when necessary.
- Code is reviewed.
- Simulator testing is completed where applicable.
- Hardware validation is completed where applicable.

## 33. First Tasks

### Developer 1

1. Create project structure.
2. Implement `CameraDevice`.
3. Implement `SimulatorCamera`.
4. Implement frame model.
5. Implement frame queue.
6. Add unit tests.
7. Prepare Arena SDK integration layer.

### Developer 2

1. Create Dear PyGui application shell.
2. Create dark theme.
3. Create main window.
4. Create camera sidebar.
5. Create 2x2 multiview.
6. Display SimulatorCamera frames.
7. Create camera control panel.

### Integration

Connect:

```text
SimulatorCamera
      ↓
CameraManager
      ↓
Multiview
```

Then replace the simulator with `ArenaCamera`.

## 34. Final Product Vision

The final application should feel like a professional camera workstation rather than a basic Python GUI.

Target experience:

```text
Professional UI
      +
Reliable LUCID camera control
      +
High-performance multiview
      +
Multi-camera acquisition
      +
Recording
      +
Snapshots
      +
Camera diagnostics
      +
Profiles/configuration
      +
Performance monitoring
      +
Robust error handling
```

This document is the baseline project specification. Any major architectural change should be discussed and documented before implementation.
