from asyncio import Lock
import asyncio
from typing import TYPE_CHECKING, final
from asyncio.subprocess import Process
import random
from ..adb_cmd.base import adb_connect, adb_disconnect
from .. import consts
from ..logger import logger
from ..adb_cmd.device_control import (
    push_file,
    start_scrcpy_server,
    launch_app,
    stop_app,
)
from .control_handle import DeviceControlHandle
from .bin_utils import random_sleep_ms, to_u32_be
from ..media_ext import bgra8_to_jpg
from .types import ConnectionType, GestureAction, GestureActionNode
import math


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

    def __init__(self, serial: str, connection_type: ConnectionType) -> None:
        """
        初始化AndroidDevice对象

        :param serial: 设备序列号/IP地址，可以通过adb devices命令查看。如果是USB设备，
        则为设备的序列号，例如：emulator-5554。如果是网络设备，
        则为设备的IP地址+端口，例如：192.168.1.100:5555
        :param connection_type: 设备连接类型，目前只支持tcp和usb
        """
        self.serial = serial
        self.connection_type = connection_type
        # scid是一个0到2^31的整数，按照%08x的格式展示的字符串
        random_int = random.randint(0, 2**31 - 1)
        self.scid = f"{random_int:08x}"
        self.scrcpy_server_process = None
        self._mutex = Lock()

    async def connect(self) -> bool:
        """
        连接设备
        :return: 如果连接成功，则返回True；否则返回False
        """
        async with self._mutex:
            if self.scrcpy_server_process is not None:
                logger.warning(f"设备{self.serial}已经连接，无需重复连接")
                return True

            # 如果是网络设备，这里还要执行一步connect
            if self.connection_type == "tcp":
                logger.info(f"尝试连接TCP设备{self.serial}")
                if not await adb_connect(self.serial) == 0:
                    logger.error(f"连接设备{self.serial}失败")
                    return False

            # 推送dex过去
            if not await push_file(
                self.serial, consts.SCRCPY_SERVER_PATH, consts.SCRCPY_PATH_ON_DEVICE
            ):
                logger.error(f"推送scrcpy server到设备{self.serial}失败")
                return False

            # 启动scrcpy server
            sub_process = await start_scrcpy_server(self.serial, self.scid)
            if sub_process is None:
                logger.error(f"启动scrcpy server在设备{self.serial}失败")
                return False
            else:
                self.scrcpy_server_process = sub_process
                logger.info(f"成功启动scrcpy server在设备{self.serial}")

            # 启动socket
            handle = DeviceControlHandle(self.serial, self.scid)
            if not await handle.connect_sockets():
                logger.error(f"连接设备{self.serial}的控制socket失败")
                return False
            self.control_handle = handle

            return True

    async def disconnect(self) -> None:
        """
        断开设备连接
        :return: None
        """
        async with self._mutex:
            if self.scrcpy_server_process is None:
                logger.warning(f"设备{self.serial}未连接，无需断开")
                return

            # 终止scrcpy server进程
            if self.scrcpy_server_process is not None:
                self.scrcpy_server_process.kill()
                self.scrcpy_server_process = None
                logger.info(f"成功终止scrcpy server在设备{self.serial}")

            # 断开控制socket
            if self.control_handle is not None:
                await self.control_handle.disconnect_sockets()
                self.control_handle = None
                logger.info(f"成功断开设备{self.serial}的socket")

            # 如果是网络设备，这里还要执行一步disconnect
            if self.connection_type == "tcp":
                logger.info(f"尝试断开TCP设备{self.serial}")
                await adb_disconnect(self.serial)

    async def launch_app(self, package_name: str, activity_name: str) -> bool:
        """
        启动设备上的应用
        :param package_name: 应用包名
        :param activity_name: 应用主活动名
        :return: 是否成功
        """
        async with self._mutex:
            return await launch_app(self.serial, package_name, activity_name)

    async def stop_app(self, package_name: str) -> bool:
        """
        停止设备上的应用
        :param package_name: 应用包名
        :return: 是否成功
        """
        async with self._mutex:
            return await stop_app(self.serial, package_name)

    def get_screen_size(self) -> tuple[int, int] | None:
        """
        获取屏幕长宽。屏幕坐标系统是0-based索引，左上角为(0,0)，右下角为(width-1,height-1)
        注意，刚刚connect好的时候，由于屏幕数据可能还没及时送达，读取的结果可能是错的
        建议等待几百毫秒之后再尝试
        :return: 返回 (width, height) 元组，如果获取失败则返回 None
        """
        if self.control_handle is None:
            logger.warning("设备未连接，无法获取屏幕尺寸")
            return None

        return self.control_handle.screen_width, self.control_handle.screen_height

    async def get_screenshot_jpg(self, quality: int = 75) -> bytes | None:
        """
        获取当前屏幕截图的jpg数据
        （这个方法效率不是很高，内部有锁，不建议高频调用或利用这个接口重编码为视频流）
        :return: 返回jpg格式的字节数据，如果获取失败则返回 None
        """
        if self.control_handle is None:
            logger.warning("设备未连接，无法获取屏幕截图")
            return None

        frame_data = await self.control_handle.get_current_frame()
        if frame_data is None:
            return None

        width, height, bgra_data = frame_data
        jpg_data = bgra8_to_jpg(width, height, bgra_data, quality)
        if jpg_data is None:
            logger.error("将BGRA数据转换为JPG格式失败")
            return None

        return jpg_data

    def _check_in_screen(self, x: int, y: int) -> bool:
        """
        检查坐标是否在屏幕内，防止后续操作出现意外。
        屏幕坐标依然遵循0-based索引，所以1920x1080的屏幕，最大坐标为1919,1079
        :param x: x坐标
        :param y: y坐标
        :return: 是否在屏幕内
        """
        size = self.get_screen_size()
        if size is None:
            return False

        width, height = size
        return 0 <= x < width and 0 <= y < height

    async def long_press(self, x: int, y: int, duration_ms: int) -> None:
        """
        长按屏幕上的某个位置
        :param x: x坐标
        :param y: y坐标
        :param duration: 长按持续时间（秒）
        """
        if not self._check_in_screen(x, y):
            logger.error(f"坐标({x},{y})不在屏幕内，拒绝执行")
            return

        async with self._mutex:
            if self.control_handle is None:
                logger.warning("设备未连接，无法执行长按操作")
                return

            await self.control_handle.send_gesture_event(x, y, GestureAction.DOWN.value)
            await asyncio.sleep(float(duration_ms) / 1000)
            await self.control_handle.send_gesture_event(x, y, GestureAction.UP.value)

    async def click(self, x: int, y: int) -> None:
        """
        单击屏幕上的某个位置
        :param x: x坐标
        :param y: y坐标
        """
        # 相当于一个时间很短的"long_press"
        await self.long_press(x, y, random.randint(150, 220))

    async def double_click(self, x: int, y: int) -> None:
        """
        双击屏幕上的某个位置
        :param x: x坐标
        :param y: y坐标
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
            await random_sleep_ms(80, 150)
            await self.control_handle.send_event(bytes([0x04, GestureAction.UP.value]))

    async def swipe(self, x1: int, y1: int, x2: int, y2: int) -> None:
        """
        一个简化的方便版本，用于执行一个简单的滑动操作。
        里面用到了一些插帧操作，确保滑动过程中尽量丝滑。
        :param x1: 滑动开始的x坐标
        :param y1: 滑动开始的y坐标
        :param x2: 滑动结束的x坐标
        :param y2: 滑动结束的y坐标
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
        """
        执行一系列的手势操作，用于模拟复杂的手指操作，比如玩游戏或者拖拽等等。
        正常情况下，list开头的操作一定是一个DOWN，结尾的操作一定是一个UP，毕竟手指肯定会按下然后抬起。
        除非是在处理游戏或者某些需要打破这个常规的操作，所以默认会检查这个条件，不符合条件的会拒绝执行。
        你如果很确定自己在干什么，可以将check参数设为False来跳过这个检查。
        假如你DOWN之后没有UP，将会导致手机认为一直有一根手指放在某个地方，所以check=False是一个非常危险的操作！

        :param actions: 要执行的手势操作列表，每一个节点表示，在某个坐标处执行某个操作，然后等待duration_ms后继续下一个操作。
        每个节点（除了最后一个）的duration_ms都不建议设为0，因为这样会导致操作之间的间隔过短，手机可能会无法响应高速的操作。
        最后一个节点的duration_ms不会被忽略，但是建议你填0，除非你认为最后等待若干毫秒是有必要的。
        每个节点的duration_ms都不能大于10000（10秒），否则会被拒绝执行。（check=False时例外）
        """
        if len(actions) == 0:
            return

        for action in actions:
            # 检查有没有坐标越界
            if not self._check_in_screen(action.x, action.y):
                logger.error(f"坐标({action.x},{action.y})不在屏幕内，拒绝执行")
                return

        if check:
            if len(actions) < 2:
                logger.error("手势操作列表长度太短，拒绝执行")
                return

            for action in actions:
                # 检查duration_ms是否符合要求
                if action.duration_ms > 10000 or action.duration_ms < 0:
                    logger.error(f"节点的duration_ms异常，拒绝执行")
                    return

            # 跑一下状态机检测一下手指的状态有没有不合理的
            state = GestureAction.UP
            for node in actions:
                match node.action:
                    case GestureAction.DOWN:
                        if state != GestureAction.UP:
                            logger.error("DOWN操作准备条件异常，拒绝执行")
                            return
                        state = GestureAction.DOWN
                    case GestureAction.MOVE:
                        if state == GestureAction.UP:
                            logger.error("MOVE操作不能在UP状态下执行，拒绝执行")
                            return
                        state = GestureAction.MOVE
                    case GestureAction.UP:
                        if state == GestureAction.UP:
                            logger.error("UP操作不能在UP状态下执行，拒绝执行")
                            return
                        state = GestureAction.UP
                    case _:
                        logger.error(f"未知的手势操作{node.action}，拒绝执行")
                        return

        async with self._mutex:
            if self.control_handle is None:
                logger.warning("设备未连接，无法执行长按操作")
                return

            # 发送事件
            for node in actions:
                await self.control_handle.send_gesture_event(
                    node.x, node.y, node.action.value
                )

                if node.duration_ms > 0:
                    # 添加一点扰动避免检测
                    await random_sleep_ms(
                        min(1, node.duration_ms - 10), node.duration_ms + 10
                    )

    async def paste(self, text: str) -> None:
        """
        粘贴文本到设备。执行这个操作时，需要先将设备焦点切换到文本输入框，否则是无效的
        :param text: 要粘贴的文本
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
