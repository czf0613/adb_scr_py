# Python API 参考

本文与 Python docstring、原生 `.pyi` 使用统一术语：时间显式标注单位，异步方法写出 `async def`，区分返回值与设备执行确认。说明采用中文，docstring 使用 Google 风格 `Args`、`Returns`、`Raises`、`Notes`。省略不适用的小节。

所有公开入口从 `adb_scr` 导入。最低支持 Python 3.10，本地开发使用 3.14。架构、线程和资源所有权见 [architecture.md](architecture.md)。

## 模块函数

| 签名 | 返回与行为 |
| --- | --- |
| `async def init_lib(adb_path: str \| None = None) -> tuple[str, str]` | 启动共享 ADB daemon，返回 `(adb_version, scrcpy_version)`；重复调用返回已有信息 |
| `async def deinit_lib() -> None` | 停止共享 daemon、删除临时服务端文件；应在所有设备断开后调用 |
| `async def list_devices() -> list[str]` | 返回 ADB 列出的序列号；未初始化或命令返回失败时为空列表 |
| `def set_screen_record_fps(fps: int) -> None` | 设置后续会话的 `max_fps`，10–60 的整数；非法值记录警告并保持原值 |

`adb_path=None` 使用 PATH 中的 `adb`。初始化的显式路径检查和 daemon 非零退出会抛出 `exceptions.AdbScrPyInitException`；文件/子进程异常及 `asyncio.TimeoutError` 也可能传播。模块级锁仅协调同一事件循环内的初始化与反初始化。

