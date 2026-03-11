# adb_scr_py

使用 ADB 协议在电脑上控制 Android 手机，并能高效获取屏幕显示的内容。

## 特性

- 🚀 **高性能**：多媒体处理调用C扩展且利用硬件加速。可以轻松批量连接多台手机（至少5台以上）
- 📱 **多种连接方式**：支持 USB 和 TCP（网络）连接
- 🎯 **完整的控制功能**：点击、双击、长按、返回键、粘贴文本等
- 📸 **屏幕截图**：实时获取屏幕内容，支持自定义 JPG 质量
- 🎬 **应用管理**：启动和停止应用
- 🧵 **无 GIL 锁**：充分利用多核 CPU 性能

## 安装

### 系统要求

- Python >= 3.11
- macOS(x64或arm64都可以，但默认不提供x64的whl)
- 已安装 ADB 工具

### 安装方式

```bash
pip install adb_scr_py
```

或使用 uv：

```bash
uv add adb_scr_py
```

## 快速开始

### 基本使用

```python
import asyncio
from adb_scr import init_lib, deinit_lib, list_devices, AndroidDevice

async def main():
    # 初始化库（启动 ADB 守护进程等）
    adb_version, scrcpy_version = await init_lib()
    print(f"ADB 版本: {adb_version}, scrcpy 版本: {scrcpy_version}")
    
    # 列出已连接的设备
    devices = await list_devices()
    print(f"已连接设备: {devices}")
    
    # 连接设备
    device = AndroidDevice("192.168.1.100:5555", "tcp")
    if await device.connect():
        print("设备连接成功")
        
        # 获取屏幕尺寸
        await asyncio.sleep(0.5)  # 等待屏幕数据
        size = device.get_screen_size()
        if size:
            print(f"屏幕尺寸: {size[0]}x{size[1]}")
        
        # 获取屏幕截图
        jpg_data = await device.get_screenshot_jpg(quality=90)
        if jpg_data:
            with open("screenshot.jpg", "wb") as f:
                f.write(jpg_data)
            print("截图已保存")
        
        # 点击屏幕
        await device.click(500, 500)
        
        # 断开连接
        await device.disconnect()
    
    # 反初始化库
    await deinit_lib()

asyncio.run(main())
```

### USB 设备连接

```python
from adb_scr import AndroidDevice

# USB 设备使用设备的序列号
device = AndroidDevice("emulator-5554", "usb")
await device.connect()
```

### 网络设备连接

```python
from adb_scr import AndroidDevice

# 网络设备使用 IP:端口 格式
device = AndroidDevice("192.168.1.100:5555", "tcp")
await device.connect()
```

## API 文档

### 模块函数

#### `init_lib(adb_path: str | None = None) -> tuple[str, str]`

初始化库。会启动 ADB 守护进程等一系列准备工作。

**参数：**
- `adb_path`: ADB 可执行文件路径，如果为 None 则自动在系统PATH中找ADB

**返回：**
- `(adb_version, scrcpy_version)`: ADB 版本号和 scrcpy 版本号

**异常：**
- `AdbScrPyInitException`: 初始化失败

#### `deinit_lib() -> None`

反初始化库。会停止 ADB 守护进程并清理临时文件。

**！作死警告！**
- 千万不要在已经连接设备（调用了 `AndroidDevice.connect()` 方法）后调用这个函数，可能导致非常严重的后果！
- 因为一些奇怪的通信协议和操作系统的机制，这么干轻则导致python进程挂死，必须强行结束。
- 重则把手机内核搞崩掉导致需要强制重启。

#### `list_devices() -> list[str]`

获取当前连接的 ADB 设备列表。

**返回：**
- 设备列表，每个元素为设备地址或序列号，可以用于后续连接设备

#### `set_screen_record_fps(fps: int) -> None`

设置录屏帧率（10-60）。仅推荐无硬件解码器的平台进行设置，硬件解码的性能绰绰有余无需关注。

### AndroidDevice 类

#### 构造函数

```python
AndroidDevice(serial: str, connection_type: Literal["tcp", "usb"])
```

**参数：**
- `serial`: 设备序列号或 IP:端口
- `connection_type`: 连接类型，"tcp" 或 "usb"

#### 方法

