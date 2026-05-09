# CLAUDE.md

## Network Access

This repository is hosted on GitHub, which is not accessible in some countries. If there's a network issue, set the HTTP proxy and HTTPS proxy to `localhost:7890`:

```bash
export http_proxy=http://localhost:7890
export https_proxy=http://localhost:7890
```

## Package Manager

We use `uv` to manage this project. Always use `uv` commands for project operations:

- `uv run` to execute scripts and commands
- `uv add` to add dependencies
- `uv sync` to sync the environment

## Building

Build the C extension in-place before running or testing:

```bash
uv run setup.py build_ext --inplace
```

For a clean distribution build, use:

```bash
./build.sh
```

## Testing

Tests are in the `tests/` directory. Some tests (`test_run.py`) require a real Android device connected via ADB and will prompt for input.

Only run the specific test file(s) relevant to your changes, not the full suite. For example:

```bash
uv run pytest tests/test_jpg.py -v -s
```

Only run the full suite (`uv run pytest -v -s`) when explicitly asked or before a commit/push.

## Project Structure

```
native_code/
  macOS/          # macOS C extension source
    include/
      jpg_encoder.h
      vtb_decoder.h
      vtb_helper.h
    src/
      adb_scr_media.c   # Module entry point (PyMethodDef, PyModuleDef)
      jpg_encoder.c      # BGRA8 → JPEG encoding via ImageIO
      vtb_decoder.c      # H.264 decoding via VideoToolbox
      vtb_helper.c       # NALU parsing, NV12 → BGRA8 via Accelerate/vImage
    CMakeLists.txt       # IDE hints only, NOT for building
src/
  adb_scr/
    __init__.py          # Top-level public API (init_lib, list_devices, AndroidDevice)
    consts.py            # Global mutable constants (paths, versions, FPS)
    logger.py            # Logging setup
    exceptions.py        # Exception hierarchy
    adb_cmd/
      base.py            # ADB daemon management, device enumeration
      device_control.py  # File push, app launch/stop, scrcpy server start
    device/
      android_device.py  # AndroidDevice class (connect, screenshot, gestures)
      control_handle.py  # Video/control socket management, H.264 stream processing
      tcp_forward_tunnel.py  # ADB protocol tunneling to device UDS
      bin_utils.py       # Binary encoding helpers, frame header parsing
      types.py           # ConnectionType, GestureAction, GestureActionNode
    media_ext/
      __init__.py        # Platform-dispatched H.264 decoder factory
      _adb_scr_media.pyi # Type stubs for the C extension
      h264/
        decoder_base.py  # Abstract H264DecoderBase
        vtb_decoder.py   # macOS VideoToolbox decoder implementation
```

The `CMakeLists.txt` file is **only** for IDE code navigation and hints (e.g. CLion, VS Code IntelliSense). Do **not** use CMake to build this project. Always build via `setup.py` (which is invoked automatically by `uv` / `pip`).

## Documentation

Write docstrings and type annotations in the `.pyi` stub files, not in C code. Every public function exposed by the C extension must have a corresponding annotated entry in the `.pyi` file so that users and IDEs can understand the API.

## C Code Style

- **No docstrings in C method tables.** Pass `NULL` for the `ml_doc` field in `PyMethodDef`. All documentation belongs in the `.pyi` stub files.
- **Declare variables near first use.** Do not group declarations at the top of a function.
- **Simplify allocation checks.** Functions like `PyList_New`, `Py_BuildValue`, and `PyDict_New` have negligible failure probability on modern machines. Do not write elaborate NULL-check-and-cleanup chains for them. Only check return values from system/OS calls that can realistically fail (e.g. `CMVideoFormatDescriptionCreateFromH264ParameterSets`, `VTDecompressionSessionCreate`).
- **Do not check `PyArg_ParseTuple` return values.** The `.pyi` stub enforces correct types at the Python level, so callers will not pass wrong arguments. Just call it and use the parsed variables directly.
- **Always use braces for control flow.** Every `if`, `else`, `for`, and `while` body must be wrapped in `{}`, even if it is a single statement.
- **Use `(void)self;`** at the top of module-level functions to suppress unused parameter warnings.

## Python Code Style

- All async I/O uses `asyncio`. Blocking C extension calls (decoder create/destroy/get_frame) must be wrapped in `asyncio.to_thread()`.
- Module-level `consts.py` values are mutable globals. Always access them via `consts.XXX`, never `from consts import XXX` (which captures the value at import time).
- Internal modules set `__all__ = []` to prevent unintended re-exports. Public API is explicitly re-exported from `adb_scr/__init__.py`.
