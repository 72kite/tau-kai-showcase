# vision-mcp-server

The vision MCP server for Project Tau — see `project-tau-plan.md`
Section 5.5. Phase 2.5 domain server: camera access, face detection, scene understanding, and
PTZ control. Independently deployable, like every domain server in Tau's architecture — this
package does not depend on `tau-core`.

## Layout

```
src/vision_mcp_server/
  camera_client.py        CameraClient - OpenCV wrapper for camera snapshots
  face_detector.py        FaceDetector - insightface wrapper for face detection/embedding
  scene_describer.py      SceneDescriber - lightweight scene understanding
  ptz_client.py           PTZClient - HTTP API wrapper for PTZ cameras
  server.py               FastMCP tools for all above
tests/
  test_camera_client.py   camera operations (mocked cv2)
  test_server.py          tool functions with monkeypatched clients
  conftest.py
```

## Tools

- `get_snapshot()` — capture current frame from camera, return as base64 JPEG (read-only).
- `describe_scene(snapshot_b64)` — analyze scene: lighting, detail level, dominant colors
  (read-only).
- `detect_faces(snapshot_b64)` — find faces and compute embeddings ready for
  `memory-mcp-server.store_face` (read-only; approval happens in memory-mcp-server).
- `track_object(object_type, snapshot_b64)` — track objects by type; currently returns detected
  faces (read-only, placeholder for real tracking).
- `get_active_touches()` — current touch points from an AirTouch/TUIO surface (read-only).
- `ptz_pan(degrees)` — pan PTZ camera left/right (requires approval).
- `ptz_tilt(degrees)` — tilt PTZ camera up/down (requires approval).
- `ptz_zoom(factor)` — zoom PTZ camera (requires approval).

## Integration with memory-mcp-server

Typical flow:
1. `detect_faces(snapshot)` → returns list of faces with embeddings
2. Tau selects a face and calls `memory-mcp-server.store_face(person_id, embedding)`
3. CDG gates the enrollment (requires approval)
4. Person profile created, face embedding stored

## Setup

```bash
python -m venv .venv
.venv/Scripts/activate        # .venv/bin/activate on Linux/macOS
pip install -e ".[dev]"
cp .env.example .env           # set CAMERA_SOURCE, PTZ_URL if using PTZ
pytest
```

## Camera setup

**USB camera (default)**:
```bash
export CAMERA_SOURCE=0        # /dev/video0 on Linux, 0 on Windows/Mac
export CAMERA_WIDTH=640
export CAMERA_HEIGHT=480
```

**IP/PTZ camera**:
```bash
export PTZ_ENABLED=true
export PTZ_URL=http://192.168.1.100:8000
export FACE_DETECTION_ENABLED=true
export SCENE_DESCRIPTION_ENABLED=true
```

## Wiring into tau-core

Registered in `tau-core/config/servers.yaml` as `vision-mcp-server`, spawned via
`python -m vision_mcp_server.server`. Install with
`pip install -e ../vision-mcp-server` into tau-core's `.venv`.

## CDG Rules

Defined in `tau-core/config/cdg_rules.yaml`:
- `vision-ptz-pan-needs-approval`: `ptz_pan` requires approval
- `vision-ptz-tilt-needs-approval`: `ptz_tilt` requires approval
- `vision-ptz-zoom-needs-approval`: `ptz_zoom` requires approval

Read-only tools (`get_snapshot`, `describe_scene`, `detect_faces`, `track_object`) fall through to
`default_effect: allow`.

## Face detection & embeddings

Uses `insightface` (buffalo_l model) to compute face embeddings (512-dimensional vectors). These
embeddings are designed for face matching — pass them directly to
`memory-mcp-server.store_face(person_id, embedding)` for enrollment.

Model is lazy-loaded on first call (downloads ~150MB on first use).

## Scene description

Currently a stub using basic image analysis (brightness, edge density, color). Future: integrate a
lightweight model like CLIP, timm (image classification), or YOLOv5/v8 (object detection) for
richer descriptions.

## PTZ control

Stubs out HTTP API calls. Real implementation requires a PTZ camera that exposes a REST API
(e.g., Hikvision, Dahua, ONVIF-compatible). Configure `PTZ_URL` and set `PTZ_ENABLED=true`.

## AirTouch / TUIO touch tracking

[AirTouch](https://github.com/jing-interactive/AirTouch) is a native Cinder/OpenCV app that turns
a Kinect V1/V2/Azure, Intel RealSense, or other OpenNI-compatible depth camera into a multitouch
surface. It has no API beyond this: it broadcasts every touch point as a standard **TUIO 1.1**
`/tuio/2Dcur` OSC bundle over UDP (`127.0.0.1:3333` by default — see its own `include/item.def`).

`touch_client.py`'s `TouchClient` is a pure TUIO listener — it runs a background UDP server (no
CPU cost between packets) and keeps the latest cursor state in memory, so `get_active_touches()`
is an instant in-memory read, never a blocking network call. Because it only speaks TUIO 1.1, it
works against any TUIO source, not just AirTouch specifically.

**To activate**: run AirTouch (or another TUIO 1.1 source) pointed at this machine, set
`TOUCH_ENABLED=true`, and set `TUIO_HOST`/`TUIO_PORT` if AirTouch isn't using its own defaults.
`get_active_touches()` raises a clear error if called while disabled, same as the PTZ tools do
when `PTZ_ENABLED` is false.

Each touch point is `{id, x, y, x_velocity, y_velocity, acceleration}`, with `x`/`y` normalized
0..1 over whatever surface AirTouch was calibrated against.

## Immich Integration (Dormant)

[Immich](https://immich.app) is a self-hosted photo management system. The `ImmichClient` class
provides dormant (not yet exposed as MCP tools) integration to:

- **Search personal photos by person** (`search_by_person`) for training/enrollment
- **Fetch photos from library** (`get_photo`) for visual analysis
- **Search for similar faces** (`search_similar_faces`) using embeddings
- **Tag photos with recognized people** (`tag_photo`) to update Immich metadata
- **Manage person identities** (`list_people`, `create_person`)

When enabled, this allows Tau to learn from your personal photo library without uploading photos
externally. Face recognition improves over time as more samples are tagged.

**To activate**: Set `IMMICH_ENABLED=true`, `IMMICH_API_URL`, and `IMMICH_API_KEY` in `.env`. Then
expose additional MCP tools that call `ImmichClient` methods (not yet wired).

**Privacy note**: Immich data stays on your network. Tau never leaves your home network to perform
face recognition — all inference is local (insightface).

## Manual smoke test

Install and run. Connect via `tau_core.mcp_client.MCPClientManager`. Call `get_snapshot()` to
verify camera access works. Call `detect_faces()` to confirm face detection (requires camera +
insightface model loaded).

With Immich enabled: instantiate `ImmichClient`, call `list_people()` to verify API connectivity.
