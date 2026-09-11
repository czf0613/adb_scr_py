# Screen Recording Implementation Plan

> **For agentic workers:** Use superpowers:subagent-driven-development. Native implementation is delegated; the controller implements the independent Python integration and performs final integration verification. No commits. Device testing begins only after the user connects a phone and authorizes that phase.

**Goal:** Add asynchronous start/stop MP4 recording with hardware H.264 and passthrough AAC.

**Architecture:** A native recorder subscribes to the existing decoder's NV12 output and retains the latest frame for start/stop boundaries. Python selects the audio source before launching scrcpy, consumes framed AAC, and owns cancellation-safe recording sessions.

**Tech Stack:** asyncio, C/Objective-C, VideoToolbox, AVFoundation, CoreMedia, AudioToolbox, uv, pytest.

**Spec:** `docs/superpowers/specs/2026-09-11-screen-recording-design.md`

## Global Constraints

- Python >= 3.10; default environment ordinary 3.14; independent 3.14t verification without forced GIL settings.
- No Python media codec; no runtime ffmpeg dependency. ffmpeg is used only for synthetic test fixtures and inspecting artifacts.
- No real Android device commands until the user connects a phone for the device phase.
- Preserve BGRA8 and JPEG access, cancellation ownership and bounded stream waits.
- Native public APIs documented and annotated in `.pyi`; C method-table docs remain NULL; braces on all native control flow.
- Work in current clean checkout on `codex/screen-recording`; keep all changes uncommitted for review and device testing.

## Task 1: Native recording engine

**Files:** Create `native_code/macOS/include/recording.h`, `native_code/macOS/src/recording.m`, optionally separate recording bindings; modify `vtb_decoder.c/.h`, `adb_scr_media.c`, `setup.py`, `_adb_scr_media.pyi`; create `tests/test_recording_native.py` and synthetic fixture helpers as needed.

**Interfaces (positional native calls):**

```python
def start_recording(decoder: DecoderHandle, output_file: str,
                    audio_config: bytes | None, source_pts: int,
                    fps: int) -> RecordingHandle: ...
def set_decoder_recording(decoder: DecoderHandle,
                          recording: RecordingHandle | None) -> None: ...
def append_recording_audio(recording: RecordingHandle,
                           packet: bytes, pts: int) -> None: ...
def stop_recording(recording: RecordingHandle, end_pts: int) -> None: ...
```

`start_recording` snapshots the decoder and attaches the recorder, creates the MP4 and encodes/writes the first frame at zero before returning. It raises for no frame, existing path or encoder/writer failure. `set_decoder_recording` attaches/detaches retained references; decoder destruction releases its attachment after callbacks drain. `stop_recording` rejects new submissions, finishes a tail frame and MP4 at relative `end_pts - source_pts`, is idempotent, and reports asynchronous errors. Native object lifetime remains safe while attached to a decoder, even after stop/Capsule release. Audio configuration is scrcpy AAC AudioSpecificConfig, payloads are raw AAC access units. No transcode.

- [x] Write synthetic tests that call the public native API and inspect produced MP4; verify missing APIs fail before implementation.
```python
assert hasattr(media, "start_recording"), "native recording API missing"
# Fixture supplies a decoded 64x64 NV12 frame, audio_config=None.
rec = media.start_recording(decoder, str(output), None, 1_000_000, 30)
media.set_decoder_recording(decoder, None)
media.stop_recording(rec, 2_000_000)
# ffprobe: h264, first packet keyframe, duration approximately 1 second.
```
- [x] Implement retained native recorder, bounded queues, H.264 low-latency session and AVAssetWriter compressed inputs. Keep the first-frame canvas dimensions, real-time encoding and frame reordering disabled. Following device feedback, use system defaults for bitrate, quality, profile and keyframe interval without a speed-over-quality hint. Validate native failures and release resources.
- [x] Implement decoder subscription and Capsule bridge, including cross-thread close and Python thread-state detachment. Ensure lock order never waits back from recorder queue onto decoder queue.
- [x] Add synthetic AAC passthrough, static/dynamic/rotation, no-frame/path failure, repeated start/stop, decoder destruction and concurrent lifecycle tests.
- [x] Build with `uv run --python 3.14 --locked setup.py build_ext --inplace`; run `uv run --python 3.14 --locked pytest tests/test_recording_native.py tests/test_native_lifecycle.py -q`. Report actual output and red/green evidence. Do not commit.

