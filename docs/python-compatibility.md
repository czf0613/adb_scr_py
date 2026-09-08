# Python 版本与兼容性验证

## 版本约定

运行时最低支持 **Python 3.10**；本地开发和默认测试继续使用 **Python 3.14**。`pyproject.toml` 和 `uv.lock` 的 `requires-python` 均为 `>=3.10`。本地 `.python-version` 保持 `3.14`，目前该文件被 `.gitignore` 排除。

Python 源码、测试、构建脚本和 `.pyi` 均须兼容 3.10。新增依赖或使用较新语法/标准库 API 时，要在真实 3.10 解释器下验证；仅在 3.14 上通过测试不能证明最低版本可用。

## 2026-09-08 JPEG 直出验证

Python 3.14.6 在正常工作区构建 C/Objective-C 扩展；Python 3.10.20 在独立
临时源树构建 sdist，再从 sdist 构建 wheel 并安装。正常 `.venv` 和
`.python-version` 继续使用 3.14。

两个版本均通过 60 项相关无设备测试（下列五个文件），编译 24 个 Python/
存根文件并导入 16 个模块。`test_run.py` 均只收集 1 项，未执行。合成 H.264
显式声明 BT.709；测试覆盖默认质量、缩小/放大、奇数 ROI、四角颜色、先裁剪
后缩放、取整、非法参数、无帧/关闭、会话复用及质量切换、取消和并发销毁。
公开入口统一为 `get_screenshot_jpg(quality=75, scale=1.0, roi=None)`，覆盖
无参数调用及原有质量位置参数/关键字参数调用的兼容性。
本机 M5 Max/macOS 26.6.2 实际通过硬件 JPEG 测试，同时强制模拟硬件不可用
验证 Core Image 回退；未以跳过硬件用例代替验证。

3.10 发布归档已确认包含新增 `frame_jpg_encoder.m`/`.h` 原生源码、资源和
类型信息，并排除 docs/、tests/、AGENTS.md。新增 Python 测试通过 Ruff 检查；
改动涉及的 Python/存根通过限定 E4/E7/E9/F 检查，未清理历史风格诊断。

```bash
uv run --python 3.14 --locked setup.py build_ext --inplace
uv run --python 3.14 --locked pytest tests/test_jpg_direct.py tests/test_native_lifecycle.py tests/test_jpg.py tests/test_lifecycle.py tests/test_device_session.py -q
uv run --python 3.14 --locked pytest tests/test_run.py --collect-only -q
```

未运行 ADB 真机操作、其他 Mac 型号或长期性能测试。独立 3.10 环境的复制、
构建和归档检查步骤见本文后续章节；测试文件列表使用上面的五个文件。

## 2026-09-07 生命周期与 API 整理验证

本次验证平台为 macOS arm64。Python 3.14.6 在正常工作区构建原生扩展；
Python 3.10.20 在独立临时源树中构建 sdist/wheel，再安装 wheel 测试。
正常 `.venv` 和 `.python-version` 继续使用 3.14。

| 验证 | 3.10.20 | 3.14.6 |
| --- | --- | --- |
| 源码、测试、setup.py、原生 .pyi 编译（23 个文件） | 通过 | 通过 |
| 全部 16 个 Python 模块导入 | 通过，路径为临时 site-packages | 通过 |
| 原生扩展构建及真实 VideoToolbox 调用 | 通过 | 通过 |
| 四个相关无设备测试文件 | 21 passed | 21 passed |
| test_run.py 仅收集 | 1 collected，未执行 | 1 collected，未执行 |

新增回归测试先确认旧行为失败，再修复关键路径：FPS 小数校验、控制流 EOF
空转、原生重复销毁、取帧取消、失败/取消连接回滚、探测回收顺序、满输出管道、
长按断连及连接期间的并发 disconnect。原生测试使用合成 64×64 H.264，另含
确定性的 GCD 排队完成测试和并发读取/入队/重复关闭压力测试。

3.10 sdist/wheel 的实际文件清单已核对：不含 docs/、tests/ 和 AGENTS.md，
sdist 保留原生源码/头文件及 scrcpy-server.bin，wheel 保留扩展、资源、.pyi、py.typed。
README/API 示例语法及限定范围的 Ruff 静态检查通过。没有连接手机或运行 ADB
设备命令；测试中的 transport 和进程均为本地模拟或合成子进程。

当前改动相关的无设备验证命令：

