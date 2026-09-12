# GitHub Actions 构建与测试

配置见 [ci.yml](../.github/workflows/ci.yml)。推送到 `master` 后自动运行，也可在
Actions 页面使用 **Run workflow** 手动触发。其他分支的 push 不触发。

## 版本矩阵

| macOS runner | 架构 | Python |
| --- | --- | --- |
| `macos-15` | arm64 | 3.10、3.11、3.12、3.13、3.14、3.14t |
| `macos-26` | arm64 | 3.10、3.11、3.12、3.13、3.14、3.14t |

共 12 个独立任务，一个失败不会取消同次运行的其他版本。连续推送同一分支时，
取消旧的 workflow。每个任务最多 20 分钟，产物与 JUnit 报告保留 7 天。

截至 2026-09-11，选择 GitHub 提供的最近两个稳定 macOS 镜像；显式版本避免
`macos-latest` 迁移期间覆盖范围漂移。macOS 14 已进入退役流程，将于
2026-11-02 停用，因此不加入新矩阵。未来新增稳定镜像时更新此表与 workflow。
标签及支持状态以 [runner 镜像列表](https://github.com/actions/runner-images) 和
[macOS 14 退役公告](https://github.com/actions/runner-images/issues/13518) 为准。

## 每个任务执行的检查

1. 使用 uv 0.12.13 和目标 Python，以 `uv sync --locked --no-install-project`
   安装锁定的运行及开发依赖。
2. 通过 `setup.py build_ext --inplace` 编译 C/Objective-C 扩展，再用 `uv build`
   构建 sdist，并从 sdist 构建目标解释器 ABI 对应的 wheel。
3. 安装刚构建的 wheel；随后使用 `uv run --no-sync`，避免恢复可编辑安装。
4. [check_ci.py](../.github/scripts/check_ci.py) 编译源码、测试、构建脚本和
   `.pyi`；检查归档的 Python/原生源码、扩展、资源与类型信息，以及内部文件排除规则。
   同时验证 wheel ABI，并从 site-packages 导入所有模块和原生扩展。
5. 运行显式列出的无设备测试，覆盖手势、连接生命周期、音频协议、录制编排、
   真实 BGRA8 → JPEG 编码、原生线程状态与取消等待。真机 `test_run.py` 仅收集。
6. 3.14t 校验解释器构建标志，并在导入前后及测试结束时确认 GIL 关闭；清除
   `PYTHON_GIL`，不使用 `-X gil=0`。普通版本只跳过 free-threaded 专属用例。

Python 仅固定小版本，实际补丁版本由固定的 uv 版本及 runner 环境解析并记录在日志。
普通 3.14 显式选择 `3.14+gil`，防止 uv 将未限定的 `3.14` 匹配到 free-threaded
解释器；这是选择普通构建，不是强制改变 3.14t 的 GIL 状态，见
[uv 版本选择说明](https://docs.astral.sh/uv/concepts/python-versions/#free-threaded-python)。
普通 3.14 与 3.14t 分别构建、安装、测试，不共享 `.venv` 或原生扩展。
流程不修改 lockfile，不运行 `test.sh`。

## 云端覆盖边界

标准 GitHub macOS runner 是虚拟机，不能把它的运行结果当作真实 Mac 媒体硬件的
验证。库本身要求 VideoToolbox 硬件解码，录制也要求硬件 H.264 编码；CI 显式选择
不依赖这些硬件的测试，不改变库的硬件要求或测试断言。新增测试应先判断是否适合
标准 runner，再加入 `CI_TESTS`。

合成 H.264 解码、JPEG 直出、真实 H.264/AAC 录制、原生句柄并发压力和完整媒体
链路仍按 [Python 兼容性文档](python-compatibility.md) 在真实 Mac 上验证。
这份 CI 不覆盖 Intel Mac、Android 真机操作或性能指标。构建产物仅作为 Actions
artifact 保存，不发布到 PyPI 或 GitHub Release。独立的 wheel 发布 workflow、
跨 macOS 安装验证和 Trusted Publisher 配置见 [发布文档](releasing.md)。

## 本地复现

先按 [独立环境步骤](python-compatibility.md#独立环境中的-310-构建和测试) 创建
临时源码副本；不要在日常工作树里切换 `.venv`。在该临时副本中执行，替换版本
即可复现其他矩阵项：

```bash
export CI_PYTHON_VERSION=3.10
export UV_PYTHON="$CI_PYTHON_VERSION"
if [ "$CI_PYTHON_VERSION" = 3.14 ]; then
  export UV_PYTHON=3.14+gil
fi
uv sync --locked --no-install-project
uv run --no-sync setup.py build_ext --inplace
uv build --out-dir dist
uv pip install --python .venv/bin/python --no-deps dist/*.whl
env -u PYTHON_GIL PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run --no-sync python -Werror::RuntimeWarning .github/scripts/check_ci.py "$CI_PYTHON_VERSION"
```
