import asyncio
from asyncio import IncompleteReadError, Lock
from typing import TYPE_CHECKING, final

from ..async_utils import close_writer, complete_on_cancel
from ..logger import logger
from ..media_ext import H264DecoderBase, create_h264_decoder
from .bin_utils import (
    decode_frame_header,
    decode_lossy_utf8,
    from_u32_be,
    to_u16_be,
    to_u32_be,
)
from .options import ConnectionOptions
from .tcp_forward_tunnel import setup_tunnel
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

    def __init__(
        self, serial: str, scid: str, *, options: ConnectionOptions | None = None
    ) -> None:
        self.options = options or ConnectionOptions()
        self.disconnect_reason: str | None = None
        self._ready = asyncio.Event()
        self._closed = asyncio.Event()
        self._stopped = asyncio.Event()
        self._close_task: asyncio.Task | None = None
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
        """按视频、控制顺序连接，等待视频元数据；失败或取消时完整回滚。"""
        if self.running:
            return True
        if self._close_task is not None:
            return False  # Each handle belongs to exactly one session.
        try:
            video = await setup_tunnel(
                self.serial,
                self.uds_name,
                timeout=self.options.io_timeout,
                close_timeout=self.options.close_timeout,
            )
            if video is None:
                raise ConnectionError("视频隧道建立失败")
            self.video_socket_reader, self.video_socket_writer = video
            control = await setup_tunnel(
                self.serial,
                self.uds_name,
                timeout=self.options.io_timeout,
                close_timeout=self.options.close_timeout,
            )
            if control is None:
                raise ConnectionError("控制隧道建立失败")
            self.control_socket_reader, self.control_socket_writer = control
            self.running = True
            self.async_tasks = [
                asyncio.create_task(self.process_video_upstream()),
                asyncio.create_task(self.process_control_upstream()),
            ]
            await asyncio.wait_for(self._ready.wait(), self.options.io_timeout)
            if not self.running:
                raise ConnectionError(self.disconnect_reason)
            return True
        except asyncio.CancelledError:
            await self.disconnect_sockets("连接已取消")
            raise
        except Exception as error:
            await self.disconnect_sockets(f"连接失败：{error!r}")
            return False

    async def _read_metadata(self) -> None:
        reader = self.video_socket_reader
        metadata = await reader.readexactly(77)
        if metadata[0] != 0 or metadata[65:69] != b"h264":
            raise ValueError("无效的视频握手或不支持的编码器")
        self.screen_width = from_u32_be(metadata[69:73])
        self.screen_height = from_u32_be(metadata[73:77])
        if self.screen_width <= 0 or self.screen_height <= 0:
            raise ValueError("无效的屏幕尺寸")
        logger.info(f"设备名称：{decode_lossy_utf8(metadata[1:65])}")

    async def _read_packet(self, first: bytes):
        reader = self.video_socket_reader
        header = first + await reader.readexactly(11)
        size = from_u32_be(header[8:])
        if not 0 < size <= 64 * 1024 * 1024:
            raise ValueError("无效的视频包长度")
        return decode_frame_header(header[:8]), await reader.readexactly(size)

    async def _replace_decoder(self, data: bytes) -> None:
        if self.h264_decoder is not None:
            await self.h264_decoder.close_decoder()
            self.h264_decoder = None
        self.h264_decoder = await asyncio.to_thread(create_h264_decoder, data)
        if self.h264_decoder is None:
            raise RuntimeError("创建 H.264 解码器失败")
        self.screen_width = self.h264_decoder.width
        self.screen_height = self.h264_decoder.height

    async def process_video_upstream(self) -> None:
        reason = "视频流 EOF"
        try:
            await asyncio.wait_for(self._read_metadata(), self.options.io_timeout)
            self._ready.set()
            while self.running:
                # A static screen may legitimately produce no new packets.
                first = await self.video_socket_reader.readexactly(1)
                header, data = await asyncio.wait_for(
                    self._read_packet(first), self.options.io_timeout
                )
                async with self._mutex:
                    if not self.running:
                        break
                    if header.config_flag:
                        # Installing the result is part of the protected operation:
                        # cancellation must not orphan a newly created native handle.
                        await complete_on_cancel(self._replace_decoder(data))
                    elif (
                        self.h264_decoder is None
                        or not self.h264_decoder.enqueue_frame(
                            header.key_flag, data, header.pts
                        )
                    ):
                        raise RuntimeError("解码帧失败")
        except IncompleteReadError:
            pass
        except asyncio.CancelledError:
            reason = "视频接收已取消"
            raise
        except Exception as error:
            reason = f"视频接收失败：{error!r}"
        finally:
            self._stop(reason)
            self._ready.set()

    async def process_control_upstream(self) -> None:
        reason = "控制流 EOF"
        try:
            while self.running and self.control_socket_reader is not None:
                if not await self.control_socket_reader.read(4096):
                    break
        except asyncio.CancelledError:
            reason = "控制接收已取消"
            raise
        except Exception as error:
            reason = f"控制接收失败：{error!r}"
        finally:
            self._stop(reason)

    def _stop(self, reason: str) -> None:
        self.running = False
        self._stopped.set()
        if self._close_task is None:
            self.disconnect_reason = reason
            self._close_task = asyncio.create_task(self._cleanup())

    async def _cleanup(self) -> None:
        try:
            tasks, self.async_tasks = self.async_tasks, []
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            writers = [self.video_socket_writer, self.control_socket_writer]
            self.video_socket_writer = self.control_socket_writer = None
            self.video_socket_reader = self.control_socket_reader = None
            results = await asyncio.gather(
                *(
                    close_writer(writer, self.options.close_timeout)
                    for writer in writers
                    if writer is not None
                ),
                return_exceptions=True,
            )
            for result in results:
                if isinstance(result, BaseException):
                    logger.warning(f"关闭流失败：{result!r}")
            async with self._mutex:
                if self.h264_decoder is not None:
                    await self.h264_decoder.close_decoder()
                    self.h264_decoder = None
            self.screen_width = self.screen_height = 0
        finally:
            self._closed.set()

    async def disconnect_sockets(self, reason: str = "主动断开") -> None:
        """幂等关闭本会话，确认接收任务和解码器退出后返回。"""
        self._stop(reason)
        await complete_on_cancel(self._close_task)

    async def wait_disconnected(self) -> str:
        """等待本句柄清理完成，返回首次断连原因。"""
        await self._closed.wait()
        if self._close_task is not None:
            await asyncio.shield(self._close_task)
        return self.disconnect_reason or "已断开"

    async def get_current_frame(self) -> tuple[int, int, bytes] | None:
        """获取当前视频帧的BGRA数据

        Returns:
            如果获取成功，则返回一个元组，包含帧的宽度、高度和像素数据；否则返回None
        """
        if not self.running:
            logger.warning("设备未连接，无法获取视频帧")
            return None

        # 这个要加锁操作
        async with self._mutex:
            if not self.running or self.h264_decoder is None:
                return None

            return await self.h264_decoder.get_current_frame_bgra8()

    async def get_current_frame_jpg(
        self,
        quality: int = 75,
        scale: float = 1.0,
        roi: tuple[int, int, int, int] | None = None,
    ) -> bytes | None:
        """按需直接编码最新原生帧，取消时等待编码完成后释放解码器锁。"""
        if not self.running:
            return None
        async with self._mutex:
            if not self.running or self.h264_decoder is None:
                return None
            return await self.h264_decoder.get_current_frame_jpg(quality, scale, roi)

    async def send_event(self, data: bytes) -> bool:
        """发送事件到设备，这是一个通用方法，组装好请求体就能发生

        Args:
            data: 事件数据

        Returns:
            如果发送成功，则返回True；否则返回False
        """
        if not self.running or self.control_socket_writer is None:
            logger.warning("控制socket未连接，无法发送事件")
            return False

        try:
            # 写入，然后立即冲刷
            self.control_socket_writer.write(data)
            await asyncio.wait_for(
                self.control_socket_writer.drain(), self.options.io_timeout
            )
            return True
        except Exception as e:
            logger.error(f"发送事件到设备失败：{e}")
            self._stop(f"控制发送失败：{e!r}")
            return False

    async def send_gesture_event(self, x: int, y: int, action: int) -> bool:
        """发送手势事件到设备（这个方法太常用了，所以单独拎出来）

        Args:
            x: 事件坐标x
            y: 事件坐标y
            action: 事件动作，取值来自于GestureAction的枚举值

        Returns:
            如果发送成功，则返回True；否则返回False
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
