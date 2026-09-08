import asyncio
import math
import random
from asyncio import Lock
from asyncio.subprocess import Process
from collections.abc import Awaitable
from typing import TYPE_CHECKING, final

from .. import consts
from ..adb_cmd.base import adb_connect, adb_device_cmd, adb_disconnect
from ..adb_cmd.device_control import (
    launch_app,
    push_file,
    start_scrcpy_server,
    stop_app,
)
from ..async_utils import complete_on_cancel, stop_process
from ..logger import logger
from .bin_utils import random_sleep_ms, to_u32_be
from .control_handle import DeviceControlHandle
from .options import ConnectionOptions
from .types import ConnectionType, GestureAction, GestureActionNode

__all__ = []


class _DisconnectEvent(asyncio.Event):
    disconnect_reason: str | None = None


@final
class AndroidDevice:
    if TYPE_CHECKING:
        # 设备序列号/IP地址
        serial: str
        # 是否为网络设备，网络设备需要多的connect和disconnect操作
        connection_type: ConnectionType
        scid: str
        # scrcpy服务进程
        scrcpy_server_process: Process | None
        control_handle: DeviceControlHandle | None
        _mutex: Lock

    def __init__(
        self,
        serial: str,
        connection_type: ConnectionType,
        *,
        options: ConnectionOptions | None = None,
    ) -> None:
        """创建一个设备会话管理对象，不立即连接。

        Args:
            serial: USB 序列号，或已启用网络调试设备的 IP:端口。
            connection_type: "usb" 或 "tcp"。
            options: 连接、I/O、关闭及存活探测配置；None 使用默认值。

        Notes:
            一个实例及其异步方法应在同一事件循环中使用。
        """
        self.options = options or ConnectionOptions()
        self.control_handle = None
        self._monitor_task: asyncio.Task | None = None
        self._tcp_owned = False
        self._disconnected = _DisconnectEvent()
        self._disconnected.set()
        self.last_disconnect_reason: str | None = None
        self.serial = serial
        self.connection_type = connection_type
        # scid是一个0到2^31的整数，按照%08x的格式展示的字符串
        random_int = random.randint(0, 2**31 - 1)
        self.scid = f"{random_int:08x}"
        self.scrcpy_server_process = None
        self._mutex = Lock()

    @property
    def is_connected(self) -> bool:
        """当前会话是否可用；不主动探测网络，不表示首帧已经解码。"""
        return (
            self.control_handle is not None
            and self.control_handle.running
            and self.scrcpy_server_process is not None
            and self.scrcpy_server_process.returncode is None
        )

    async def wait_disconnected(self) -> str | None:
        """等待调用开始时的会话清理完成。

        Returns:
            首次断连原因。实例尚未连接过时立即返回 None；已断开时立即返回
            最近会话的原因。后续重连不改变已开始等待的会话结果。

        Raises:
            asyncio.CancelledError: 等待被取消；不会因此断开设备。

        Notes:
            可使用 asyncio.wait_for() 限制调用方的等待时间。
        """
        event = self._disconnected
        await event.wait()
        return event.disconnect_reason

    async def connect(self) -> bool:
        """连接设备并等待视频元数据就绪。

        Returns:
            成功返回 True；连接失败或超时后回收资源并返回 False。
            已有有效会话时返回 True，失败或断开后可在同一实例上重新连接。

        Raises:
            asyncio.CancelledError: 调用被取消，已取得的会话资源会先被回收。

        Notes:
            调用前必须完成 init_lib()。返回 True 表示两条流和屏幕尺寸已就绪，
            不保证已有解码帧。连接预算从取得设备锁后开始，清理可额外耗时。
            不自动重连。
        """
        async with self._mutex:
            if self.is_connected:
                return True
            await complete_on_cancel(self._disconnect_locked("清理旧会话"))
            self._disconnected = _DisconnectEvent()
            self.last_disconnect_reason = None
            try:
                await asyncio.wait_for(
                    self._connect_session(), self.options.connect_timeout
                )
                if not self.is_connected:
                    raise ConnectionError("会话在连接期间退出")
                self._monitor_task = asyncio.create_task(self._supervise())
                return True
            except asyncio.CancelledError:
                await complete_on_cancel(self._disconnect_locked("连接已取消"))
                raise
            except Exception as error:
                await complete_on_cancel(
                    self._disconnect_locked(f"连接失败：{error!r}")
                )
                return False

    async def _connect_session(self) -> None:
        if self.connection_type == "tcp":
            self._tcp_owned = True
            if await adb_connect(self.serial, timeout=self.options.io_timeout) != 0:
                raise ConnectionError("ADB 网络连接失败")
        if not await push_file(
            self.serial, consts.SCRCPY_SERVER_PATH, consts.SCRCPY_PATH_ON_DEVICE
        ):
            raise ConnectionError("推送 scrcpy server 失败")
        self.scrcpy_server_process = await start_scrcpy_server(self.serial, self.scid)
        if self.scrcpy_server_process is None:
            raise ConnectionError("启动 scrcpy server 失败")
        self.control_handle = DeviceControlHandle(
            self.serial, self.scid, options=self.options
        )
        if not await self.control_handle.connect_sockets():
            raise ConnectionError(self.control_handle.disconnect_reason)

    async def _probe(self) -> str:
        failures = 0
        while True:
            await asyncio.sleep(self.options.probe_interval)
            # A device-side no-op checks transport liveness without touching UI.
            if await adb_device_cmd(
                self.serial, "shell", "true", timeout=self.options.io_timeout
            ):
                failures = 0
            else:
                failures += 1
                if failures >= self.options.probe_failures:
                    return "设备存活探测连续失败"

    async def _supervise(self) -> None:
        handle = self.control_handle
        process = self.scrcpy_server_process
        stream_task = asyncio.create_task(handle.wait_disconnected())
        process_task = asyncio.create_task(process.wait())
        tasks = [stream_task, process_task]
        if self.options.probe_interval is not None:
            tasks.append(asyncio.create_task(self._probe()))
        try:
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            if stream_task in done:
                reason = stream_task.result()
            elif process_task in done:
                reason = f"scrcpy 进程退出：{process_task.result()}"
            else:
                reason = tasks[-1].result()
            handle._stop(reason)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            reason = f"会话监控失败：{error!r}"
            handle._stop(reason)
        finally:
            for task in tasks:
                task.cancel()
            await complete_on_cancel(asyncio.gather(*tasks, return_exceptions=True))
        async with self._mutex:
            if self.control_handle is handle:
                await complete_on_cancel(
                    self._disconnect_locked(reason, skip_monitor=True)
                )

    async def _disconnect_locked(
        self, reason: str, *, skip_monitor: bool = False
    ) -> None:
        monitor, self._monitor_task = self._monitor_task, None
        if monitor is not None and not skip_monitor:
            monitor.cancel()
            await asyncio.gather(monitor, return_exceptions=True)
        handle, self.control_handle = self.control_handle, None
        process, self.scrcpy_server_process = self.scrcpy_server_process, None
        had_session = not self._disconnected.is_set()
        try:
            operations = []
            if handle is not None:
                operations.append(handle.disconnect_sockets(reason))
            if process is not None:
                operations.append(stop_process(process, self.options.close_timeout))
            if self._tcp_owned:
                self._tcp_owned = False
                operations.append(
                    adb_disconnect(self.serial, timeout=self.options.close_timeout)
                )
            results = await asyncio.gather(*operations, return_exceptions=True)
            for result in results:
                if isinstance(result, BaseException):
                    logger.warning(f"会话清理失败：{result!r}")
            if had_session:
                self.last_disconnect_reason = (
                    handle.disconnect_reason if handle is not None else None
                ) or reason
        finally:
            self._disconnected.disconnect_reason = self.last_disconnect_reason
            self._disconnected.set()

    async def disconnect(self) -> None:
        """断开当前会话，等待接收任务、探测任务、流及原生资源清理。

        Raises:
            asyncio.CancelledError: 取消在已开始的清理完成后传播。

        Notes:
            重复调用安全。流和子进程关闭异常会记录日志；原生销毁必须等待
            VideoToolbox/GCD 工作完成，不能用硬超时强制释放在用内存。
        """
        if self.control_handle is not None and self.control_handle.running:
            self.control_handle._stop("主动断开")
        async with self._mutex:
            await complete_on_cancel(self._disconnect_locked("主动断开"))

    async def launch_app(self, package_name: str, activity_name: str) -> bool:
        """启动设备上的应用

        Args:
            package_name: 应用包名
            activity_name: 应用主活动名

        Returns:
            是否成功
        """
        async with self._mutex:
            return await launch_app(self.serial, package_name, activity_name)

    async def stop_app(self, package_name: str) -> bool:
        """停止设备上的应用

        Args:
            package_name: 应用包名

        Returns:
            是否成功
        """
        async with self._mutex:
            return await stop_app(self.serial, package_name)

    def get_screen_size(self) -> tuple[int, int] | None:
        """读取当前屏幕尺寸。

        Returns:
            (width, height)，单位为像素；会话不可用时返回 None。

        Notes:
            坐标原点为左上角，有效范围为 0 <= x < width、0 <= y < height。
            屏幕旋转后尺寸可随新的视频配置更新。
        """
        if not self.is_connected:
            logger.warning("设备未连接，无法获取屏幕尺寸")
            return None

        return self.control_handle.screen_width, self.control_handle.screen_height

    async def get_screenshot_jpg(
        self,
        quality: int = 75,
        scale: float = 1.0,
        roi: tuple[int, int, int, int] | None = None,
    ) -> bytes | None:
        """获取最新视频帧的 JPEG 截图，可选原图裁剪和缩放。

        Args:
            quality: 1 到 100 的整数，默认 75；不保证不同编码器画质一致。
            scale: 有限正数，默认 1.0；小于 1 缩小，大于 1 放大。
            roi: 原图像素坐标 (x, y, width, height)，左上角为原点；必须
                完全位于当前帧内。None 表示整图，默认 None。

        Returns:
            独立 JPEG bytes；未连接、尚无帧或编码失败时返回 None。

        Raises:
            TypeError: 参数类型不符合约定（bool 不作为数值接受）。
            ValueError: 质量、比例、ROI 或缩放后尺寸超出有效范围。
            OverflowError: 整数参数超出原生数值类型范围。

        Notes:
            先裁剪再缩放。输出宽高分别按 floor(裁剪尺寸 * scale + 0.5)
            取整，最少 1 像素，最多 65535 像素；不要求偶数尺寸。
            ROI 边界以取得的帧为准，不依赖可能已过期的屏幕尺寸缓存。
            无可用会话时直接返回 None；ROI 越界检查需要已有解码帧。
            多次调用可能返回同一帧，取消会等待原生工作结束。
        """
        if self.control_handle is None:
            return None
        return await self.control_handle.get_current_frame_jpg(quality, scale, roi)

    def _check_in_screen(self, x: int, y: int) -> bool:
        """检查坐标是否在屏幕内，防止后续操作出现意外。
        屏幕坐标依然遵循0-based索引，所以1920x1080的屏幕，最大坐标为1919,1079

        Args:
            x: x坐标
            y: y坐标

        Returns:
            是否在屏幕内
        """
        size = self.get_screen_size()
        if size is None:
            return False

        width, height = size
        return 0 <= x < width and 0 <= y < height

    async def _gesture_wait(self, delay: Awaitable[None]) -> bool:
        """等待手势间隔或会话停止，避免长手势阻塞资源回收。"""
        sleep_task = asyncio.ensure_future(delay)
        handle = self.control_handle
        if handle is None:
            sleep_task.cancel()
            await asyncio.gather(sleep_task, return_exceptions=True)
            return False
        stopped = asyncio.create_task(handle._stopped.wait())
        try:
            done, _ = await asyncio.wait(
                [sleep_task, stopped], return_when=asyncio.FIRST_COMPLETED
            )
            if sleep_task in done:
                sleep_task.result()
            return handle.running
        finally:
            sleep_task.cancel()
            stopped.cancel()
            await complete_on_cancel(
                asyncio.gather(sleep_task, stopped, return_exceptions=True)
            )

    async def long_press(self, x: int, y: int, duration_ms: int) -> None:
        """在指定坐标按下并保持，然后抬起。

        Args:
            x: 横坐标，单位为像素。
            y: 纵坐标，单位为像素。
            duration_ms: 持续时间，单位为毫秒，调用方应传入非负整数。

        Notes:
            无有效会话或坐标越界时记录日志并返回。返回 None 不代表设备 UI
            已执行动作；断连后无法保证最终抬起消息送达。
        """
        if not self._check_in_screen(x, y):
            logger.error(f"坐标({x},{y})不在屏幕内，拒绝执行")
            return

        async with self._mutex:
            if self.control_handle is None:
                logger.warning("设备未连接，无法执行长按操作")
                return

            if not await self.control_handle.send_gesture_event(
                x, y, GestureAction.DOWN.value
            ):
                return
            if not await self._gesture_wait(asyncio.sleep(float(duration_ms) / 1000)):
                return
            await self.control_handle.send_gesture_event(x, y, GestureAction.UP.value)

    async def click(self, x: int, y: int) -> None:
        """单击屏幕上的某个位置

        Args:
            x: x坐标
            y: y坐标
        """
        # 相当于一个时间很短的"long_press"
        await self.long_press(x, y, random.randint(150, 220))

    async def double_click(self, x: int, y: int) -> None:
        """双击屏幕上的某个位置

        Args:
            x: x坐标
            y: y坐标
        """
        # 相当于快速的两次click
        await self.click(x, y)
        await random_sleep_ms(50, 100)
        await self.click(x, y)

    async def press_back(self) -> None:
        """
        返回键
        """
        async with self._mutex:
            if self.control_handle is None:
                logger.warning("设备未连接，无法执行返回键操作")
                return

            await self.control_handle.send_event(
                bytes([0x04, GestureAction.DOWN.value])
            )
            if not await self._gesture_wait(random_sleep_ms(80, 150)):
                return
            await self.control_handle.send_event(bytes([0x04, GestureAction.UP.value]))

    async def swipe(self, x1: int, y1: int, x2: int, y2: int) -> None:
        """一个简化的方便版本，用于执行一个简单的滑动操作。
        里面用到了一些插帧操作，确保滑动过程中尽量丝滑。

        Args:
            x1: 滑动开始的x坐标
            y1: 滑动开始的y坐标
            x2: 滑动结束的x坐标
            y2: 滑动结束的y坐标
        """
        STEPPING = 25.0
        SPEED_RATIO = 1.85
        distance = math.sqrt((x2 - x1) ** 2 + (y2 - y1) ** 2)
        steps = int(distance / STEPPING)

        move_actions: list[GestureActionNode] = [
            GestureActionNode(x1, y1, GestureAction.DOWN, 10)
        ]

        # 滚动距离太小可能会除0，稍微处理一下，然后分步挪动
        if steps > 0:
            gap_time_ms = int(distance * SPEED_RATIO / steps)
            step_x = (x2 - x1) / steps
            step_y = (y2 - y1) / steps

            for i in range(steps):
                move_actions.append(
                    GestureActionNode(
                        int(x1 + step_x * i),
                        int(y1 + step_y * i),
                        GestureAction.MOVE,
                        gap_time_ms,
                    )
                )

        # 由于steps可能会因为floor导致实际上少了一步，所以这里可能需要补上一次
        if distance % STEPPING > 0.01:
            move_actions.append(GestureActionNode(x2, y2, GestureAction.MOVE, 10))

        # 抬起手指
        move_actions.append(GestureActionNode(x2, y2, GestureAction.UP, 0))

        # 执行
        await self.action_series(move_actions)

    async def action_series(
        self, actions: list[GestureActionNode], check: bool = True
    ) -> None:
        """按顺序发送单指或多指手势节点。

        Args:
            actions: 节点列表，以 pointer_id 区分手指。每个节点发送后按
                duration_ms 加入随机等待，最后一个节点也适用。空列表不执行操作。
            check: 默认 True，检查节点数量、持续时间、每根手指的状态和配对，
                同时按下的手指最多 10 根。False 跳过这些检查，但仍检查全部
                坐标和 pointer_id 的类型及范围。

        Notes:
            check=True 时，每个 ID 必须先 DOWN，按下后才允许 MOVE 或 UP；
            已按下的 ID 不能重复 DOWN，UP 后可以再次 DOWN。不同 ID 的动作
            可以交错，结束时必须全部抬起，支持连续多组手势。
            持续时间范围为 0 到 10000 毫秒；等待含随机扰动，不是精确的计时接口。
            发送前完整检查列表，失败时记录日志并返回，不发送任何节点。
            返回值不表示动作执行确认；断连或取消仍可能中断已开始的手势。
        """
        if len(actions) == 0:
            return

        for action in actions:
            # 检查有没有坐标越界
            if not self._check_in_screen(action.x, action.y):
                logger.error(f"坐标({action.x},{action.y})不在屏幕内，拒绝执行")
                return
            if (
                not isinstance(action.pointer_id, int)
                or isinstance(action.pointer_id, bool)
                or not 0 <= action.pointer_id <= (1 << 63) - 1
            ):
                logger.error("pointer_id必须是0到2**63-1的整数，拒绝执行")
                return

        if check:
            if len(actions) < 2:
                logger.error("手势操作列表长度太短，拒绝执行")
                return

            for action in actions:
                # 检查duration_ms是否符合要求
                if action.duration_ms > 10000 or action.duration_ms < 0:
                    logger.error("节点的duration_ms异常，拒绝执行")
                    return

            # 按 ID 跟踪仍按下的手指，允许不同手指的动作交错。
            active_pointers: set[int] = set()
            for node in actions:
                match node.action:
                    case GestureAction.DOWN:
                        if node.pointer_id in active_pointers:
                            logger.error(f"手指{node.pointer_id}已按下，不能重复DOWN，拒绝执行")
                            return
                        # scrcpy 3.2 的 PointersState.MAX_POINTERS 为 10。
                        if len(active_pointers) >= 10:
                            logger.error("同时按下的手指不能超过10根，拒绝执行")
                            return
                        active_pointers.add(node.pointer_id)
                    case GestureAction.MOVE:
                        if node.pointer_id not in active_pointers:
                            logger.error(f"手指{node.pointer_id}未按下，不能MOVE，拒绝执行")
                            return
                    case GestureAction.UP:
                        if node.pointer_id not in active_pointers:
                            logger.error(f"手指{node.pointer_id}未按下，不能UP，拒绝执行")
                            return
                        active_pointers.remove(node.pointer_id)
                    case _:
                        logger.error(f"未知的手势操作{node.action}，拒绝执行")
                        return
            if active_pointers:
                logger.error(f"手势结束时仍有未抬起的手指{sorted(active_pointers)}，拒绝执行")
                return

        async with self._mutex:
            if self.control_handle is None:
                logger.warning("设备未连接，无法执行长按操作")
                return

            # 发送事件
            for node in actions:
                if not await self.control_handle.send_gesture_event(
                    node.x, node.y, node.action.value, node.pointer_id
                ):
                    return

                if node.duration_ms > 0:
                    # 添加一点扰动避免检测
                    if not await self._gesture_wait(
                        random_sleep_ms(
                            min(1, node.duration_ms - 10), node.duration_ms + 10
                        )
                    ):
                        return

    async def paste(self, text: str) -> None:
        """粘贴文本到设备。执行这个操作时，需要先将设备焦点切换到文本输入框，否则是无效的

        Args:
            text: 要粘贴的文本
        """
        async with self._mutex:
            if self.control_handle is None:
                logger.warning("设备未连接，无法执行粘贴操作")
                return

            # 固定头
            data = bytes([0x09, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x01])
            text_bytes = text.encode("utf-8")
            data += to_u32_be(len(text_bytes))
            data += text_bytes

            await self.control_handle.send_event(data)
