# Windows 原生媒体后端

首版面向近年的 Windows 10/11 x64 和 Windows on ARM64。使用系统
Media Foundation、D3D11、WIC，不依赖 FFmpeg、显卡厂商 SDK 或 OpenMP。
扩展由 `setup.py` 编译为当前 Python ABI/架构的 `.pyd`，不用 CMake。
本机测试是 Windows x64；ARM64 构建和运行需由原生 ARM64 CI/实机验证。

## 构建与无设备测试

安装 Visual Studio C++ 桌面工具及 Windows SDK，使用与 Python 匹配的
x64/ARM64 工具链。系统需具备 Media Foundation 和系统 H.264/AAC/WIC
组件；缺少媒体组件的 Windows N 安装需要先安装 Media Feature Pack。

```powershell
uv sync --python 3.14 --locked --extra mcp --no-install-project
uv run --python 3.14 --locked setup.py build_ext --inplace
uv run --python 3.14 --locked pytest tests/test_windows_media.py tests/test_windows_media_errors.py -q
```

测试通过额外的测试扩展调用 Windows 编码器生成 H.264/AAC，使用 WIC、
SourceReader 回读 JPEG/MP4。没有 FFmpeg，也不会启动 ADB 或访问手机。
`ADB_SCR_TESTING` / `ADB_SCR_TEST_DISABLE_D3D` 只用于测试构建，不是用户选项。
`test_run.py` 仅收集，执行真机测试前必须由用户连接并授权。

真机测试使用手动脚本，不会被 pytest 自动执行：

```powershell
uv run --python 3.14 --locked tests/windows_device_smoke.py --duration 60 --output build/phone-smoke-01
```

要求恰好连接一台已授权手机，输出目录必须尚不存在。若当前进程 PATH 尚未
刷新，可加 `--adb "C:\Program Files\android-platform-tools\adb.exe"`。
脚本采集当前屏幕和音频、不注入触摸操作；产物可能含私人内容，保留在本地
忽略目录。检查 BGRA8/ROI、MP4 全量系统解码、AAC 包一致性、重复 stop、
取消错误等待、重连及断开自动收尾；最后执行库的断连和反初始化清理。

完整的 Windows 无设备验证入口为 `.github/scripts/check_windows.py`：
在独立源树中构建 sdist/wheel、安装 wheel 后，用相应解释器运行该脚本。
它检查源码语法、包内容、已安装模块、媒体测试和 GIL 状态。
Windows 3.14t 使用 `--core-only`，不安装可选 MCP extra：其 pywin32 312
依赖目前没有 cp314t wheel。此限制不影响已验证的核心媒体库。
`.github/workflows/windows.yml` 覆盖 x64 的 3.10/3.14/3.14t 和 ARM64 的
3.14/3.14t。ARM64 矩阵使用原生 `windows-11-arm`，不会把 x64 仿真当作
原生 ARM64 验证。现有正式发布工作流仍为 macOS；Windows 暂提供源码构建
和独立 CI 产物，尚未发布 Windows wheel。

本机已完成 Windows 11 x64 / Android 14 竖屏真机测试：最终构建录制约
60.2 秒，1715 个视频样本全部解码，2822 个 AAC 包直通一致且可解码；
BGRA8/JPEG 同时取帧、重连与断开自动收尾通过。最终安装包的无设备回归为
Python 3.10/3.14 各 96 项、3.14t 核心库 97 项；详见
[完整验证记录](python-compatibility.md)。后续已补充下述旋转和控制验证；
ARM64、Windows 10 仍未实机验证。

### 2026-09-16 真机旋转与控制补测

在同一 Windows 11 x64 / Android 14 手机上，通过 B 站进入/退出全屏完成
四次 1080×2400 ↔ 2400×1080 切换。分别从竖屏、横屏开始录制：

| 初始固定画布 | MP4 时长 | 视频样本 | AAC 包 |
| --- | --- | --- | --- |
| 1080×2400 | 205.384300 秒 | 6141 | 9627 |
| 2400×1080 | 179.145167 秒 | 5370 | 8397 |

两段文件全部经 Windows 系统解码器回读，AAC 与实际提交的源包逐包一致。
旋转后 MP4 画布仍保持初始尺寸，末帧回读确认画面等比居中、相应上下/左右
黑边为 BGRA `(0,0,0,255)`。会话期间检查 2958 个紧密 BGRA 帧，未出现
媒体错误；暂停播放形成约一分钟静止画面的录制也正常结束。