| 方法 | 说明 |
|------|------|
| `connect() -> bool` | 连接设备 |
| `disconnect() -> None` | 断开设备连接 |
| `get_screen_size() -> tuple[int, int] \| None` | 获取屏幕尺寸 |
| `get_screenshot_jpg(quality: int = 75) -> bytes \| None` | 获取屏幕截图。由于技术问题，这个接口不能高频调用（建议不超过 5fps）|
| `click(x: int, y: int) -> None` | 单击屏幕 |
| `double_click(x: int, y: int) -> None` | 双击屏幕 |
| `long_press(x: int, y: int, duration_ms: int) -> None` | 长按屏幕 |
| `swipe(x1: int, y1: int, x2: int, y2: int) -> None` | 滑动屏幕，从 (x1, y1) 滑动到 (x2, y2) |
| `action_series(actions: list[GestureActionNode], check: bool = True) -> None` | 执行一系列手势操作（下方详见说明） |
| `press_back() -> None` | 按返回键 |
| `paste(text: str) -> None` | 粘贴文本 |
| `launch_app(package_name: str, activity_name: str) -> bool` | 启动应用 |
| `stop_app(package_name: str) -> bool` | 停止应用 |

*\*`get_screenshot_jpg`方法需要访问解码器，但python无内建的高效率DispatchQueue，导致不得不用asyncio.Lock保护操作。如果高频调用会导致整个解码流程阻塞！*

## 高级用法

### 自定义 ADB 路径

```python
await init_lib(adb_path="/path/to/adb")
```

### 设置录屏帧率

```python
from adb_scr import set_screen_record_fps

set_screen_record_fps(30)
```

### 应用管理

```python
# 启动应用
await device.launch_app("com.example.app", ".MainActivity")

# 停止应用
await device.stop_app("com.example.app")
```

### 滑动操作

```python
# 从 (100, 500) 滑动到 (100, 200)，实现向上滑动
await device.swipe(100, 500, 100, 200)
```

### 自定义手势序列

使用 `action_series` 方法可以执行复杂的手势操作，用于模拟复杂的手指操作，比如玩游戏或者拖拽等。

```python
from adb_scr.device.types import GestureActionNode, GestureAction

# 自定义手势序列
actions = [
    GestureActionNode(100, 500, GestureAction.DOWN, 10),   # 按下
    GestureActionNode(100, 400, GestureAction.MOVE, 20),   # 移动
    GestureActionNode(100, 300, GestureAction.MOVE, 20),   # 继续移动
    GestureActionNode(100, 200, GestureAction.UP, 0),      # 抬起
]
await device.action_series(actions)
```

**参数说明：**

1. **手势序列规则**：正常情况下，列表开头的操作必须是 `DOWN`，结尾的操作必须是 `UP`。方法默认会检查这个条件，不符合条件的会拒绝执行。

2. **duration_ms 参数**：每个节点的 `duration_ms` 表示执行该操作后等待的时间（毫秒）。
   - 每个节点（除了最后一个）的 `duration_ms` 不建议设为 0，否则操作间隔过短，手机可能无法响应
   - 最后一个节点的 `duration_ms` 建议填 0，除非你确定需要等待一段时间
   - `duration_ms` 必须在 0-10000 范围内（0-10秒）

3. **跳过检查**：这是个非常危险的操作，如果你确定自己在做什么非得要越过常规的操作，可以将 `check` 参数设为 `False` 跳过检查：
   ```python
   await device.action_series(actions, check=False)
   ```
   ⚠️ **警告**：假如 `DOWN` 之后没有 `UP`，会导致手机认为一直有手指按在屏幕上，后续的所有操作都可能会错乱

4. **坐标检查**：默认会检查坐标是否在屏幕范围内，超出范围的坐标会被拒绝执行。

## 注意事项

1. **并发安全**：`AndroidDevice` 的方法内部有锁保护，不会并行执行多个操作
2. **屏幕尺寸获取**：刚连接时屏幕数据可能还未送达，建议等待几百毫秒后再获取
3. **网络设备**：TCP 连接的设备需要确保手机和电脑在同一网络，且已开启 ADB 网络调试
4. **资源清理**：设备使用完毕后必须调用 `disconnect()` 清理资源，否则会导致内存泄漏。建议（不强制但建议）在软件退出前调用 `deinit_lib()` 反初始化库，释放所有资源。

## 技术实现

- 使用 [scrcpy](https://github.com/Genymobile/scrcpy) 服务端进行屏幕镜像
- C extension 使用 [libjpeg-turbo](https://libjpeg-turbo.org/) 实现高效的图像编码
- 完全异步实现，基于 Python asyncio

## 许可证

MIT License

## 致谢

- [scrcpy](https://github.com/Genymobile/scrcpy) - 屏幕镜像核心
- [libjpeg-turbo](https://libjpeg-turbo.org/) - 高性能 JPEG 编码
