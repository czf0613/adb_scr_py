# Python 版本与兼容性验证

## 版本约定

运行时最低支持 **Python 3.10**；本地开发和默认测试继续使用 **Python 3.14**。`pyproject.toml` 和 `uv.lock` 的 `requires-python` 均为 `>=3.10`。本地 `.python-version` 保持 `3.14`，目前该文件被 `.gitignore` 排除。

Python 源码、测试、构建脚本和 `.pyi` 均须兼容 3.10。新增依赖或使用较新语法/标准库 API 时，要在真实 3.10 解释器下验证；仅在 3.14 上通过测试不能证明最低版本可用。

推送到 `master` 后的自动构建矩阵为 macOS 15/26 arm64 × Python 3.10–3.14、3.14t。
CI 检查已安装 wheel、归档内容、源码语法、无设备测试和 3.14t 的实际 GIL 状态。
云端测试选择及真实媒体硬件验证的边界见 [CI 文档](ci.md)。

预编译发布 wheel 分为 `macosx_15_0_arm64`、`macosx_26_0_arm64` 两组，分别在
macOS 15、26 构建并在各自系统上安装验证，均覆盖上述六种 Python ABI。
发布流程见 [发布文档](releasing.md)。

## 2026-09-12 0.3.2 发布前验证

MCP 的工具说明、quality 参数 schema 和包内 Agent 指南补齐录屏质量范围、
默认 0.75、与截图质量范围的区别，以及画质/体积取舍和 AAC 直通说明。
`pyproject.toml` 与 `uv.lock` 同步为 0.3.2。普通 Python 3.14.7 重新原地
构建后，全部无设备测试 **253 passed, 1 skipped**；没有执行 `test_run.py`。

独立临时源树分别以 Python 3.10.20、3.14.7、3.14.6t 构建 0.3.2 sdist/wheel
并安装，从 site-packages 执行 `check_ci.py`。普通版本各 **173 passed,
1 skipped**，3.14t 为 **174 passed**；源码编译、全部模块导入、实际归档
内容/ABI、包内 MCP 指南及只收集真机测试均通过，3.14t 的 GIL 检查通过。
本地三个 wheel 使用 macOS 15 deployment target；macOS 15/26 与其他 Python
版本的完整发布矩阵由 GitHub Actions 验证，本地测试不能代替该矩阵。

## 2026-09-12 录制 quality 参数验证

`AndroidDevice.start_recording(output_file, quality=0.75)` 新增可选质量参数，
MCP 同步公开。接受 0.0–1.0 的有限 int/float，拒绝 bool 和其他类型；在创建
文件前校验，经 Python 控制器、解码器包装层和 C 扩展传至原生录制器，在
首帧编码前设置 `kVTCompressionPropertyKey_Quality`。原生五参数旧调用
继续可用并采用 0.75；不添加固定码率或速度优先提示。推荐 Apple silicon，
因使用部分 Metal 特性，不保证 Intel Mac 能正常使用。

新增回归先在旧实现上确认失败，再完成参数实现。普通 Python **3.14.7**
原地构建后，三个录制测试文件和 MCP server 测试共 **90 passed**。
Python **3.10.20**、**3.14.6t** 在独立源树构建并安装 wheel，运行上述测试
及 free-threading 检查，分别 **91 passed, 1 skipped** 和 **92 passed**；
3.14t 未强制关闭 GIL，导入前、导入后及测试后均保持关闭。原生 probe 从
实际硬件 session 读回自定义 Quality=0.625，0/0.4/0.75/1 均完成 H.264
编码。校验覆盖默认值、自定义值、范围、非有限数、bool 和 MCP schema/转发。
44 个 Python/存根/测试文件通过真实 3.10 语法编译；实际 sdist/wheel 的
源码、类型、MCP 指南、资源及排除规则已核对，安装的扩展与 wheel 内容一致。

在 Apple M5 Max / macOS 26.6.2 另做两组离线体积比较。每组使用同样的
180 帧（30 FPS、6 秒），仅改变 Quality 设置，原生录制队列逐帧排空以避免
过载丢帧影响比较。旧实现未设置 Quality，系统读回 -1；新实现读回传入值。
合成画面使用 1920×1080 testsrc2；已有录屏样本取自上一轮用户授权录制的
2400×1080 视频，先解码为相同的无损中间帧序列。后者是对已压缩录屏的离线
重编码，不代替手机原始输入的实时画质/性能验证。该离线比较未连接或操作手机。

