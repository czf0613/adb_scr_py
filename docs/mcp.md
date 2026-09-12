# Android MCP 服务

`src/adb_scr_mcp` 使用 FastAPI 和官方 MCP Python SDK，通过 Streamable HTTP
提供设备控制工具。服务进程在 lifespan 中初始化 `adb_scr`，手机由客户端显式连接。
导入模块或调用 `create_app()` 不启动 ADB。

## 安装与启动

在仓库根目录执行：

```bash
uv sync --python 3.14 --locked --extra mcp
uv run --python 3.14 --locked --extra mcp setup.py build_ext --inplace
uv run --python 3.14 --locked --extra mcp adb-scr-mcp
```

也可以使用模块入口：

```bash
uv run --python 3.14 --locked --extra mcp python -m adb_scr_mcp --port 8000
```

服务要求 macOS、已安装的 ADB 和对应 Python ABI 的原生扩展，与基础库相同。
Python 最低支持 3.10；服务依赖是可选的 `mcp` extra，普通 `adb_scr` 使用者无需安装。

参数：

| 参数 | 默认值 | 含义 |
| --- | --- | --- |
| `--host` | `127.0.0.1` | 可选 `127.0.0.1`、`localhost`、`::1` |
| `--port` | `8000` | HTTP 监听端口 |
| `--adb-path` | PATH 中的 `adb` | 自定义 ADB 可执行文件路径 |
| `--drain-timeout` | `10` | 退出时等待在途 HTTP 请求的秒数，允许 0 |

MCP 客户端填写 URL **`http://127.0.0.1:8000/mcp`**，选择 Streamable HTTP。
`GET /healthz` 在就绪时返回 200，退出开始后返回 503；Uvicorn 关闭监听后连接会被拒绝。
没有旧版 SSE `/sse` 或 stdio 传输。

## Agent 如何发现和使用服务

给 agent 的操作指南位于 [agent_guide.md](../src/adb_scr_mcp/agent_guide.md)，作为
运行时资源随 wheel/sdist 一起分发。它与面向开发者的本文件分开，只有一份正文。

服务通过三个入口让 agent 发现使用方法：

1. MCP `initialize` 返回 `instructions`：简短说明能力范围、推荐调用顺序和指南入口。
2. `resources/list` 列出 `adb-scr://guide`，`resources/read` 返回完整 Markdown 指南。
3. `get_agent_guide()` 返回同一正文，供不自动读取 resources 的客户端使用。

每个工具仍通过 `tools/list` 提供自己的说明和参数 JSON Schema。指南解释
“枚举 → 连接 → 截图观察 → 操作 → 截图确认”、原图坐标换算、错误重试、
手机会话所有权和录制路径；不包含任何真实设备标识或用户数据。

指南、设备枚举、设备状态和截图标记为 `readOnlyHint=true`；连接、触控、应用
控制和录制保留有副作用的语义。客户端仍可按自己的权限策略要求用户批准。

不同 MCP 客户端对 server instructions/resources 的展示方式不同；不能假定它们
会自动把仓库内的 Markdown 文件交给模型。新 agent 首次使用应读取指南工具或资源，
后续再按工具 schema 调用。可以用一次性 Codex CLI 配置验证，无需写入全局设置：

```bash
codex exec --ephemeral \
  -c 'mcp_servers.adb_scr.url="http://127.0.0.1:8000/mcp"' \
  '读取 adb_scr 的 get_agent_guide，然后仅列出设备，暂不连接或操作。'
```

