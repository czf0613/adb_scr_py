import asyncio
from typing import TYPE_CHECKING, final
from ..logger import logger
from .bin_utils import (
    to_u16_be,
    to_u32_be,
    decode_lossy_utf8,
    from_u32_be,
    decode_frame_header,
)
from .tcp_forward_tunnel import setup_tunnel
from ..media_ext import create_h264_decoder, H264DecoderBase
from asyncio import Lock, IncompleteReadError
from .types import GestureAction

__all__ = []


@final
class DeviceControlHandle:
    if TYPE_CHECKING:
        serial: str
        # 手机上的uds名称
        uds_name: str
        screen_width: int
        screen_height: int
        running: bool
        async_tasks: list[asyncio.Task]

        # 用于传输视频流的socket
        video_socket_reader: asyncio.StreamReader | None
        video_socket_writer: asyncio.StreamWriter | None
        # 用于传输控制流的socket
        control_socket_reader: asyncio.StreamReader | None
        control_socket_writer: asyncio.StreamWriter | None

        # 视频解码器
        h264_decoder: H264DecoderBase | None
        _mutex: Lock

    def __init__(self, serial: str, scid: str) -> None:
        self.serial = serial
        self.uds_name = f"localabstract:scrcpy_{scid}"
        self.screen_width = 0
        self.screen_height = 0
        self.running = False
        self.async_tasks = []

        self.video_socket_reader = None
        self.video_socket_writer = None
        self.control_socket_reader = None
        self.control_socket_writer = None

        self.h264_decoder = None
        self._mutex = Lock()

    async def connect_sockets(self) -> bool:
        """
        连接设备的控制socket
        :return: 如果连接成功，则返回True；否则返回False
        """
        if self.running:
            logger.warning("设备已连接，无需重复连接")
            return True

        # 连接视频socket
        video_socket = await setup_tunnel(self.serial, self.uds_name)
        if video_socket is None:
            logger.error(f"连接设备{self.serial}的视频socket失败")
            return False

        self.video_socket_reader, self.video_socket_writer = video_socket
        video_task = asyncio.create_task(self.process_video_upstream())

        # 连接控制socket
        control_socket = await setup_tunnel(
            self.serial, self.uds_name.replace("video", "control")
        )
        if control_socket is None:
            logger.error(f"连接设备{self.serial}的控制socket失败")
            # 关闭视频socket
            self.video_socket_writer.close()
            self.video_socket_writer = None
            self.video_socket_reader = None
            video_task.cancel()
            return False

        self.control_socket_reader, self.control_socket_writer = control_socket
        control_task = asyncio.create_task(self.process_control_upstream())

        self.running = True
        self.async_tasks.append(video_task)
        self.async_tasks.append(control_task)
        return True

    async def process_video_upstream(self) -> None:
        try:
            # 1字节的确认包
            await self.video_socket_reader.readexactly(1)
            # 64字节的设备名称
            device_name_data = await self.video_socket_reader.readexactly(64)
            logger.info(f"设备名称: {decode_lossy_utf8(device_name_data)}")
            # 12字节，包含编码器ID，屏幕宽度和高度
            codec_id = from_u32_be(await self.video_socket_reader.readexactly(4))
            self.screen_width = from_u32_be(
                await self.video_socket_reader.readexactly(4)
            )
            self.screen_height = from_u32_be(
                await self.video_socket_reader.readexactly(4)
            )
            logger.info(
                f"编码器ID: 0x{codec_id:x}, 屏幕宽度: {self.screen_width}, 屏幕高度: {self.screen_height}"
            )

            # 接下来是每一帧的数据了，每个数据块的前4字节是数据长度
            while self.running and self.video_socket_reader is not None:
                frame_header = decode_frame_header(
                    await self.video_socket_reader.readexactly(8)
                )
                frame_size = from_u32_be(await self.video_socket_reader.readexactly(4))
                frame_data = await self.video_socket_reader.readexactly(frame_size)

                if frame_header.config_flag:
                    # 收到sps/pps，开始重建编码器
                    async with self._mutex:
                        if self.h264_decoder is not None:
                            await self.h264_decoder.close_decoder()
                        new_decoder = create_h264_decoder(frame_data)
                        if new_decoder is None:
                            logger.error("创建H264解码器失败")
                            break

                        self.h264_decoder = new_decoder
                        self.screen_width = self.h264_decoder.width
                        self.screen_height = self.h264_decoder.height

                    logger.info(
                        f"解码器创建成功，屏幕宽度: {self.screen_width}, 屏幕高度: {self.screen_height}"
                    )
                else:
                    # 直接进行解码
                    success = False
                    async with self._mutex:
                        if self.h264_decoder is not None:
                            success = self.h264_decoder.enqueue_frame(
                                frame_header.key_flag, frame_data, frame_header.pts
                            )

                    if not success:
                        logger.error("解码帧失败")
                        break

        except IncompleteReadError:
            # 这个没问题，因为要关闭了
            pass
        except Exception as e:
            logger.error(f"读取视频流时发生异常：{e}")
        finally:
            self.running = False
            logger.info("视频流已退出")

    async def process_control_upstream(self) -> None:
        try:
            # 这个比较简单，没啥数据有价值，读出来扔掉
            while self.running and self.control_socket_reader is not None:
                await self.control_socket_reader.read()
        except Exception:
            pass
        finally:
            self.running = False
            logger.info("控制流已退出")

    async def disconnect_sockets(self) -> None:
        # 先优雅退出
        self.running = False
        await asyncio.sleep(0.5)

        # 关闭视频socket
        if self.video_socket_writer is not None:
            self.video_socket_writer.close()
            self.video_socket_writer = None
        self.video_socket_reader = None

        # 关闭控制socket
        if self.control_socket_writer is not None:
            self.control_socket_writer.close()
            self.control_socket_writer = None
        self.control_socket_reader = None

        for task in self.async_tasks:
            task.cancel()
        self.async_tasks.clear()
        await asyncio.sleep(0.5)

        # 释放编码器
        async with self._mutex:
            if self.h264_decoder is not None:
                await self.h264_decoder.close_decoder()
                self.h264_decoder = None

    async def get_current_frame(self) -> tuple[int, int, bytes] | None:
        """
        获取当前视频帧的BGRA数据
        :return: 如果获取成功，则返回一个元组，包含帧的宽度、高度和像素数据；否则返回None
        """
        if not self.running:
            logger.warning("设备未连接，无法获取视频帧")
            return None

        # 这个要加锁操作
        async with self._mutex:
            if self.h264_decoder is None:
                return None

            return await self.h264_decoder.get_current_frame_bgra8()

    async def send_event(self, data: bytes) -> bool:
        """
        发送事件到设备，这是一个通用方法，组装好请求体就能发生
        :param data: 事件数据
        :return: 如果发送成功，则返回True；否则返回False
        """
        if not self.running or self.control_socket_writer is None:
            logger.warning("控制socket未连接，无法发送事件")
            return False

        try:
            # 写入，然后立即冲刷
            self.control_socket_writer.write(data)
            await self.control_socket_writer.drain()
            return True
        except Exception as e:
            logger.error(f"发送事件到设备失败：{e}")
            return False

    async def send_gesture_event(self, x: int, y: int, action: int) -> bool:
        """
        发送手势事件到设备（这个方法太常用了，所以单独拎出来）
        :param x: 事件坐标x
        :param y: 事件坐标y
        :param action: 事件动作，取值来自于GestureAction的枚举值
        :return: 如果发送成功，则返回True；否则返回False
        """
        # 组装字节数组，前面的东西是固定的
        data = bytes([0x02, action, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFD])
        data += to_u32_be(x)
        data += to_u32_be(y)
        data += to_u16_be(self.screen_width)
        data += to_u16_be(self.screen_height)

        # 压力值，只在抬起时为0
        if action == GestureAction.UP.value:
            data += to_u16_be(0)
        else:
            data += to_u16_be(0xFFFF)

        # 对齐
        data += to_u32_be(0)
        data += to_u32_be(0)

        return await self.send_event(data)