| 同一段 6 秒画面 | 旧系统默认 | Quality=0.75 | Quality=0.5 |
| --- | --- | --- | --- |
| 合成 testsrc2 | 8.90 MB | 9.28 MB（增加约 4%） | 3.67 MB（减少约 59%） |
| 已有录屏画面 | 9.71 MB | 5.55 MB（减少约 43%） | 1.68 MB（减少约 83%） |

以上为单轮、单机样本，MB 按十进制计算，不承诺固定缩减比例。已有录屏
样本相对共同输入的 SSIM：旧默认 0.979915、0.75 为 0.977677、0.5 为
0.969036；该指标不等于主观画质保证。默认保持用户指定的 0.75，文档明确
quality 不是文件体积比例，1.0 也不保证 H.264 无损。测试视频仅保留在本机
临时目录，不进入仓库。

后续经用户明确授权，使用默认 quality=0.75 录制 Android 14 真机当前画面
20 秒，保持当时的竖屏 1080×2400，不执行点击、滑动或旋转。实际文件时长
20.001148 秒，9,222,759 字节（9.22 MB），整体平均码率约 3.687 Mbps；
601 个视频包，约 30 FPS，最大相邻视频 PTS 间隔 42.052 ms。48 kHz 双声道
AAC 共 937 包，与源输入逐包 SHA-256 一致，输出音频连续且非静音；源 PTS
与采样时钟最大偏差 4.924 ms，小于一个 AAC 包。音视频完整解码无错误，
抽查第 1、10、19 秒画面，人物及字幕正常。start 约 68.9 ms，stop 约
10.1 ms；停止后保持连接、重复停止与 disconnect/deinit 清理均通过。
当前画面包含较大的静态黑色区域，不用这份文件大小推断其他内容的压缩比例。

```bash
uv run --python 3.14 --locked --extra mcp setup.py build_ext --inplace
uv run --python 3.14 --locked --extra mcp pytest tests/test_recording.py tests/test_recording_native.py tests/test_recording_integration.py tests/test_mcp_server.py -q
```

## 2026-09-12 录制旋转 Metal 优化验证

录制的 BT.709/未标记输入在尺寸变化时由 Metal 直接适配 NV12 平面；
显式非 BT.709 输入保留基于 Metal 的 Core Image 色彩转换。shader 在
录制开始时准备，首次加载成本不放在首次旋转时承担。原始 BGRA8/JPEG
路径继续保留，公共函数签名不变。

普通 Python **3.14.7** 原地构建后，相关六个文件通过 **87 项，跳过 1 项**；
后续颜色回归修复后重新构建，三个录制测试文件通过 **42 项**。
最终源码在独立源树构建并安装 wheel，Python **3.10.20** 通过
**87 项，跳过 1 项**；Python **3.14.6t** 通过 **88 项**，导入前、
导入后及测试后 GIL 均关闭，没有强制设置 PYTHON_GIL 或 -X gil。
3.10/3.14t 归档仍包含原生源码、扩展、类型和 scrcpy 资源，排除 docs/tests/AGENTS.md。

新增原生像素 probe 覆盖同尺寸直接引用、横竖适配、非 16 对齐尺寸、Y/UV
黑边、常量与渐变采样、显式 BT.601 到 BT.709 转换；后者先在未经修正的
直接 Metal 路径下失败。新公共 API 回归覆盖两次配置/解码器替换过程中
录制器不变、视频时间戳递增及 40 个 AAC 包的哈希和 PTS 保留。
Metal API validation 下的像素与旋转检查通过。额外临时 probe 比较非均匀
BT.601 输入，三组尺寸下新颜色转换分支与旧实现的 37,446 字节输出一致。
上述自动回归全部使用合成媒体，未连接或操作 Android；真机验证见下文。