## Task 2: Python audio transport and recording lifecycle

**Files:** Modify `adb_cmd/base.py`, `adb_cmd/device_control.py`, `device/android_device.py`, `device/control_handle.py`; create `device/recording.py` for recording lifecycle if helpful; create `tests/test_audio_stream.py`, `tests/test_recording.py`; adapt existing synthetic session fixtures.

**Consumes:** Task 1 native interfaces above.
**Produces:** `AndroidDevice.start_recording(output_file: str) -> None` and `stop_recording() -> None`; `adb_android_api_level(serial, timeout=...) -> int`; launcher accepts keyword `android_api_level`, control handle accepts keyword `audio_enabled`.

- [x] Test real API-level parsing with mocked external command output and verify launch parameters for 29, 30, 32, 33, 36, invalid and failed queries before implementing.
```python
assert await adb_android_api_level("synthetic") == 33
# Capture process argv: includes audio_codec=aac, audio_source=playback,
# audio_dup=true. Older levels use output or audio=false.
```
- [x] Implement detection inside the connection timeout before server launch; pass one audio-enabled decision to both launcher and socket layer. Query failures propagate through existing connection rollback.
- [x] Test three socket ordering, four-byte AAC codec header, config flag/data packets, disable codes 0/1, EOF, half-packet timeout and idle no-timeout. Implement dedicated audio reader with bounded packet sizes and readiness state. Never capture or play audio on the Mac.
- [x] Maintain a monotonic/device PTS anchor, cache AAC config, route packets to native only while recording. Use a separate recording lock/lifecycle object rather than blocking audio behind expensive screenshot operations.
- [x] Test first-frame preconditions, duplicate start/stop, cancellation during creation/finish, decoder replacement and disconnect. Implement native worker ownership using complete_on_cancel; detach recorder before finishing and before decoder replacement, reattach after replacement.
- [x] Run `uv run --python 3.14 --locked pytest tests/test_audio_stream.py tests/test_recording.py tests/test_device_session.py tests/test_lifecycle.py -q`. Do not run real ADB.

## Task 3: Integration, docs and compatibility

**Files:** `docs/architecture.md`, `docs/control-flow.md`, `docs/api.md`, `docs/python-compatibility.md`, `README.md`; tests as needed for integration failures.

- [x] Run native and Python tests together, inspect generated MP4 streams/packet timestamps, check unchanged screenshot and lifecycle regressions.
- [x] Review cross-layer cancellation, decoder replacement, queue backpressure, output-file ownership and native free-threading. Fix with regression evidence.
- [x] Document public signatures, version policy, muted older devices, AAC packet-boundary precision, no-frame/disabled-audio behavior, static frames and cleanup contracts.
- [x] Copy tracked plus untracked source files to isolated temporary trees; build sdist/wheel with 3.10 and 3.14t, install and run relevant device-free tests, compile `.py`/`.pyi`, collect `test_run.py` only. Verify GIL disabled before imports and after tests on 3.14t; validate archives exclude docs/tests/AGENTS while retaining sources/resources/stubs.
- [x] Final `git diff --check`, targeted tests and status. Stop at device phase and ask user to connect the phone; no commit or push.

## Authorized device phase and default-quality adjustment

- [x] Validate AAC playback duplication on the connected Android 14 phone; user confirms local sound continues.
- [x] Validate playing, short, paused-screen and disconnect-finalization recordings; inspect output codec, source AAC hashes, first keyframe and stop duration.
- [x] Following user feedback, remove bitrate/quality tradeoff overrides and use VideoToolbox defaults; compare the same paused picture before/after.
- [x] Rebuild and rerun the 25 native/integration tests on ordinary 3.14, isolated 3.10 and isolated 3.14t; validate new archives and natural GIL-disabled state.
- [x] Record 20 seconds of resumed playback with default settings, inspect size and representative frames, validate audio/video, and release the test session. Results are in `docs/python-compatibility.md`; changes remain uncommitted.