无人值守的 `codex exec` 在需要批准而无法交互时会拒绝工具。对用户已明确授权
的一次任务，可在本次调用中用 `enabled_tools` 限定工具，再用
`default_tools_approval_mode="approve"` 允许该范围；不要把所有手机控制工具
永久设为免确认。配置字段见 [Codex MCP 官方说明](https://learn.chatgpt.com/docs/extend/mcp?surface=cli)。

## 客户端示例

下面仅枚举设备，不自动控制手机：

```python
import asyncio

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


async def main():
    async with streamable_http_client("http://127.0.0.1:8000/mcp") as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            print([tool.name for tool in tools.tools])
            result = await session.call_tool("list_devices", {})
            print(result.structuredContent)


asyncio.run(main())
```

通常按 `list_devices` → `connect_device` → `get_device_info`/`screenshot` → 控制工具
的顺序操作。连接示例参数为 `{"serial": "YOUR_USB_SERIAL", "connection_type": "usb"}`；
网络设备使用 `{"serial": "192.168.1.100:5555", "connection_type": "tcp"}`。
同一序列号复用一个会话；设备掉线后需要再次显式调用 `connect_device`。
首次连接时确定的 `connection_type` 在该服务进程中保持不变。

## 工具

除 `get_agent_guide` 和 `list_devices` 外，各工具都需要 `serial`。

| 工具 | 其他参数 | 结果/约定 |
| --- | --- | --- |
| `get_agent_guide` | 无 | 读取 agent 操作指南，不需要 serial 或手机连接 |
| `list_devices` | 无 | `devices` 包含 ADB 枚举及已管理会话；枚举结果可能包含未授权或离线设备 |
| `connect_device` | `connection_type="usb"` | 连接状态、原图尺寸；等待元数据，不保证首帧已解码 |
| `disconnect_device` | 无 | 关闭会话、完成录制；重复调用安全 |
| `get_device_info` | 无 | 当前连接状态、原图尺寸、最近断连原因 |
| `screenshot` | `quality=75`、`scale=1.0`、`roi=null` | MCP `image` 内容，MIME 为 `image/jpeg` |
| `click` / `double_click` | `x`、`y` | 单击/双击 |
| `long_press` | `x`、`y`、`duration_ms` | 长按后抬起，0–10000 毫秒 |
| `swipe` | `x1`、`y1`、`x2`、`y2` | 从起点滑到终点 |
| `action_series` | `actions` | 按顺序发送单指/多指节点 |
| `press_back` | 无 | Android 返回键 |
| `paste` | `text` | 粘贴到已聚焦输入框，最多 100000 字符 |
| `launch_app` | `package_name`、`activity_name` | 例如 `com.example.app`、`.MainActivity` |
| `stop_app` | `package_name` | 强制停止应用 |
| `start_recording` | `output_file`，`quality=0.75`（0.0–1.0） | 写入**服务端电脑**上的新 MP4 文件，不覆盖已有文件，不创建父目录 |
| `stop_recording` | 无 | 等待 MP4 完成；断连后仍可查询最近录制的写入错误 |

录屏 `quality` 接受 `0.0–1.0` 的有限数，默认 `0.75`，与截图的 `1–100` 不同。
默认值用于兼顾画质和体积；降低数值通常减小文件，提高数值通常改善画质并增大
文件。它不是固定码率或文件体积比例，不保证固定缩减幅度，`1.0` 也不保证
H.264 无损。实际效果随画面和硬件变化，AAC 音频保持直通。MCP 参数示例：

```json
{"serial": "YOUR_DEVICE_SERIAL", "output_file": "/path/to/capture.mp4", "quality": 0.75}
```

截图 quality 范围为 1–100；scale 必须是有限正数；ROI 为原图
`[x, y, width, height]`，先裁剪再缩放。触控始终使用原图左上角为原点的像素坐标。
例如 ROI `[100, 200, 600, 400]`、scale `0.5` 的截图上 `(20, 30)`，对应触控
坐标为 `(100 + 20 / 0.5, 200 + 30 / 0.5)`，即 `(140, 260)`。
暂无解码帧时截图返回工具错误，可稍后重试；不会阻塞等待无限长时间。

`actions` 接受 2–1000 个节点，例如：

```json
[
  {"x": 100, "y": 200, "action": "DOWN", "duration_ms": 50, "pointer_id": 0},
  {"x": 100, "y": 300, "action": "MOVE", "duration_ms": 50, "pointer_id": 0},
  {"x": 100, "y": 300, "action": "UP", "duration_ms": 0, "pointer_id": 0}
]
```

省略 `duration_ms` 时为 50，省略 `pointer_id` 时为 0；最多同时按下 10 根手指，
每根都必须先 DOWN，再 MOVE/UP，最终全部抬起。节点时长为 0–10000 毫秒，
沿用库的随机等待语义。服务在发送前完整校验列表，非法参数作为 MCP 工具错误返回。
普通控制结果 `status="submitted"` 表示调用已提交，并非手机 UI 执行确认。

## 优雅退出

使用上述专用入口启动后，Ctrl+C / SIGINT / SIGTERM 执行以下流程：

1. 将服务标记为停止中，拒绝新工具操作；Uvicorn 停止接收新连接。
2. 给在途 HTTP 请求 `--drain-timeout` 秒完成；超时后开始取消请求。
3. 退出 MCP session manager，取消并等待尚未结束的工具操作及连接回滚。
4. 对所有已登记设备调用并等待 `disconnect()`。库完成录制 MP4、收流任务、
   socket、scrcpy 子进程、解码器及原生队列的清理。
5. 全部设备关闭后，调用并等待 `deinit_lib()`，再结束服务器和事件循环。

重复 Ctrl+C/SIGTERM 仍只请求退出，不进入 Uvicorn 的 `force_exit` 分支。
不调用 `loop.stop()`，不在同步信号回调中启动另一个 event loop，也不依赖
`atexit` 执行异步清理。初始化期间收到信号时，等待初始化结束再清理；初始化失败
或监听端口已被占用时也执行 lifespan 清理。嵌入时取消 `serve()` 协程，同样等待
清理后再传播 `CancelledError`。退出后恢复此前安装的信号处理器。

HTTP drain 超时不是整个退出流程的硬截止时间。原生 VideoToolbox/GCD 工作和
MP4 收尾必须完成才能释放资源，实际退出可能超过该时长。SIGKILL 或强制终止
进程无法执行 Python 清理。单台设备抛出清理异常时仍尝试清理其余设备并反初始化，
记录错误并以失败状态退出。

## 进程与访问边界

服务供同一电脑上的可信 MCP 客户端使用：仅监听 loopback，SDK 校验 Host/Origin，
未提供远程认证。客户端共享设备注册表和会话；同一设备的工具操作串行执行，
不同设备可以并行。HTTP 无状态不代表手机会话无状态。

使用一个进程、一份 app lifespan；不使用多个 worker 或 reload。基础库的 ADB
daemon 和配置是共享状态，服务会拒绝接管同进程中已经初始化的库。
`deinit_lib()` 会停止本机共享 ADB daemon，因此服务退出也会影响其他 ADB 客户端。

可以通过 `from adb_scr_mcp import create_app` 嵌入 FastAPI 应用，但宿主需要管理
该 app 的 lifespan。信号保护属于 `adb_scr_mcp.server.serve()` / CLI；普通
`uvicorn ...` 启动方式只保留 lifespan 的清理逻辑，不提供本服务的重复信号保护。
后台线程中的 `serve()` 不安装进程信号回调，宿主应通过取消该协程请求退出。

## 无设备验证

```bash
uv run --python 3.14 --locked --extra mcp setup.py build_ext --inplace
uv run --python 3.14 --locked --extra mcp pytest tests/test_mcp_server.py tests/test_mcp_shutdown.py tests/test_device_session.py tests/test_lifecycle.py -q
```

MCP 测试使用设备/ADB 替身与真实 HTTP/子进程信号，不连接手机。
Python 3.10 的独立构建步骤见 [兼容性文档](python-compatibility.md)。

## 已授权的真机验证

2026-09-12 使用一次性 Codex CLI 会话实际调用本服务，完成
`get_agent_guide` → `list_devices` → `connect_device` → 截图/点击/滑动，
在设置中读取两个 IMEI 栏位并用原比例 ROI 截图核对。测试未向仓库保存真实
设备标识或截图。设备保持连接时按 Ctrl+C，所有设备会话及库资源完成清理，
服务正常退出。此记录仅覆盖该次设备和工作流，不代表全部机型或 UI 路径均已验证。

### 红果短剧：录制与 MCP 取帧同时运行

同日经用户授权，在 Redmi K50 / Android 14 上通过 MCP 打开红果短剧播放页面，
调用 `start_recording` 后连续取帧 20 秒，再调用 `stop_recording`。
所有截图均走真实 Streamable HTTP `screenshot` 工具，1080×2400 原尺寸、
`quality=75, scale=1.0, roi=null`，以每秒 20 次为目标。

| 指标 | 实测 |
| --- | --- |
| 录制期间取帧 | 400 次，全部成功并通过 JPEG 解码，实际 20 次/秒 |
| 画面更新 | 解码后缩为 64×142 灰度图比较，400 帧中有 398 个不同画面 |
| 取帧延迟 | 中位数 9.09 ms，P95 10.27 ms，最大 18.60 ms |
| 调用开始时间最大间隔 | 50.97 ms |
| 开始/停止录制调用耗时 | 51.55 / 19.74 ms，包含 MCP 往返 |
| MP4 | 38,098,529 字节；容器 20.072542 秒，视频轨 20.053044 秒 |
| 视频 | H.264 High，1080×2400，598 帧，平均约 29.82 fps |
| 音频 | AAC-LC，48 kHz 双声道，非静音 |
| 停止录制后取帧 | 2 秒内 40 次全部成功，手机会话保持连接 |

录制前 2 秒取得 38 帧（探针跳过已错过的 50 ms 采样点），三个阶段共 478 张 JPEG
全部解码成功。MP4 音视频完整解码通过，包 PTS/DTS 严格递增，抽查第 10 秒
录制画面与同时获取的截图内容一致。视频包最大 PTS 间隔为 100 ms；没有上游
帧计数对照，不能据此宣称零丢帧。测试结束后 Ctrl+C 正常清理，退出码为 0。

完整解码检查保留源视频微秒时间精度，避免 null 输出默认按 30 fps 取整时产生
重复 DTS 提示：

```bash
uv run --python 3.14 --locked --extra mcp ffmpeg -hide_banner -v error -xerror \
  -i RECORDING.mp4 -map 0:v -map 0:a \
  -fps_mode:v passthrough -enc_time_base:v 1:1000000 -f null -
```

这次没有观察到录制导致 MCP JPEG 取帧失败或长时间画面冻结；
结论仅覆盖该设备、内容和 20 次/秒负载，不包含 BGRA8 原始帧接口的并发压力测试。
本地视频、抽样截图、探针脚本和指标保存在被 Git 忽略的
`build/device-tests/hongguo-20260912-1532/`，不进入分发包。
