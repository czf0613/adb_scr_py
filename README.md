# adb_scr_py

通过 ADB 和 scrcpy 控制 Android 设备，并使用 macOS VideoToolbox 解码屏幕视频。Python 导入名为 `adb_scr`。

支持 USB 和网络调试连接、点击/滑动/长按/粘贴、单指及多指手势、应用启动与停止、按需 JPEG 截图，以及 H.264/AAC MP4 屏幕录制。内部保留 BGRA8 原始帧通路，供 NumPy/OpenCV 消费；截图和录制都直接使用原生解码帧。

## 安装

- Python 3.10 或更高版本；本地开发使用普通 3.14。支持 free-threaded CPython，已验证 3.14t；需要对应 ABI 的构建，详见 [Python 兼容性](docs/python-compatibility.md)。
- macOS，使用 VideoToolbox 硬件编解码和部分 Metal 特性，**推荐 Apple silicon**。不保证 Intel Mac 能正常使用；源码仍允许构建 x86_64，具体 wheel 可用性以发布文件为准。
- 已安装 ADB，设备已授权 USB 调试，或已具备 ADB 网络调试条件。

```bash
uv add adb_scr_py
```

## 快速开始

将示例序列号替换为自己的设备。网络设备使用 `AndroidDevice("192.168.1.100:5555", "tcp")`。

```python
import asyncio
from pathlib import Path

from adb_scr import AndroidDevice, deinit_lib, init_lib, set_screen_record_fps


async def main() -> None:
    await init_lib()
    device = AndroidDevice("YOUR_DEVICE_SERIAL", "usb")
    try:
        set_screen_record_fps(30)  # 后续启动会话的上限，不改变正在运行的会话
        if not await device.connect():
            print(device.last_disconnect_reason)
            return
        print(device.get_screen_size())  # connect 已等待有效的视频元数据

        # 解码首帧可能晚于连接成功；使用截止时间，而不是固定 sleep 猜测。
        deadline = asyncio.get_running_loop().time() + 5
        while device.is_connected:
            jpg = await device.get_screenshot_jpg(quality=90)
            if jpg is not None:
                await asyncio.to_thread(Path("screenshot.jpg").write_bytes, jpg)
                break
            if asyncio.get_running_loop().time() >= deadline:
                print("等待首帧超时")
                break
            await asyncio.sleep(0.05)
    finally:
        try:
            await device.disconnect()
        finally:
            await deinit_lib()


asyncio.run(main())
```

## 截图、裁剪与缩放

`get_screenshot_jpg()` 支持可选的质量、缩放比例和原图裁剪区域：

```python
jpeg = await device.get_screenshot_jpg()  # quality=75, scale=1.0, roi=None
jpeg = await device.get_screenshot_jpg(
    quality=85, scale=0.5, roi=(100, 200, 600, 400)
)  # 裁剪原图区域，再缩小为 300 × 200
```

