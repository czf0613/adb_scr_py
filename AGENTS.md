# AGENTS.md

## Start Here

Read [docs/architecture.md](docs/architecture.md) for the current system design, module responsibilities, connection lifecycle, data flow, and concurrency boundaries before changing the implementation. Update that document when those contracts change.

Read [docs/control-flow.md](docs/control-flow.md) for the reasons behind connection ordering, startup/shutdown waits, VideoToolbox configuration, GCD queue ownership, and the original high-frequency BGRA8/OpenCV use case. Preserve that raw-frame use case when changing the media pipeline; JPEG is an additional output path.

See [docs/python-compatibility.md](docs/python-compatibility.md) for the Python compatibility audit and repeatable verification commands.

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

## Python Versions

- The minimum supported runtime is **Python 3.10**, as declared in `pyproject.toml` and `uv.lock`.
- Local development and default tests use **Python 3.14**. Keep the local `.python-version` at `3.14` (the file is currently gitignored).
- New Python code, tests, and `.pyi` stubs must remain compatible with 3.10. Do not introduce newer-only syntax or standard-library APIs without a compatible fallback.
- Validate compatibility with a real Python 3.10 interpreter in a separate temporary source tree/environment. Do not replace the normal 3.14 `.venv` just to perform this check.
- A successful 3.14 run alone does not establish 3.10 compatibility. Check imports, native extension builds, and relevant device-free tests on both versions.
- The native extension supports free-threaded CPython (verified on 3.14t). Native concurrency changes also require a separate 3.14t build and device-free tests with the GIL confirmed disabled before imports and after tests. Do not force `PYTHON_GIL=0` or `-X gil=0`, which can hide a missing module declaration. Keep the normal `.venv` on 3.14; see `docs/python-compatibility.md` for the separate wheel ABI and commands.

## Building

Build the C extension in-place before running or testing:

```bash
uv run --python 3.14 --locked setup.py build_ext --inplace
```

For a clean distribution build, use:

```bash
./build.sh
```

## Testing

Tests are in the `tests/` directory. Some tests (`test_run.py`) require a real Android device connected via ADB and will prompt for input.

Only run the specific test file(s) relevant to your changes, not the full suite. For example:

```bash
uv run --python 3.14 --locked pytest tests/test_jpg.py -v -s
```

Only run the full suite (`uv run pytest -v -s`) when explicitly asked or before a commit/push.

For compatibility checks, compile or collect `tests/test_run.py` without executing it. Running it starts ADB and can control a connected phone; it needs an explicitly requested device test. `test.sh` runs the full suite, so it is not the default device-free check.

## Project Structure

The detailed source map lives in [docs/architecture.md](docs/architecture.md). Python modules are under `src/adb_scr/`; macOS native code is under `native_code/macOS/`. The distribution is named `adb_scr_py`, while callers import `adb_scr`.

The `CMakeLists.txt` file is **only** for IDE code navigation and hints (e.g. CLion, VS Code IntelliSense). Do **not** use CMake to build this project. Always build via `setup.py` (which is invoked automatically by `uv` / `pip`).

## Documentation

Write docstrings and type annotations in the `.pyi` stub files, not in C code. Every public function exposed by the C extension must have a corresponding annotated entry in the `.pyi` file so that users and IDEs can understand the API.

Architecture and development documents belong in `docs/`. Keep `docs/` and `AGENTS.md` out of both source distributions and wheels. `MANIFEST.in` excludes them; verify the actual archive contents after changing packaging rules. Keep the bundled `scrcpy-server.bin`, native sources/headers in the sdist, and extension/type information in the wheel.

## C Code Style

- **No docstrings in C method tables.** Pass `NULL` for the `ml_doc` field in `PyMethodDef`. All documentation belongs in the `.pyi` stub files.
- **Declare variables near first use.** Do not group declarations at the top of a function.
- **Simplify allocation checks.** Functions like `PyList_New`, `Py_BuildValue`, and `PyDict_New` have negligible failure probability on modern machines. Do not write elaborate NULL-check-and-cleanup chains for them. Only check return values from system/OS calls that can realistically fail (e.g. `CMVideoFormatDescriptionCreateFromH264ParameterSets`, `VTDecompressionSessionCreate`).
- **Preserve the existing `PyArg_ParseTuple` convention in unrelated changes.** Current entry points assume valid arguments and do not check its return value. The `.pyi` stub documents this input contract for static tooling; it does not enforce types at runtime.
- **Always use braces for control flow.** Every `if`, `else`, `for`, and `while` body must be wrapped in `{}`, even if it is a single statement.
- **Use `(void)self;`** at the top of module-level functions to suppress unused parameter warnings.

## Python Code Style

- All async I/O uses `asyncio`. Blocking C extension calls (decoder create/destroy/get_frame) must be wrapped in `asyncio.to_thread()`.
- Module-level `consts.py` values are mutable globals. Always access them via `consts.XXX`, never `from consts import XXX` (which captures the value at import time).
- Internal modules set `__all__ = []` to prevent unintended re-exports. Public API is explicitly re-exported from `adb_scr/__init__.py`.