```bash
uv run --python 3.14 --locked setup.py build_ext --inplace
uv run --python 3.14 --locked pytest tests/test_recording_native.py tests/test_recording_integration.py tests/test_recording.py tests/test_native_lifecycle.py tests/test_jpg_direct.py tests/test_free_threading.py -q
MTL_DEBUG_LAYER=1 uv run --python 3.14 --locked pytest tests/test_recording_native.py::test_rotation_fit_preserves_nv12_planes_and_black_levels -q
```

性能对比在 **Apple M5 Max / macOS 26.6.2** 完成：用合成、显式标记 BT.709
的 NV12 输入调用私有 fit_frame，输出宽高对调。输出来自 H.264 编码器 pool；
计时包括 buffer/texture 操作、适配、GPU 完成和输出像素锁定读取，排除 H.264
编码、音频、磁盘写入与 Python 调用。每组先预热 10 帧，再测 150 帧，交替
运行旧 Core Image 与最终 Metal 实现三轮，以下取三轮中位数的中位数。

| 输入 → 输出 | 原 Core Image | Metal NV12 | 适配耗时降低 |
| --- | --- | --- | --- |
| 1080×2400 → 2400×1080 | 0.335 ms | 0.196 ms | 约 42% |
| 1440×3200 → 3200×1440 | 0.389 ms | 0.194 ms | 约 50% |

