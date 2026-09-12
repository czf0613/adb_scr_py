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
| `connect_timeout` | 取得设备锁后的单次连接预算，含 TCP 设备连接、API 级别查询、推送、启动、隧道及媒体配置；取消清理可能额外耗时 |
| `io_timeout` | 单条隧道建连与完整 ADB 握手、视频元数据、音频头/初始 AAC 配置、已开始的媒体包、控制流 drain、API 级别查询及存活探测；也用于 TCP 设备的 adb connect |
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
| `async def connect() -> bool` | 视频/控制流和有效视频元数据就绪后返回 True；启用音频时还等待音频流和初始 AAC 配置或明确的禁用状态；失败/超时后清理并返回 False；取消先清理再传播 |
| `async def disconnect() -> None` | 幂等关闭，等待会话资源回收；没有会话时安全返回 |
| `is_connected: bool`（只读属性） | 读取当前流与进程状态，不进行主动网络探测，也不表示已产生首帧 |
| `last_disconnect_reason: str \| None` | 最近结束会话的首次断连原因；开始新连接时重置为 None |
| `async def wait_disconnected() -> str \| None` | 等待调用开始时的会话清理完成并返回原因；未连接过时立即返回 None |

`connect()` 可在失败或断开后再次调用。同一实例已有有效连接时不会重建会话。库不自动重连。`wait_disconnected()` 的取消不会导致设备断开；可用 `asyncio.wait_for()` 设置等待超时。已开始的等待保留旧会话结果，不会被后续重连覆盖。

EOF、接收或写入失败、服务端子进程退出、探测连续失败统一进入清理路径。关闭异常会记录日志；等待完成表示已执行资源回收，不承诺外部设备或系统调用永不失败。清理顺序与原生等待边界见 [control-flow.md](control-flow.md)。

### 尺寸与截图

```python
def get_screen_size() -> tuple[int, int] | None: ...
async def get_screenshot_jpg(
    quality: int = 75,
    scale: float = 1.0,
    roi: tuple[int, int, int, int] | None = None,
) -> bytes | None: ...
```

尺寸以 `(width, height)` 返回，单位为像素；会话不可用返回 `None`。坐标原点位于左上角，范围为 `0 <= x < width`、`0 <= y < height`。屏幕旋转会在新配置到达后更新尺寸。

JPEG 质量要求为 1–100 的整数。截图返回独立 JPEG `bytes`；会话不可用、尚无帧或编码失败时返回 `None`。连接成功不等于首帧就绪，调用方应在截止时间内重试。截图可能重复同一画面；没有“必须低于 5 FPS”的固定限制，也没有固定性能保证。

`get_screenshot_jpg()` 支持可选的缩放比例和原图 ROI。三个参数都有默认值；现有 `get_screenshot_jpg()`、`get_screenshot_jpg(90)` 和 `get_screenshot_jpg(quality=90)` 调用继续有效。相同质量值无需对应不同编码器下的相同画质。

| 参数 | 默认值 | 约束与语义 |
| --- | --- | --- |
| `quality` | `75` | 1–100 的整数 |
| `scale` | `1.0` | 有限正数；小于 1 缩小、大于 1 放大 |
| `roi` | `None` | `(x, y, width, height)` 整数元组；使用原图像素坐标，原点在左上角；None 输出整图 |

**先裁剪，再缩放**。ROI 对应 `image[y:y+height, x:x+width]`，必须完全位于当前原生帧内，不自动截断越界区域。允许奇数坐标和尺寸。输出宽高分别按 `max(1, floor(裁剪尺寸 * scale + 0.5))` 计算，上限为 65535 像素；具体系统编码器也可能因资源或尺寸限制失败。ROI 的边界以实际取得的帧为准，因此不会使用旋转前缓存的宽高检查。

参数类型错误抛 `TypeError`（不接受 bool 作为数值）；质量、比例、ROI、输出尺寸越界抛 `ValueError`；整数超出原生数值类型范围抛 `OverflowError`。会话不可用时直接返回 None；静态参数检查由原生入口执行，ROI 边界和输出尺寸检查需要已有解码帧。取消等待会先等待原生编码结束，再传播 `CancelledError`。

```python
jpeg = await device.get_screenshot_jpg()  # 整图，75，原比例
jpeg = await device.get_screenshot_jpg(scale=0.5)  # 整图缩小一半
jpeg = await device.get_screenshot_jpg(
    quality=85, scale=2.0, roi=(100, 200, 300, 150)
)  # 裁剪原图区域，再放大成 600 × 300
```