| 公开控制接口 | 手机端观察结果 |
| --- | --- |
| `click` / `press_back` | 显示播放器控件、进入全屏、返回竖屏 |
| `double_click` | 暂停并恢复播放 |
| `long_press` | 3.5 秒按住期间出现“倍速中”，释放后消失 |
| `swipe` | 页面上滑、下滑有明确滚动响应 |
| `action_series` | 两个不同 pointer_id 同时按下，Android 指针叠层显示两个触点；测试后恢复原叠层设置 |
| `paste` | 系统设置搜索框显示中文、英文、空格和数字；随后清空测试文字 |
| `launch_app` / `stop_app` | 打开系统设置并停止，返回 B 站播放 |

修复了 `action_series()` 等待下界的 `min`/`max` 错误。手势/生命周期
定向回归在 3.10、3.14、3.14t 各通过 74 项，Windows CI 已纳入手势测试。
粘贴测试使用临时脚本关闭 clipboard_autosync 以尝试读取原剪贴板，读取
未收到应答，未建立原内容备份；测试后以空文本清除测试剪贴板内容。
生产连接参数未改变，手机最终回到 B 站竖屏播放，测试会话已断开。

这些结果覆盖现有基础控制 API，不代表 scrcpy 控制协议的全部功能。
当前未公开通用按键（Home、音量等）、鼠标滚轮、屏幕电源、通知面板或
剪贴板读取/确认接口；触控发送成功也没有手机 UI 执行确认。多指真机验证
为两个触点，未覆盖十指、旋转中持续按住、所有取消时序或其他手机 ROM。
本轮 MP4、截图和 JSON 记录位于本地忽略目录 `build/phone-control-01/`，
补充控制记录位于 `build/phone-control-02/` 至 `build/phone-control-04/`。

## 媒体路径

| 功能 | 实现 |
| --- | --- |
| H.264 解码 | Microsoft H.264 Decoder MFT，Annex B 完整 access unit |
| 最新帧 | 持有 NV12 `IMFSample`、媒体类型及 D3D/MF 生命周期引用 |
| BGRA8 | VideoProcessorMFT 色彩转换，直接打包进最终 Python bytes |
| JPEG | 系统视频处理器转 BGRA，WIC 精确 ROI、缩放、JPEG 编码 |
| H.264 录制 | SinkWriter 选择系统 MFT，质量 VBR，禁用 B 帧提示 |
| AAC / MP4 | 原始 AAC-LC 包直通系统 MP4 sink，无音频解码/重编码 |

解码尝试提供 D3D11 device manager，录制允许系统硬件 transforms。
初始化时 D3D 创建/协商失败可走系统内存路径。不查询最终硬件使用状态，
不以硬件加速作为成功条件，也没有厂商识别逻辑。硬件提示不等于硬件保证。
JPEG 使用系统 WIC 编码器。质量值不承诺与 macOS 相同的画质或文件体积。

H.264 解码设置低延迟，并提供 NAL 长度；含帧重排的码流仍可能需要缓冲。
scrcpy 的无 B 帧输入及测试生成器的低延迟流支持单帧输出。测试编码器必须
在协商媒体类型之前配置低延迟/禁用 B 帧，否则 SPS 仍可能要求重排。

BGRA8 固定为自顶向下、`stride = width * 4`、`len = width * height * 4`，
通道顺序 B/G/R/A，A 为 255。系统内存 RGB 输出显式指定正步长，防止倒置。
`IMF2DBuffer2` 路径检查实际 stride/地址跨度，以 `MFCopyImage` 去除 padding；
该系统函数已有优化实现，首版不加入自定义 SIMD/OMP。JPEG 不创建 Python
BGRA 中间对象，但当前仍有原生 BGRA 临时缓冲。

## 线程、容量和失败

每个解码器/录制器拥有一个 MTA 工作线程，在线程内初始化 COM/MF 并串行
驱动 MFT/SinkWriter。状态句柄互斥；图像转换保留独立快照，关闭解码器后
在途读取仍可安全完成。快照使 MF 生命周期延长到最后一次读取结束。
异步 Python 调用继续用 `asyncio.to_thread()` 并保留取消期间的所有权。

| 有界资源 | 上限（包括在途任务） |
| --- | --- |
| 解码提交队列 | 32 项 / 64 MiB 压缩数据 |
| 录制工作队列 | 256 项 / 128 MiB 估算媒体数据 |
| 录制待编码视频 | 八帧独立 NV12；另持有一个用于补时长的 pending 帧 |
| 录制待写 AAC | 256 包 / 4 MiB，单包最多 1 MiB |
| SinkWriter 已提交但未处理的视频 | 32 样本 / 128 MiB（按原始 NV12 大小计） |
| SinkWriter 已提交但未处理的 AAC | 256 包 / 4 MiB |
| 最旧媒体任务延迟 | 5 秒，提交或状态检查时检测 |