```bash
uv run --python 3.14 --locked setup.py build_ext --inplace
uv run --python 3.14 --locked pytest tests/test_device_session.py tests/test_lifecycle.py tests/test_native_lifecycle.py tests/test_jpg.py -q
uv run --python 3.14 --locked pytest tests/test_run.py --collect-only -q
```

原生合成测试需要本机 ffmpeg/libx264 和 macOS 编译工具链；没有 ffmpeg 时会跳过
相关用例。本次两个版本均实际执行了原生测试，没有以跳过代替验证。

## 2026-09-06 核查结果

| 检查对象 | 结果与处理 |
| --- | --- |
| `pyproject.toml`、`uv.lock` | 已是 `>=3.10`，无需修改；`uv lock --check` 通过 |
| `README.md` | 原系统要求残留 `Python >= 3.11`，已改为 3.10，并说明开发使用 3.14 |
| `tests/test_run.py` | 原截图路径在外层 f-string 中复用了引号，3.10 报 `SyntaxError: f-string: unmatched '('`；已直接使用 `os.path.join()`，保留原文件命名方式 |
| 运行源码与类型存根 | 当前的 `match/case`、联合类型 `X \| None`、`typing.TypeAlias` 等可用于 3.10；本次未发现其他需要降级的语法或标准库调用 |
| 锁定依赖 | 在 3.10 和 3.14 均成功安装；运行依赖 `aiofiles==25.1.0`，开发依赖包括 `pytest==9.0.2`、`setuptools==82.0.0` |
| 锁文件中的 `3.11` | pytest 的 `exceptiongroup`、`tomli` 使用 `python_full_version < '3.11'` 条件，为旧解释器提供回退支持；这些条件不是最低版本限制，必须保留 |
| C 扩展 | 使用两种解释器各自的头文件和 ABI 成功构建，并从对应 wheel 导入执行 |
| `native_code/macOS/CMakeLists.txt` | 3.14 头文件路径仅供 IDE 索引；正式构建由 `setup.py` 使用目标解释器的配置，不构成 3.14 运行限制 |