这是当前机器、合成帧和稳态适配步骤的测量，不是整段录制吞吐承诺，也不代表
Intel Mac、其他 Apple Silicon 或非 BT.709 转换分支的性能。首次加载 shader
曾观测到约 94 ms，已移至 start_recording 的准备阶段。当前构建未额外封锁
x86_64，但录制要求 Metal；本次只在 arm64 实机验证。Metal 本身也支持部分
Intel Mac，并非 Apple Silicon 专属；是否可录制仍取决于 Metal 与硬件编解码
能力，尚未进行本库的 Intel 真机验证。由于本库使用部分 Metal 特性，不保证
Intel Mac 能正常使用，推荐 Apple silicon。[Apple Metal 支持范围](https://support.apple.com/en-euro/102894)

经用户授权，在已播放 B 站视频的 Android 14 真机上完成两轮录制；每轮退出、
进入全屏各一次，实际尺寸按 2400×1080 → 1080×2400 → 2400×1080 变化。
四次配置变化均触发解码器替换，原生录制器始终为同一实例；停止录制无错误，
设备会话仍连接，最后恢复全屏播放并完成 disconnect/deinit 清理。

| 真机录制 | 第一轮 | 第二轮 |
| --- | --- | --- |
| 视频时长 | 110.566 s | 75.920 s |
| MP4 视频包 | 3,304 | 2,264 |
| MP4 AAC 包 | 5,183 | 3,559 |
| 两次切换附近最大视频帧间隔 | 218.6 / 245.9 ms | 334.5 / 212.8 ms |
| 本地解码器替换耗时 | 4.7 / 10.8 ms | 11.1 / 7.0 ms |

两份 MP4 的音视频时间戳均递增，使用 FFmpeg 的 VFR passthrough 和 demux
timebase 完整解码，无错误输出。输出画布始终为 2400×1080，抽帧确认竖屏
居中加黑边、恢复横屏后画面正常。切换附近的视频间隔包含手机端旋转、上游
编码器重建及媒体恢复，不能归因于 Metal 适配耗时，也不承诺旋转无停顿。

第二轮全部 3,559 个 AAC 压缩包逐包 SHA-256 与输入一致，无丢包或插包，
输出包间隔约 21.333 ms；输入 PTS 每包递增 21,333 微秒，输出采用 48 kHz
连续采样时钟，最大偏差 1.183 ms，符合一包内时间戳抖动的现有契约。
第一轮初版取证脚本漏采录制启动交界的首包，其余 5,182 包全部对应一致；
该轮最大源时间戳偏差 4.528 ms，同样小于一个 AAC 包。第二轮另曾因临时
脚本的 ADB 子进程继承 stdin，消耗预先排队的停止命令而延长录制；进程采样
确认主流程在等 stdin，单独发送停止命令即成功，未发现录制停止阻塞。
设备截图和视频仅保留于本机临时目录，不进入仓库。

## 2026-09-12 MCP Agent 指南及真机补充验证

0.3.1 发布前，普通 3.14.7 的完整无设备测试通过 **226 项，跳过 1 项**。
独立临时源树中的 3.10.20、3.14.7、3.14.6t 均重新构建并安装 0.3.1 wheel，
通过更新后的 `check_ci.py`：普通版本各 **159 passed, 1 skipped**，3.14t
为 **160 passed**，导入及测试后 GIL 仍关闭。三份归档的 MCP 模块、Agent 指南、
原生资源和内部文件排除规则通过验证；`test_run.py` 均只收集。
两个 workflow 通过 actionlint 1.7.12；发布构建明确覆盖 macOS 15 和 26。

Agent 指南通过初始化 instructions、`adb-scr://guide` resource 和
`get_agent_guide` 工具提供，正文作为包资源分发。新增指南发现/读取及工具
readOnlyHint 回归测试后，Python **3.14.7** 和独立临时环境中的
Python **3.10.20** 均通过两个 MCP 测试文件中的 **28 项无设备测试**。
3.10 重新构建、安装 wheel，验证包内指南可读取；sdist/wheel 均包含指南，
原有原生源码、扩展和类型资源保留，docs/tests/AGENTS.md 仍未进入归档。

经用户明确授权，另用一次性 Codex CLI 配置连接真实 MCP 服务。Codex 先读取
指南，再枚举、连接手机，通过截图、点击、滑动进入设置中的设备信息页面，读取
两个 IMEI 栏位，并用原比例高质量 ROI 截图核对；随后由另一 MCP 客户端截图复核。
不在仓库保存设备标识或截图。保持设备连接时发送 Ctrl+C，日志确认关闭所有
设备会话、删除临时 scrcpy 资源、完成 `deinit_lib()`，服务以退出码 0 结束。

## 2026-09-12 FastAPI MCP 服务初次验证

新增可选 `mcp` extra 与 `src/adb_scr_mcp`，基于 FastAPI/官方 MCP SDK 提供
Streamable HTTP 工具，由 lifespan 管理库及设备所有权，专用 Uvicorn runner
处理 Ctrl+C/SIGTERM 和重复信号。入口和使用方法见 [MCP 文档](mcp.md)。

普通 Python **3.14.7** 原地构建扩展；Python **3.10.20** 在独立临时源树
构建 sdist/wheel，安装 wheel 后导入和测试。两个版本均通过下列四个文件中的
**42 项无设备测试**，其中新增 MCP 测试 26 项。测试覆盖真实 HTTP 和官方
MCP 客户端初始化/调用、JPEG 图片内容、输入拒绝、多指转换、设备复用、失败清理、
并发 lifespan 所有权、asyncio/AnyIO 取消，以及真实子进程下 SIGINT、SIGTERM、
重复信号、启动中退出、连接中退出和绑定端口失败。

3.10 编译全部 43 个源码/测试/存根文件，并导入全部源码模块，确认使用临时
site-packages 中的安装包。归档包含新 MCP 模块、py.typed、console script、
可选依赖元数据，以及原有原生源码/头文件、scrcpy 资源、扩展及类型存根；
不包含 docs/、tests/、AGENTS.md。正常 `.venv` 和 `.python-version` 保持 3.14。

本次未修改原生并发实现，没有重跑 3.14t。MCP 验证使用设备/ADB 替身，没有
连接手机；`tests/test_run.py` 仅包含在语法编译中，没有执行。

```bash
uv run --python 3.14 --locked --extra mcp setup.py build_ext --inplace
uv run --python 3.14 --locked --extra mcp pytest tests/test_mcp_server.py tests/test_mcp_shutdown.py tests/test_device_session.py tests/test_lifecycle.py -q
```

3.10 沿用后文独立源树流程，环境安装改为
`uv sync --python 3.10 --locked --extra mcp --no-install-project`，安装 wheel 后
使用 `uv run --no-sync --python 3.10 pytest` 执行上述四个文件。

## 2026-09-11 H.264/AAC 屏幕录制验证

新增 `AndroidDevice.start_recording(output_file)` / `stop_recording()`，连接前
检测 Android API 级别并选择 AAC 音源；原生 VideoToolbox 重编码 NV12 视频，
AAC 压缩包通过 CoreMedia/AVFoundation 封装。实现与时间精度见 [录制 API](api.md#屏幕录制)。

| 验证 | 3.10.20 | 普通 3.14.7 | 3.14.6t |
| --- | --- | --- | --- |
| 10 个相关无设备测试文件 | 124 passed，1 skipped | 124 passed，1 skipped | 125 passed |
| 33 个 Python/存根文件、12 段文档示例编译 | 通过 | 通过 | 通过 |
| 全部 18 个 Python 模块导入 | 通过 | 通过 | 通过 |
| 原生扩展及真实硬件 H.264 编码 | 通过 | 通过 | 通过 |
| `test_run.py` 仅收集 | 1 collected | 1 collected | 1 collected |
| 未强制关闭 GIL，导入及测试后仍关闭 | 不适用 | 不适用 | 通过 |

普通构建仅跳过 free-threaded 专属的 GIL 检查。3.10 和 3.14t 在各自独立
临时源树构建 sdist/wheel，并从安装后的 site-packages 运行测试；本地 `.venv`
仍为普通 3.14，`.python-version` 保持 `3.14`。两份发布归档已检查：新录制
原生源码/头文件、Python 模块、scrcpy 资源、类型存根和扩展完整，
docs/、tests/、AGENTS.md、`.superpowers/` 均未打包。

验证包括 SDK 分支和三条流的协议顺序、AAC 原包与时间戳、静止画面的首尾帧、
P 帧期间开始录制、旋转、取消、断连、重复停止、并发销毁、队列上限及失败排空。
音频覆盖 0/1/2/3/4/40 包、零起点/延后起点、时间抖动与间隔，结合 ffprobe
的包哈希和 Apple AVAssetReader 的有效播放区间检查，避免预填充导致音频偏移。
原生探针验证硬件编码器实际启用，以及复制失败时清理部分临时文件、保留已有
同名文件和原始输出。现有 BGRA8/JPEG、线程状态及原生生命周期回归均通过。

```bash
uv run --python 3.14 --locked setup.py build_ext --inplace
uv run --python 3.14 --locked pytest tests/test_audio_stream.py tests/test_recording.py tests/test_recording_native.py tests/test_recording_integration.py tests/test_device_session.py tests/test_lifecycle.py tests/test_native_lifecycle.py tests/test_jpg_direct.py tests/test_jpg.py tests/test_free_threading.py -q
uv run --python 3.14 --locked pytest tests/test_run.py --collect-only -q
```

### 同日真机验证：初始低码率配置

用户连接并播放有声视频后，在普通 Python 3.14.7 环境验证 Xiaomi 22041211AC，
Android 14 / API 34。自动选择 `audio_source=playback`、`audio_dup=true`，
实际收到 AAC-LC 配置和 48 kHz 双声道音频；用户确认采集期间手机仍正常外放。

20 秒动态录制及三次短录制均成功，H.264 为 1080×2400，AAC 原包直接封装。
这四次开始前最新输入均为 P 帧，输出均以 PTS 0 的关键帧开始，
`start_recording()` 实测耗时约 24–33 ms，`stop_recording()` 约 7–13 ms。
20 秒文件的有效 942 个 AAC 包全部与接收包哈希匹配，完整音视频解码成功且
音频非静音；按共同录制原点比较，AAC 包时间与来源差异在约 -3.52 至 +0.06 ms。
视频轨时长 20.097392 秒，容器时长 20.110521 秒，尾部差异小于一个 AAC 包。
录制期间 JPEG 截图、停止后保持连接及重复停止均通过。

另一次录制 3 秒后主动 `disconnect()`，会话自动完成 MP4，随后重复调用
`stop_recording()` 成功。视频轨时长 3.048092 秒与冻结的停止边界一致；
有效 142 个 AAC 包全部匹配来源，完整音视频解码成功。该次开始约 36 ms，
整个断连收尾约 27 ms。测试仅使用连接、录制和截图接口，没有注入手势；
结束时释放本次 scrcpy 会话，保留共享 ADB daemon。

这些数据是本机、该设备和本次内容的观测值，不是跨设备延迟保证。
时间戳检查不等同于人工口型同步验证；其他 Android 版本的音源分支目前由
无设备测试覆盖，未在对应真机上验证。

### 同日调整：使用系统默认编码画质

用户反馈低码率模糊后，移除 `AverageBitRate=256000`、速度优先提示、
Baseline Profile 和两秒关键帧间隔。码率、Quality、Profile、关键帧间隔
由 VideoToolbox 使用默认值，保留实时编码、预期帧率、禁止帧重排与色彩描述。
没有改变音频直通或录制生命周期。

在同一手机暂停画面上，修改前后各录制约 5 秒：

| 指标 | 原低码率配置 | 系统默认配置 |
| --- | --- | --- |
| MP4 大小（十进制） | 230,010 字节 | 355,607 字节 |
| 视频轨时长 | 5.078974 秒 | 5.065720 秒 |
| 实际 H.264 Profile | Baseline | High |
| 接收的新视频包 | 22 | 11 |
| 首帧视频区域 SSIM | 0.921723 | 0.984744 |

SSIM 对比每次录制的首帧与紧邻开始前获取的 BGRA8 源画面，区域为
`(0, 802, 1080, 608)`，通过 ffmpeg 转为同一像素格式计算；这里只反映
该暂停画面的重编码损失，不是所有内容的质量保证。视觉检查确认默认配置下
人脸、发丝和文字保留了更多细节。两次录制的状态栏等区域仍有更新，
包数不同，因此文件大小只能作为样本，不能当作固定压缩比。

默认配置的开始/停止约 39/18 ms，输出首帧为 PTS 0 关键帧，视频轨结束
与冻结停止时间一致，音视频完整解码成功；最长约 2.48 秒未收到新视频帧。
另一次 0.2 秒短录制也成功。整个录制期间没有新视频帧的情况已通过原生和
公开 API 的无设备测试，这两次默认配置真机录制仍分别收到 11/2 个视频包。
用户恢复播放后，使用同一默认配置完成 20 秒动态录制：

| 指标 | 默认配置动态样本 |
| --- | --- |
| MP4 大小（十进制） | 37,351,850 字节，约 37.35 MB |
| 容器 / 视频轨时长 | 20.086458 / 20.076107 秒 |
| 视频 | H.264 High，1080×2400，603 帧，约 30 fps |
| 实际视频码率 | 14,749,897 bit/s，约 14.75 Mbps |
| 音频 | AAC-LC，48 kHz 双声道，约 128 kbps |
| 开始 / 停止耗时 | 42.45 / 13.70 ms |

开始前最新输入为 P 帧，输出首帧为 PTS 0 的关键帧，后续视频时间戳严格递增。
视频轨结束与冻结停止边界一致。有效 941 个 AAC 包全部匹配手机回传内容，
相对共同原点的音频包时间差约 -0.704 至 +0.202 ms。完整音视频解码成功，
音频非静音；录制期间截图、停止后保持连接与重复停止均通过。
抽查第 0、10、19 秒画面，人物、服饰和字幕细节清楚。测试后已关闭本次
scrcpy 会话并保留共享 ADB daemon。

旧低码率的 20 秒样本约 1.84 MB；两段视频内容不同，不据此给出固定增幅。
按当前样本粗略折算约 112 MB/分钟，实际大小仍随画面内容变化。

该设置变更后，普通 3.14.7、独立 3.10.20 与独立 3.14.6t 均重新构建，
`test_recording_native.py` 和 `test_recording_integration.py` 各通过 25 项测试。
3.14t 未强制关闭 GIL，导入前、导入后与测试结束时均保持关闭。两份新构建的
sdist/wheel 已核对原生源码、更新后的类型文档、扩展、scrcpy 资源与排除规则。

## 2026-09-08 手势配对与多指验证

`GestureActionNode` 新增末尾参数 `pointer_id=0`，原有四个位置参数仍可使用。
`action_series(check=True)` 现在按 ID 校验 DOWN/MOVE/UP，并在结束时要求所有
手指抬起；不同 ID 可以交错操作，同一 ID 抬起后可以复用。控制消息传递实际 ID，
单指 click/long_press/swipe 统一使用默认 ID 0。详细约定见 [API 手势说明](api.md)。

Python 3.10.20、普通 3.14.6、3.14.6t 各通过 78 项相关无设备测试，覆盖
`test_action_series.py`（62 项）、`test_device_session.py` 和 `test_lifecycle.py`。
新增用例通过真实手势校验与控制消息编码、录制写出的协议字节，覆盖四指连续按下、
乱序抬起、交错 MOVE、ID 复用、缺少最终 UP、错误配对、未按下/已抬起后的 MOVE、
并发触点上限、非法 ID、整份列表拒绝、check=False 和原有单指调用。
修复前已复现不完整手势仍发送事件的问题，再实现校验与多指编码。

3.10 和 3.14t 均在独立源树构建 sdist/wheel，并从安装后的 site-packages 测试；
3.14t 未强制关闭 GIL，导入及测试后均确认 GIL 保持关闭。归档中的代码、资源、
扩展和类型信息已核对，docs/、tests/、AGENTS.md 保持排除。正常环境仍使用 3.14。
三个版本均编译 27 个 Python/存根文件、导入 16 个模块、编译 10 段 README/API
示例；`test_run.py` 只收集 1 项，未执行。新增测试与限定范围的 Ruff 检查通过。
没有连接手机，多指在具体 Android 设备和应用中的注入效果仍需真机验证。

```bash
uv run --python 3.14 --locked setup.py build_ext --inplace
uv run --python 3.14 --locked pytest tests/test_action_series.py tests/test_device_session.py tests/test_lifecycle.py -q
uv run --python 3.14 --locked pytest tests/test_run.py --collect-only -q
```

## Free-threaded CPython

扩展在 free-threaded 构建下声明 `Py_MOD_GIL_NOT_USED`，已验证 **CPython 3.14t**。
普通 CPython 继续支持 3.10 及以上；本地默认环境仍为普通 3.14。
free-threaded 支持需要独立构建，普通 3.14 wheel 的 `cp314-cp314` 标签和
3.14t wheel 的 `cp314-cp314t` 标签不能混用。现有 setuptools 配置会使用
目标解释器的头文件和 ABI，无需手工定义 `Py_GIL_DISABLED` 或修改 CMake。

`Py_BEGIN_ALLOW_THREADS` / `Py_END_ALLOW_THREADS` 在两类构建中都必须保留：
普通构建释放 GIL，free-threaded 构建分离线程状态，使 GC 等全局协调不被
原生等待阻塞。该范围内只执行原生代码，Python 参数解析及返回值构造在范围外。
`bgra8_to_jpg()` 的编码现在也遵循这一规则。同一句柄仍由原生互斥锁串行化；
Python 设备对象和库初始化/配置继续在同一 asyncio 事件循环中管理。
本次不新增子解释器支持。

实现依据：[CPython 3.14 扩展适配指南](https://docs.python.org/3.14/howto/free-threading-extensions.html)、
[free-threaded 运行状态检查](https://docs.python.org/3.14/howto/free-threading-python.html)。

### 2026-09-08 验证结果

在本机 macOS arm64 上，普通 3.14.6 原地构建；3.10.20 和 3.14.6t 在独立
临时源树构建 sdist，再从 sdist 构建并安装各自 wheel。

| 验证 | 3.10.20 | 3.14.6 | 3.14.6t |
| --- | --- | --- | --- |
| 六个相关无设备测试文件 | 62 passed，1 skipped | 62 passed，1 skipped | 63 passed |
| 编码时分离线程状态、返回时恢复 | 通过 | 通过 | 通过 |
| 4 工作线程 + GC 并发压力 | 通过 | 通过 | 通过 |
| 导入和测试结束时 GIL 保持关闭 | 不适用 | 不适用 | 通过，无强制关闭参数 |
| 26 个源码/测试/存根编译，16 个模块导入 | 通过 | 通过 | 通过 |
| README/API 的 9 段 Python 示例编译 | 通过 | 通过 | 通过 |
| test_run.py 只收集、不执行 | 1 collected | 1 collected | 1 collected |

3.10 和 3.14t 的 sdist/wheel 已检查实际内容：原生源码、头文件、资源、扩展及
类型信息完整，排除 docs/、tests/、AGENTS.md；3.14t wheel 的扩展文件带
`cpython-314t` 标识。两个独立环境均从已安装 wheel 的 site-packages 导入。
新增 Python 测试通过 Ruff 检查，已有改动文件通过限定 E4/E7/E9/F 检查。

普通构建仅跳过“导入后保持 GIL 关闭”这一专属用例；硬件 JPEG 与模拟硬件
不可用的回退用例在三个环境均实际通过。新增线程状态探针通过 `setup.py`
构建测试专用扩展，在真实 C 包装进入真实编码器的边界检查线程状态，无计时阈值。
并发子进程共享不可变输入，执行 160 次 BGRA8 → JPEG 编码、48 次独立解码器
生命周期，覆盖原始帧、JPEG/ROI、重复关闭、Capsule 析构与 GC 并发；已有测试
另覆盖同一句柄读取、入队和关闭竞争。

两项关键回归测试先在修改前失败：3.14t 导入触发自动启用 GIL 的 RuntimeWarning，
BGRA8 编码入口仍附着 Python 线程状态。修改后均通过。
没有运行 ADB 真机操作、其他 Mac 型号或长期性能基准；测试不构成吞吐提升承诺。

普通开发环境验证命令：

```bash
uv run --python 3.14 --locked setup.py build_ext --inplace
uv run --python 3.14 --locked pytest tests/test_free_threading.py tests/test_native_lifecycle.py tests/test_jpg_direct.py tests/test_jpg.py tests/test_lifecycle.py tests/test_device_session.py -q
uv run --python 3.14 --locked pytest tests/test_run.py --collect-only -q
```

### 独立 3.14t 环境的可重复验证

先执行本文[独立环境中的 3.10 构建和测试](#独立环境中的-310-构建和测试)
里的临时目录创建及源码复制步骤，得到新的 `$compat_dir/source`。
随后使用以下命令代替该节的 3.10 构建命令；不要在正常工作区执行 3.14t sync：

```bash
uv --directory "$compat_dir/source" sync --python 3.14t --locked --no-install-project
uv build "$compat_dir/source" --python 3.14t --out-dir "$compat_dir/dist"
uv pip install --python "$compat_dir/source/.venv/bin/python" --no-deps "$compat_dir/dist/"*.whl
env -u PYTHON_GIL uv --directory "$compat_dir/source" run --no-sync --python 3.14t python -Werror::RuntimeWarning - <<'PY'
import sys
import sysconfig

assert sysconfig.get_config_var("Py_GIL_DISABLED") == 1
assert not sys._is_gil_enabled()
import adb_scr
import pytest

print(adb_scr.__file__)  # 应指向临时 site-packages
assert not sys._is_gil_enabled()
result = pytest.main([
    "tests/test_free_threading.py",
    "tests/test_native_lifecycle.py",
    "tests/test_jpg_direct.py",
    "tests/test_jpg.py",
    "tests/test_lifecycle.py",
    "tests/test_device_session.py",
    "-q", "-Werror::RuntimeWarning",
])
assert not sys._is_gil_enabled()
raise SystemExit(result)
PY
uv --directory "$compat_dir/source" run --no-sync --python 3.14t pytest tests/test_run.py --collect-only -q
```

不能只检查 `Py_GIL_DISABLED` 编译标志，也不能用 `PYTHON_GIL=0` 或 `-X gil=0`
强制关闭 GIL 来证明支持：未声明兼容的扩展可能在导入时自动启用 GIL。
本节命令清除强制环境变量、将 RuntimeWarning 当作错误，并检查导入前后和
测试后的实际状态。第三方扩展也可能改变该状态，调用方环境需独立核查。

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
        assert "adb_scr_mcp/agent_guide.md" in names
        assert any(name.endswith(".so") for name in names)
    else:
        with tarfile.open(path) as archive:
            raw_names = archive.getnames()
        names = [name.split("/", 1)[1] for name in raw_names if "/" in name]
        required = "src/adb_scr/res/scrcpy-server.bin"
        assert "native_code/macOS/src/adb_scr_media.c" in names
        assert "native_code/macOS/include/vtb_decoder.h" in names
        assert "src/adb_scr_mcp/agent_guide.md" in names
    forbidden = {"docs", "tests", "AGENTS.md", "CLAUDE.md"}
    assert not any(forbidden.intersection(PurePosixPath(name).parts) for name in names)
    assert required in names
    print(f"{path.name}: package contents verified")
PY
```

setuptools 可能提示 `no previously-included files found matching 'AGENTS.md'`，表示该文件原本就没有进入自动清单；仍需以最终归档检查为准。