ROI 使用原图左上角坐标 `(x, y, width, height)`，必须完全位于图内；None 表示整图。先裁剪再缩放，比例可大于 1。三个参数都有默认值，无参数或只传质量的现有调用无需修改；参数范围、取整和异常见 [API 文档](docs/api.md#尺寸与截图)。

## 连接与断连

```python
from adb_scr import AndroidDevice, ConnectionOptions

options = ConnectionOptions(
    connect_timeout=30,
    io_timeout=5,
    close_timeout=5,
    probe_interval=5,
    probe_failures=3,
)
device = AndroidDevice("YOUR_DEVICE_SERIAL", "usb", options=options)
```

EOF、接收/发送失败、服务端进程退出或设备存活探测连续失败会触发会话清理。`await device.wait_disconnected()` 等待清理完成并返回原因；`device.is_connected` 可读取当前状态。库不自动重连，调用方可在设备恢复后再次 `await device.connect()`。

静态画面可能没有新视频帧，控制上行也可能长期无数据，因此不设置普通空闲读取超时。默认每 5 秒通过设备端 `shell true` 探测 transport，连续 3 次失败后断开；`probe_interval=None` 可禁用。探测不能证明编码器持续产帧，也不是精确的断连检测时限。

## 屏幕录制

连接成功且首帧已经解码后：

```python
await device.start_recording("capture.mp4", quality=0.75)  # 默认 0.75，可传 0.0–1.0
try:
    await asyncio.sleep(5)  # 此期间仍可截图或操作设备
finally:
    await device.stop_recording()  # 等待 MP4 封装完成后再读取文件
```

开始时使用缓存画面立即生成关键帧，停止时补足静止画面的持续时间；录制期间没有新视频包也能生成有效文件。视频使用 VideoToolbox 硬件 H.264 编码，通过 `quality` 控制画质与体积的取舍，默认 `0.75`，不指定固定码率；音频直接封装手机回传的 AAC，不重新编码。录制要求可用的 Metal 设备，输出尺寸固定为首帧尺寸，旋转后由 GPU 直接缩放 NV12 平面并居中留黑。`quality` 越高通常画质越好、文件越大；它不是体积比例，`1.0` 不保证 H.264 无损，实际体积取决于画面和硬件。不覆盖已有文件，也不自动创建父目录。

连接时自动读取 Android API 级别并开启支持的音源：Android 13+ 使用 `playback + audio_dup` 保留手机播放声音；Android 11–12L 使用 `output`，采集期间手机静音（从连接开始，即使尚未录制）；Android 11 启动时还需要解锁屏幕。Android 10 及以下不启用音频。服务端明确禁用音频时保留视频连接，开始录制会记录警告并生成纯视频文件。应用可以限制音频采集，系统行为见 [scrcpy 音频说明](https://github.com/Genymobile/scrcpy/blob/v3.2/doc/audio.md)。

每台设备同时最多一份录制，重复开始会报错；重复停止安全。断连会自动结束录制，异步写入错误可通过 `stop_recording()` 获取。接口异常与时间精度见 [录制 API](docs/api.md#屏幕录制)。

## MCP 服务

可选的 `adb_scr_mcp` 包提供 FastAPI + Streamable HTTP 服务，lifespan 自动初始化
库，并在 Ctrl+C/SIGTERM 时等待设备、录制和 ADB 清理完成。启动后由客户端显式连接设备。

```bash
uv run --python 3.14 --locked --extra mcp adb-scr-mcp --port 8000
```

客户端地址为 `http://127.0.0.1:8000/mcp`。提供设备枚举/连接、JPEG 截图、触控、
应用控制及 MP4 录制工具。安装、参数和退出流程见 [MCP 服务文档](docs/mcp.md)。

## API 索引

| 入口 | 用途 |
| --- | --- |
| `await init_lib(adb_path=None)` | 初始化共享 ADB daemon |
| `await list_devices()` | 获取 ADB 列出的序列号 |
| `set_screen_record_fps(fps)` | 设置后续会话的帧率上限 |
| `ConnectionOptions(...)` | 配置连接、I/O、关闭及存活探测 |
| `AndroidDevice(...)` | 设备会话、截图及控制 API |
| `await device.start_recording(output_file, quality=0.75)` / `await device.stop_recording()` | 开始录制及等待 MP4 文件完成 |
| `GestureAction` / `GestureActionNode` | 通过 `pointer_id` 区分手指的单指/多指手势序列 |
| `await deinit_lib()` | 所有设备关闭后停止共享 daemon |

完整签名、参数单位、失败行为和示例见 [API 参考](https://github.com/czf0613/adb_scr_py/blob/master/docs/api.md)（仓库内见 [docs/api.md](docs/api.md)）。原生 API 的类型及说明随包内 `.pyi` 提供。

## 使用边界

- 一个设备实例应在同一 asyncio 事件循环内使用。设备锁和解码器锁保护各自操作；并非所有公开方法共用一把锁，也不提供跨事件循环共享保证。
- `connect()` 成功表示流和尺寸就绪，截图仍可能因为首帧未到而返回 `None`。
- 控制方法返回不代表手机 UI 已执行动作；断连时不能保证手势抬起消息送达。
- 设备用完后显式 `await disconnect()`，再 `await deinit_lib()`。停止全局 daemon 会影响其他 ADB 客户端。
- 网络操作超时会开始取消与清理，实际返回可能稍晚。原生销毁等待硬件和队列完成，没有强制释放在用内存的超时。
- 不提供固定截图吞吐保证或历史帧回放。录制从调用时的缓存画面开始，不包含调用前的历史视频。

## 开发文档

[架构](docs/architecture.md) · [控制流程与 BGRA8](docs/control-flow.md) · [Python 兼容性](docs/python-compatibility.md)

仓库开发文档与测试不进入 sdist/wheel。项目通过 setuptools 构建原生扩展；CMake 仅用于 IDE 索引。

## 许可证与依赖

MIT License。屏幕传输使用 [scrcpy](https://github.com/Genymobile/scrcpy)；媒体处理使用 Apple VideoToolbox、AVFoundation/CoreMedia、AudioToolbox、Accelerate/vImage、ImageIO/CoreGraphics 和 Core Image/Metal。
