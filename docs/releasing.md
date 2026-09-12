# macOS arm64 wheel 与 PyPI 发布

[release.yml](../.github/workflows/release.yml) 构建 Python 3.10、3.11、3.12、
3.13、3.14、3.14t 的 arm64 wheel，并在成功验证后通过 Trusted Publishing 发布。
普通 `master` push 仍只运行 [CI](ci.md)。

## 产物与兼容范围

在 `macos-15` 上设置 `MACOSX_DEPLOYMENT_TARGET=15.0`、`ARCHFLAGS=-arch arm64`，
每种 Python 独立构建原生扩展、sdist，再从 sdist 构建 wheel。发布的 wheel 标签为：

| Python | Python / ABI / 平台标签 |
| --- | --- |
| 3.10 | `cp310-cp310-macosx_15_0_arm64` |
| 3.11 | `cp311-cp311-macosx_15_0_arm64` |
| 3.12 | `cp312-cp312-macosx_15_0_arm64` |
| 3.13 | `cp313-cp313-macosx_15_0_arm64` |
| 3.14 | `cp314-cp314-macosx_15_0_arm64` |
| 3.14t | `cp314-cp314t-macosx_15_0_arm64` |

这些预编译包面向 macOS 15 及更新系统上的 Apple Silicon。当前在 macOS 15、26
两种 runner 上验证；macOS 26 下载并安装 macOS 15 产物，不重新构建待发布 wheel。
不生成 x86_64、universal2、Linux 或 Windows wheel，也不承诺较老 macOS 的源码
构建兼容性。平台标签含义见 [Python 打包规范](https://packaging.python.org/en/latest/specifications/platform-compatibility-tags/#macos)。

两边复用 `check_ci.py` 验证已安装 wheel、归档内容、ABI、无设备测试和 GIL 状态。
云端不覆盖真实 VideoToolbox 媒体硬件；验证边界及本地硬件测试见 [CI 文档](ci.md)。
普通 3.14 显式选择 `3.14+gil`，3.14t 独立构建，不强制设置 GIL 状态。

12 个构建/安装测试任务全部成功后，`prepare_release.py` 检查六种 wheel 是否齐全、
平台/ABI 是否正确、文件名与包元数据版本是否一致，然后选择一份 sdist。
`twine check --strict` 通过后，将 **6 个 wheel + 1 个 sdist** 保存为
`pypi-distributions` artifact，保留 30 天。各 Python 的原始产物和 JUnit 报告也保留
30 天。汇总时先下载到独立目录，避免不同任务同名 sdist 互相覆盖。

## Trusted Publisher 配置

在现有 PyPI 项目 `adb_scr_py` 的 **Publishing** 页面绑定：

| 字段 | 值 |
| --- | --- |
| Owner | `czf0613` |
| Repository name | `adb_scr_py` |
| Workflow name | `release.yml` |
| Environment name | `pypi` |

GitHub 仓库中的 `pypi` environment 仅允许 `v*` 标签部署。发布 job 在 Ubuntu
上下载完整产物，通过 `id-token: write` 获取短期 OIDC 凭据；构建和测试 job 没有
该权限。无需创建或保存 PyPI API token。配置原理见
[PyPI Trusted Publishing](https://docs.pypi.org/trusted-publishers/using-a-publisher/)。

## 试构建与正式发布

仅构建验证，使用 GitHub Actions 页面 **Run workflow**，或：

```bash
gh workflow run release.yml --ref master
```

手动运行会生成完整 artifact，`publish` job 被跳过。

正式发布时，先更新 `pyproject.toml` 的版本，并执行 `uv lock` 更新锁文件；提交
推送后，在对应提交上发布 GitHub Release，标签必须等于 `v` 加包版本，例如包版本
`0.3.0` 对应标签 `v0.3.0`。仅 push 标签或保存 Release 草稿不会触发发布。
GitHub Release 的 `published` 事件触发全部构建测试，通过后自动上传 PyPI。

只有已验证的那批文件会上传；不会在发布 job 再次构建。未开启跳过已存在文件，
版本/文件冲突会报错。PyPI 上传与依赖身份验证的完整链路，要到首次正式发布时
才能验证；手动试构建不执行 PyPI 上传。