内部原始帧接口返回 `(width, height, bgra_bytes)`，像素按 B、G、R、A 排列，长度为 `width * height * 4`。它仍位于内部控制句柄/解码器层，没有新增顶层 BGRA8 API。NumPy/OpenCV 用法见 [BGRA8 消费说明](control-flow.md#bgra8-到-opencv-的消费方式)。

### 屏幕录制

```python
async def start_recording(output_file: str, quality: float = 0.75) -> None: ...
async def stop_recording() -> None: ...
```

这两个方法属于 `AndroidDevice`。`start_recording()` 成功表示已创建录制并生成首个 H.264 关键帧；首帧取自当前已解码画面，无需等待手机产生新帧或新 I 帧。没有已解码画面时抛出 `RuntimeError`，连接成功本身不保证首帧已到。每个设备只允许一个活动录制，重复开始抛出 `RuntimeError`。

`output_file` 必须为非空、无 NUL 的字符串。不根据后缀猜容器，输出始终为 MP4；不覆盖已有文件，不创建父目录。路径类型错误抛出 `TypeError`，空路径/NUL 抛出 `ValueError`；文件和编码器错误抛出 `OSError` 或 `RuntimeError`。`stop_recording()` 返回后文件才完成封装，运行期间不保证可供播放器打开。

`quality` 默认为 `0.75`，接受 `0.0–1.0` 范围内的有限 int/float，不接受 bool、字符串或 None。类型错误抛 `TypeError`，越界或非有限数抛 `ValueError`，参数验证在创建文件之前完成。参数传给 [VideoToolbox Quality](https://developer.apple.com/documentation/videotoolbox/kvtcompressionpropertykey_quality)，越高通常画质越好、文件越大；不是固定码率、文件体积比例或跨硬件一致的画质指标，`1.0` 不保证 H.264 无损。省略参数的旧调用继续可用，但改用明确的 `0.75` 质量。

视频从 NV12 直接经 VideoToolbox 硬件编码为 H.264，编码 Profile 和关键帧间隔采用系统默认设置，不指定固定码率，不启用牺牲画质的速度优先提示；保留实时编码、预期帧率和禁止帧重排配置。实际码率和文件大小随系统、尺寸和画面内容变化。输出画布使用首帧尺寸；旋转后的 BT.709/未标记画面由 Metal 直接对 NV12 平面做双线性等比缩放、居中留黑；显式非 BT.709 输入保留基于 Metal 的色彩转换。录制使用部分 Metal 特性，推荐 Apple silicon，不保证 Intel Mac 能正常使用。开始时要求可用的 Metal 设备并完成 GPU pipeline 准备；Metal 初始化失败抛出 `RuntimeError`。编码队列有界，过载可能丢弃中间视频帧；不影响原始 BGRA8/JPEG 获取路径。停止会补齐缓存尾帧的持续时间，即使整段录制没有新视频包也能生成文件。

AAC 包直接封装，不解码/重新编码音频。音视频共用设备 PTS，开始时间通过媒体 PTS 与本机单调时钟映射确定，不使用久未变化的视频 PTS 充当当前时间。音频以完整 AAC 包为边界，48 kHz 下 1024 个采样约为 21.3 ms；不承诺采样级裁剪或跨设备硬实时同步。

一包以内的音频时间戳抖动按连续采样时钟处理；超过一包的向前跳变保留静默区间。这类文件在停止时通过 macOS 原生接口修正 MP4 时间线，使用同目录临时副本并在成功后原子替换，不重新编码音视频；停止所需时间和临时磁盘空间会增加。时间戳向后重叠超过一包，或一份录制超过 4096 个间隔时报告错误。

| Android API 级别 | 连接时的音频行为 |
| --- | --- |
| >= 33（Android 13+） | `audio_source=playback audio_dup=true`，保留手机应用声音 |
| 30–32（Android 11–12L） | `audio_source=output audio_dup=false`，手机静音；Android 11 启动时需要解锁 |
| < 30 | 音频关闭，录制只含视频 |

API 查询失败使连接失败，不猜测版本。音频从连接时开始采集并持续消费，与是否正在录制无关；Android 11–12L 的静音也从连接时开始。服务端用禁用标记明确告知音频不可用时仍保留视频和控制，录制会记录警告并输出纯视频。不承诺应用禁止采集时仍能取得声音。[scrcpy 3.2 音频契约](https://github.com/Genymobile/scrcpy/blob/v3.2/doc/audio.md)

成功停止后重复调用无操作。断连自动停止录制；自动收尾/异步写入的错误保留在最近的录制上，后续 `stop_recording()` 仍会报告该错误。音频流异常 EOF、非法数据和半包超时会触发会话清理，尽力完成已收到的媒体。新录制可以在旧录制停止后创建，不会因正常停止而关闭设备连接。

停止信号之后，音频流继续消费，但不再进入录制；文件收尾不会阻塞仍连接设备的音频接收。已排队、时间晚于停止边界的视频也不会延长文件的播放时长。

取消开始时，会等待原生创建和结果安装，再结束已创建的录制；不会遗留活动录制，但可能留下已经完成收尾的短文件。取消停止时仍等待在途操作及 MP4 完成，再传播 `CancelledError`。磁盘或系统错误仍可能导致不可播放的部分文件；库不会把文件存在当作录制成功。

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
| `action_series(actions: list[GestureActionNode], check: bool = True) -> None` | 按顺序发送单指或多指手势节点 |

无连接、坐标不合法或手势检查失败时，控制方法记录日志并返回。`None` 不表示手机已经处理了动作；`drain()` 只处理本地发送背压。断连后无法保证最后的抬起消息到达，调用方不应依赖断连后的后续动作。方法内的锁保护指定操作序列；整个 `double_click()` 等组合调用并非全部持有同一把锁。

### 手势类型

```python
from adb_scr import GestureAction, GestureActionNode

# GestureAction.DOWN = 0; UP = 1; MOVE = 2
node = GestureActionNode(x=100, y=200, action=GestureAction.DOWN, duration_ms=50)
```

`GestureActionNode` 是数据类：`x: int`、`y: int`、`action: GestureAction`、`duration_ms: int = 50`、`pointer_id: int = 0`。新增的 pointer_id 位于参数末尾，原有四个位置参数仍可使用；默认 ID 0 表示单指。同一根手指的 DOWN/MOVE/UP 使用同一 ID，不同手指使用不同 ID。ID 必须是 0 到 `2**63 - 1` 的整数，不接受 bool；负值保留给 scrcpy 的特殊指针类型。

持续时间表示该节点发送后的等待，最后一个节点也适用；执行时包含随机扰动，不是精确计时。以下是单指示例：

```python
await device.action_series([
    GestureActionNode(100, 500, GestureAction.DOWN, 10),
    GestureActionNode(100, 400, GestureAction.MOVE, 20),
    GestureActionNode(100, 300, GestureAction.UP, 0),
])
```

`check=True` 在发送第一个节点之前校验整份列表：

- 非空列表至少两个节点，持续时间为 0–10000 毫秒，动作必须是 DOWN/MOVE/UP。
- 每个 ID 的 DOWN/UP 必须一一配对；未按下不能 MOVE 或 UP，已按下不能再次对同一 ID 发 DOWN。
- 不同 ID 可以连续 DOWN，也可以交错 MOVE、UP。全部事件结束时所有手指必须抬起，最后一个动作必须是 UP。
- 同一 ID 抬起后可以再次使用，一份列表可以包含多组单指或多指手势。最多同时按下 10 根手指，与所用 scrcpy 3.2 的上限一致；累计使用的 ID 数量不受此上限限制。

校验失败会记录日志并返回 None，整份列表不会发送任何节点。DOWN/UP 总数相同但 ID 不匹配也会被拒绝；不会自动补发 UP 来修正输入。空列表保持无操作。`check=False` 跳过数量、时长及手指状态校验，但仍完整检查坐标与 pointer_id 的类型和范围。

例如，以下两根手指可以同时按下、分别移动，再按任意顺序抬起：

```python
await device.action_series([
    GestureActionNode(100, 400, GestureAction.DOWN, pointer_id=0),
    GestureActionNode(300, 400, GestureAction.DOWN, pointer_id=1),
    GestureActionNode(80, 400, GestureAction.MOVE, pointer_id=0),
    GestureActionNode(320, 400, GestureAction.MOVE, pointer_id=1),
    GestureActionNode(80, 400, GestureAction.UP, pointer_id=0),
    GestureActionNode(320, 400, GestureAction.UP, pointer_id=1),
])
```

节点仍按列表顺序发送；“多指”表示多个触点可同时保持按下，不是同时提交整批坐标。状态校验针对当前列表，断连或取消仍可能中断已开始的手势，不能保证手机收到最终 UP。

## 原生 API 与异常

`adb_scr.media_ext._adb_scr_media` 属于底层接口。完整类型与参数约束见 [`_adb_scr_media.pyi`](../src/adb_scr/media_ext/_adb_scr_media.pyi)。输入须符合存根约定；类型注解不代替运行时验证。

原生句柄通过容器和互斥锁协调入队、读取及销毁；重复关闭安全，关闭后读取返回 None，入队返回 False。返回的 BGRA8 bytes 不引用 CVPixelBuffer。仍应显式关闭句柄，Capsule 析构仅作兜底。扩展支持 free-threaded CPython，已验证 3.14t；需要安装对应 ABI 的构建。同一句柄的原生操作串行，不同句柄及独立 BGRA8 → JPEG 编码可并行。

创建、取帧、JPEG 编码和销毁须从异步路径调度到工作线程。原生耗时操作分离 Python 线程状态，在普通 CPython 下释放 GIL；这不会让同步函数变为异步函数。Python 设备对象、库初始化/反初始化及模块配置仍在同一事件循环内管理，asyncio 锁不提供跨线程保证。取消协程不停止在途原生操作；库会等待相应操作完成，避免释放仍被工作线程使用的资源。VideoToolbox/GCD 销毁没有强制超时。

公开异常位于 `adb_scr.exceptions`：`AdbScrPyException`、`AdbScrPyInitException`、`AdbScrPyH264DecoderException`。设备 `connect()` 将普通连接异常转换成 False，`asyncio.CancelledError` 在清理后传播。
