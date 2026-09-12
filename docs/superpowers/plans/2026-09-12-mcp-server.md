# Android MCP server implementation plan

**Goal:** 在 `src/adb_scr_mcp` 提供由 FastAPI lifespan 管理的 Streamable HTTP MCP 服务，收到 Ctrl+C/SIGTERM 后等待设备及库清理再退出。

**Architecture:** 使用官方 MCP Python SDK 的 FastMCP，父 FastAPI lifespan 管理 session manager。进程内 runtime 拥有设备注册表及在途操作，按序关闭；专用 Uvicorn runner 将重复信号转为幂等退出请求，不关闭事件循环或跳过 lifespan。

**Constraints:** Python >=3.10；开发环境 3.14；原生媒体管线不改；所有测试无设备；依赖采用 `mcp` extra；默认仅监听 loopback，单进程、不自动连接手机。

## Implementation

- [x] `tests/test_mcp_server.py`：先验证父 lifespan 初始化/反初始化、MCP initialize/list/call、JPEG 图片返回、错误参数、连接复用、失败/取消连接回收、关闭拒绝新操作及取消期间等待清理。
- [x] `src/adb_scr_mcp/runtime.py`：实现进程内所有权、按设备串行访问、在途操作跟踪、幂等且抗取消的关闭。
- [x] `src/adb_scr_mcp/tools.py`、`app.py`、`__init__.py`：实现 `create_app(adb_path=None)`，`/mcp` 传输、`/healthz`，公开设备/截图/触控/应用/录制工具；参数验证和 SDK 错误响应。
- [x] `tests/test_mcp_shutdown.py`、测试子进程：对真实本地 HTTP 服务发送 SIGINT/SIGTERM 和重复信号，覆盖启动中、在途调用中、清理中及端口占用，断言资源清理顺序和正常退出。
- [x] `src/adb_scr_mcp/server.py`、`__main__.py`、`pyproject.toml`：专用 signal handler 与 runner、CLI、可选依赖和 console script。
- [x] `docs/mcp.md`、`docs/architecture.md`、README：安装、启动、客户端地址、工具参数、关闭语义和所有权限制。
- [x] 正常 3.14 原地构建并运行新增及会话/生命周期回归；独立临时源树在真实 3.10 构建 wheel、导入、执行同组无设备测试；检查打包内容与语法；保留普通 3.14 `.venv`。

## Verification commands

```bash
uv run --python 3.14 --locked --extra mcp setup.py build_ext --inplace
uv run --python 3.14 --locked --extra mcp pytest tests/test_mcp_server.py tests/test_mcp_shutdown.py tests/test_device_session.py tests/test_lifecycle.py -q
```

独立源树复制与 wheel 安装沿用 `docs/python-compatibility.md` 的流程，sync 增加 `--extra mcp`。不执行 `tests/test_run.py`，不连接真实手机，不提交或推送。