f-string 引号复用是 Python 3.12 才放开的语法限制，详见 [Python 3.12 的 PEP 701 说明](https://docs.python.org/3.12/whatsnew/3.12.html#pep-701-syntactic-formalization-of-f-strings)。本次修复同时消除了测试脚本对该新语法的隐式依赖。

本次验证平台为 macOS arm64，解释器为 CPython 3.10.20 和 3.14.6：

| 验证项目 | 3.10.20 | 3.14.6 |
| --- | --- | --- |
| 编译所有 Python 源码、测试、`setup.py` 和 `.pyi`（共 18 个文件） | 通过 | 通过 |
| 从干净临时副本构建 sdist，再由 sdist 构建 wheel | 通过 | 通过 |
| 安装对应 wheel，导入全部 14 个 Python 模块 | 通过 | 通过 |
| 已安装依赖的 `Requires-Python` 元数据与当前解释器匹配 | 通过 | 通过 |
| 从已安装包读取 `scrcpy-server.bin` | 通过 | 通过 |
| 大端序整数与视频帧标志/时间戳检查 | 通过 | 通过 |
| 合成 H.264 → VideoToolbox 解码 → 64×64 BGRA8 → JPEG → 显式关闭解码器 | 通过 | 通过 |
| `tests/test_jpg.py` | 1 passed | 1 passed |
| `tests/test_run.py --collect-only` | 收集 1 项，未执行 | 收集 1 项，未执行 |
| sdist/wheel 不包含 `docs/`、Agent 指南和 `tests/`，必需资源及类型信息仍在 | 通过 | 通过 |

H.264 冒烟验证使用本机 ffmpeg 的 `testsrc2` 和 libx264 生成单帧合成视频，拆出 SPS/PPS/IDR 后调用真实扩展，检查尺寸、BGRA8 长度、JPEG 首尾标记和解码器关闭状态。ffmpeg 仅用于本次核查，没有增加项目依赖。

本次没有运行真机控制、长时间多设备稳定性测试或 macOS x86_64 测试；这些范围不能由上述结果推断。普通 CPython 的验证也不代表 free-threaded Python 支持。架构和生命周期边界见 [architecture.md](architecture.md)。

## 默认开发验证（3.14）

在仓库根目录执行：

```bash
uv sync --python 3.14 --locked
uv run --python 3.14 --locked python --version
uv run --python 3.14 --locked setup.py build_ext --inplace
uv run --python 3.14 --locked pytest tests/test_jpg.py -v
```

`tests/test_run.py` 会要求输入、启动 ADB 并控制首台设备；`test.sh` 会运行完整测试集。只有明确需要真机测试时才执行它们。

## 最低版本语法检查（不连接手机）

此命令在内存中编译文件，不执行测试或导入项目，也不替换开发环境：

```bash
uv run --no-project --python 3.10 python - <<'PY'
from pathlib import Path
import sys

paths = [
    Path("setup.py"),
    *Path("src").rglob("*.py"),
    *Path("src").rglob("*.pyi"),
    *Path("tests").rglob("*.py"),
]
for path in sorted(paths):
    compile(path.read_text(encoding="utf-8"), str(path), "exec")
print(f"Python {sys.version.split()[0]}: {len(paths)} files compiled")
PY
```

## 独立环境中的 3.10 构建和测试

从仓库根目录开始。先复制 Git 跟踪及未忽略的新文件，保留本次尚未提交的改动；忽略目录中的 `.venv`、构建缓存和原生二进制不会被复制。后续命令使用临时副本，不改变工作区的 3.14 环境：

```bash
compat_dir=$(mktemp -d "${TMPDIR:-/tmp}/adb-scr-compat.XXXXXX")
uv run --no-project --python 3.14 python - "$compat_dir/source" <<'PY'
from pathlib import Path
import shutil
import subprocess
import sys

target = Path(sys.argv[1])
names = subprocess.check_output([
    "git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"
]).decode().split("\0")
for name in names:
    source = Path(name)
    if name and source.is_file():
        destination = target / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
PY

uv --directory "$compat_dir/source" sync --python 3.10 --locked --no-install-project
uv build "$compat_dir/source" --python 3.10 --out-dir "$compat_dir/dist"
uv pip install --python "$compat_dir/source/.venv/bin/python" --no-deps "$compat_dir/dist/"*.whl
uv --directory "$compat_dir/source" run --no-sync --python 3.10 python --version
uv --directory "$compat_dir/source" run --no-sync --python 3.10 python -c 'import adb_scr; print(adb_scr.__file__)'
uv --directory "$compat_dir/source" run --no-sync --python 3.10 pytest tests/test_jpg.py -v
uv --directory "$compat_dir/source" run --no-sync --python 3.10 pytest tests/test_run.py --collect-only -q
```

导入路径应指向临时 `.venv` 的 `site-packages`，证明测试使用实际安装的 wheel。这里的 `--no-sync` 用于保留刚安装的 wheel，避免 `uv run` 将其切回可编辑安装。对应 3.14 发布验证也应另建临时副本，按上述流程替换版本号执行。

## 发布内容检查

`MANIFEST.in` 使用 `prune docs`、`prune tests` 和 `exclude AGENTS.md` 排除仓库内部文档与测试。检查刚才的实际构建产物：

```bash
uv run --no-project --python 3.14 python - "$compat_dir/dist" <<'PY'
from pathlib import Path, PurePosixPath
import sys
import tarfile
import zipfile

dist = Path(sys.argv[1])
archives = [*dist.glob("*.tar.gz"), *dist.glob("*.whl")]
assert len(archives) == 2, archives
for path in archives:
    if path.suffix == ".whl":
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
        required = "adb_scr/res/scrcpy-server.bin"
        assert "adb_scr/py.typed" in names
        assert "adb_scr/media_ext/_adb_scr_media.pyi" in names
        assert any(name.endswith(".so") for name in names)
    else:
        with tarfile.open(path) as archive:
            raw_names = archive.getnames()
        names = [name.split("/", 1)[1] for name in raw_names if "/" in name]
        required = "src/adb_scr/res/scrcpy-server.bin"
        assert "native_code/macOS/src/adb_scr_media.c" in names
        assert "native_code/macOS/include/vtb_decoder.h" in names
    forbidden = {"docs", "tests", "AGENTS.md", "CLAUDE.md"}
    assert not any(forbidden.intersection(PurePosixPath(name).parts) for name in names)
    assert required in names
    print(f"{path.name}: package contents verified")
PY
```

setuptools 可能提示 `no previously-included files found matching 'AGENTS.md'`，表示该文件原本就没有进入自动清单；仍需以最终归档检查为准。