`list_devices()` 的结果可能包含 offline/unauthorized 设备，不能视为可连接性保证。FPS 配置是进程级共享值，默认 30；在启动 scrcpy 时读取，不改变已有会话。[scrcpy 的帧率是随画面变化的](https://github.com/Genymobile/scrcpy/blob/v3.2/doc/video.md#frame-rate)，上限不是实际产帧速率。

## ConnectionOptions

```python
options = ConnectionOptions(
    connect_timeout=30.0,
    io_timeout=5.0,
    close_timeout=5.0,
    probe_interval=5.0,
    probe_failures=3,
)
```

这是不可变配置对象。时间单位均为秒，必须为有限正数；`probe_interval=None` 禁用探测。`probe_failures` 必须为正整数；非法配置抛出 `ValueError`。

| 参数 | 覆盖范围 |
| --- | --- |
| `connect_timeout` | 取得设备锁后的单次连接预算，含 TCP 设备连接、推送、启动、隧道及元数据；取消清理可能额外耗时 |
| `io_timeout` | 单条隧道建连与完整 ADB 握手、视频元数据、已开始的视频包、控制流 drain、存活探测；也用于 TCP 设备的 adb connect |
| `close_timeout` | 单条流关闭、服务端子进程回收、TCP transport 断开等待；不是整个原生清理的总时限 |
| `probe_interval` | 上次探测结束后到下一次探测的等待时间 |
| `probe_failures` | 连续探测失败阈值；成功一次将计数归零 |

存活探测执行 `adb -s SERIAL shell true`，不操作手机 UI。它检测 ADB transport 和设备 shell 是否可响应，不能发现所有 scrcpy 编码器停滞。普通视频包间隔和控制上行静默没有超时；视频包收到首字节后必须在 `io_timeout` 内收齐，半包超时会关闭整个会话，避免解析错位。

设备应用命令、文件推送及普通模块级 ADB 命令目前使用内部 10 秒超时；取消时终止并回收本次 ADB 子进程。底层回收等待还可能额外耗时，不应将这些数值解释为硬实时保证。

## AndroidDevice

```python
class AndroidDevice:
    def __init__(
        self,
        serial: str,
        connection_type: Literal["tcp", "usb"],
        *,
        options: ConnectionOptions | None = None,
    ) -> None: ...
```

构造不会连接。USB 使用序列号；TCP 使用已启用网络调试的 `IP:端口`。调用前先完成 `init_lib()`。同一实例应在同一事件循环内使用。

### 连接与状态

| 签名或属性 | 语义 |
| --- | --- |
| `async def connect() -> bool` | 两条流及有效视频元数据就绪后返回 True；失败/超时后清理并返回 False；取消先清理再传播 |
| `async def disconnect() -> None` | 幂等关闭，等待会话资源回收；没有会话时安全返回 |
| `is_connected: bool`（只读属性） | 读取当前流与进程状态，不进行主动网络探测，也不表示已产生首帧 |
| `last_disconnect_reason: str \| None` | 最近结束会话的首次断连原因；开始新连接时重置为 None |
| `async def wait_disconnected() -> str \| None` | 等待调用开始时的会话清理完成并返回原因；未连接过时立即返回 None |

`connect()` 可在失败或断开后再次调用。同一实例已有有效连接时不会重建会话。库不自动重连。`wait_disconnected()` 的取消不会导致设备断开；可用 `asyncio.wait_for()` 设置等待超时。已开始的等待保留旧会话结果，不会被后续重连覆盖。

EOF、接收或写入失败、服务端子进程退出、探测连续失败统一进入清理路径。关闭异常会记录日志；等待完成表示已执行资源回收，不承诺外部设备或系统调用永不失败。清理顺序与原生等待边界见 [control-flow.md](control-flow.md)。

### 尺寸与截图

```python
def get_screen_size() -> tuple[int, int] | None: ...
async def get_screenshot_jpg(quality: int = 75) -> bytes | None: ...
```

尺寸以 `(width, height)` 返回，单位为像素；会话不可用返回 `None`。坐标原点位于左上角，范围为 `0 <= x < width`、`0 <= y < height`。屏幕旋转会在新配置到达后更新尺寸。

JPEG 质量要求为 1–100 的整数。截图返回独立 JPEG `bytes`；会话不可用、尚无帧或编码失败时返回 `None`。连接成功不等于首帧就绪，调用方应在截止时间内重试。截图可能重复同一画面；没有“必须低于 5 FPS”的固定限制，也没有固定性能保证。

内部原始帧接口返回 `(width, height, bgra_bytes)`，像素按 B、G、R、A 排列，长度为 `width * height * 4`。它仍位于内部控制句柄/解码器层，没有新增顶层 BGRA8 API。NumPy/OpenCV 用法见 [BGRA8 消费说明](control-flow.md#bgra8-到-opencv-的消费方式)。

### 应用和控制

以下均为异步方法：

| 签名（均带 `async def`） | 参数和行为 |
| --- | --- |
| `launch_app(package_name: str, activity_name: str) -> bool` | 通过 ADB 启动应用，成功退出返回 True；不要求先连接视频会话 |
| `stop_app(package_name: str) -> bool` | 通过 ADB force-stop 应用，成功退出返回 True |
| `click(x: int, y: int) -> None` | 单击像素坐标；内部采用短暂长按 |
| `double_click(x: int, y: int) -> None` | 两次点击，中间有随机等待 |
| `long_press(x: int, y: int, duration_ms: int) -> None` | 按住指定毫秒数后抬起；持续时间应为非负整数 |
| `swipe(x1: int, y1: int, x2: int, y2: int) -> None` | 通过插值节点滑动到目标像素坐标 |
| `press_back() -> None` | 发送返回键按下与抬起 |
| `paste(text: str) -> None` | 发送 UTF-8 文本；设备焦点应位于目标输入框 |
| `action_series(actions: list[GestureActionNode], check: bool = True) -> None` | 按顺序发送手势节点 |

无连接、坐标不合法或手势检查失败时，控制方法记录日志并返回。`None` 不表示手机已经处理了动作；`drain()` 只处理本地发送背压。断连后无法保证最后的抬起消息到达，调用方不应依赖断连后的后续动作。方法内的锁保护指定操作序列；整个 `double_click()` 等组合调用并非全部持有同一把锁。

### 手势类型

```python
from adb_scr import GestureAction, GestureActionNode

# GestureAction.DOWN = 0; UP = 1; MOVE = 2
node = GestureActionNode(x=100, y=200, action=GestureAction.DOWN, duration_ms=50)
```

`GestureActionNode` 是数据类：`x: int`、`y: int`、`action: GestureAction`、`duration_ms: int = 50`。持续时间表示该节点发送后的等待，最后一个节点也适用；执行时包含随机扰动，不是精确计时。

```python
await device.action_series([
    GestureActionNode(100, 500, GestureAction.DOWN, 10),
    GestureActionNode(100, 400, GestureAction.MOVE, 20),
    GestureActionNode(100, 300, GestureAction.UP, 0),
])
```

调用方应以 DOWN 开始、UP 结束。当前 `check=True` 检查至少两个节点、持续时间 0–10000 毫秒以及相邻状态转换，**并没有额外验证最终状态必须是 UP**。`check=False` 跳过这些检查，但仍检查坐标。此文档整理没有修改这项既有手势行为。

## 原生 API 与异常

`adb_scr.media_ext._adb_scr_media` 属于底层接口。完整类型与参数约束见 [`_adb_scr_media.pyi`](../src/adb_scr/media_ext/_adb_scr_media.pyi)。输入须符合存根约定；类型注解不代替运行时验证。

原生句柄通过容器和互斥锁协调入队、读取及销毁；重复关闭安全，关闭后读取返回 None，入队返回 False。返回的 BGRA8 bytes 不引用 CVPixelBuffer。仍应显式关闭句柄，Capsule 析构仅作兜底。扩展没有声明 free-threaded Python 支持。

创建、取帧和销毁须从异步路径调度到工作线程。取消协程不停止在途原生操作；库会等待相应操作完成，避免释放仍被工作线程使用的资源。VideoToolbox/GCD 销毁没有强制超时。

公开异常位于 `adb_scr.exceptions`：`AdbScrPyException`、`AdbScrPyInitException`、`AdbScrPyH264DecoderException`。设备 `connect()` 将普通连接异常转换成 False，`asyncio.CancelledError` 在清理后传播。