容量/延迟超限保存首个 `MediaPipelineOverloadedError`，拒绝新媒体任务，
取消未开始的普通任务，仅允许清理任务。不会通过丢帧继续运行失败的管线。
MFT 的正常 `NEED_MORE_INPUT` / `NOTACCEPTING` 按协议处理，不直接视为过载。
录制提交在解码 MTA 线程先复制为独立 NV12，随即释放对解码表面的占用；
编码队列积压不会耗尽解码表面池。八帧容量允许短暂突发，满时仍立即失败。
这一步也受解码队列的容量/延迟限制，复制失败保存为录制错误。

关闭 SinkWriter 默认限流：静止视频搭配连续音频时，其阻塞式限流会阻塞
同一工作线程上的后续视频。使用 `GetStatistics` 的已处理样本计数回收
提交账目，同时检查内部排队字节数；提交前限制样本数/字节数，最旧未处理
样本超过 5 秒报错。100 ms 监控最多挂一个检查任务，所有 COM 调用仍在
录制工作线程内执行。此统计只用于积压检测，不查询是否使用硬件加速。

Windows 会话每 100 ms 独立检查原生失败，无需等待手机下一包：

- 解码失败关闭会话，后续截图和 `await device.wait_media_error()` 抛原始错误。
- 录制失败结束该录制，保留视频/控制会话；`wait_media_error()` 和
  `stop_recording()` 都可取得失败。录制状态检查与 start/stop 共用锁。
- 正常断连让 `wait_media_error()` 返回 None；取消等待不关闭会话。
- 错误保留到所属对象结束/新会话建立。系统调用/HRESULT 错误为 RuntimeError。

5 秒是错误判定预算，不能强制终止正在执行的系统 codec/驱动调用。
清理仍需等在途系统调用返回，避免释放正在使用的内存。

## 录制时间轴与首版限制

首帧尺寸固定为偶数 NV12 画布；旋转后由系统视频处理器等比缩放、居中。
写入前将黑边显式规范为 NV12 Y=16、UV=128，避免系统处理路径的边框色差。
开始时提交 1 毫秒零时刻画面，之后保留一帧直到下一 PTS 或 stop，补齐
静止画面的时长。最短录制 1 毫秒；视频时间戳按系统 MP4 timescale 量化。
SinkWriter 在 start 返回前接收首帧，最终文件只在 stop 成功后可用。

连续音频下，Windows MP4 sink 需要视频时间轴持续前进，否则会停止接收
更多 AAC。音频领先 pending 视频满 1 秒时，用同一画面补一段视频，提交到
当前音频 PTS 之前 500 ms；这 500 ms 用于等待在途视频，不改变 AAC 的时间轴。
晚到视频若落在已提交区间，录制抛 `MediaPipelineOverloadedError`，不能静默
丢弃。静止录制因此可能包含重复画面样本；停止时间仍由 stop 的时钟边界决定。

AAC 仅接受单/双声道、每包 1024 样本的 AAC-LC。起点之前的包忽略，
首包偏移保留；一个包以内的累计时钟抖动按采样时钟量化。超过一个包的
累计间隙或重叠、非递增时间戳均使录制失败；不把这些间隙静默压平。
首版没有实现 macOS 的 MP4 空编辑修复流程，AAC 尾部按完整包处理。
失败的 MP4 仅尽力 finalize，可能不完整，不能作为成功录制交付。

路径采用 Unicode 独占创建，不覆盖已有文件；创建后若系统初始化/编码失败，
可能留下本次拥有的部分文件。重复 stop 保留首次错误。

## 系统文档

- [H.264 Decoder MFT](https://learn.microsoft.com/en-us/windows/win32/medfound/h-264-video-decoder)
- [低延迟属性的 decoder VT_UI4 特例](https://learn.microsoft.com/en-us/windows/win32/medfound/codecapi-avlowlatencymode)
- [NAL 长度和完整帧标记](https://learn.microsoft.com/en-us/windows/win32/medfound/mf-nalu-length-set)
- [MFCopyImage](https://learn.microsoft.com/en-us/windows/win32/api/mfapi/nf-mfapi-mfcopyimage)
- [SinkWriter 输入及编码参数](https://learn.microsoft.com/en-us/windows/win32/api/mfreadwrite/nf-mfreadwrite-imfsinkwriter-setinputmediatype)
- [SinkWriter 默认阻塞限流](https://learn.microsoft.com/en-us/windows/win32/medfound/mf-sink-writer-disable-throttling)
- [SinkWriter 样本和字节统计](https://learn.microsoft.com/en-us/windows/win32/api/mfreadwrite/ns-mfreadwrite-mf_sink_writer_statistics)
- [GitHub 原生 ARM64 runner](https://docs.github.com/en/actions/reference/runners/github-hosted-runners)
