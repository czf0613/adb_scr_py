from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .._adb_scr_media import RecordingHandle

__all__ = []


class H264DecoderBase(ABC):
    """内部解码器接口。

    Notes:
        调用方必须协调入队、取帧、替换和关闭的生命周期。控制句柄通过
        asyncio.Lock 提供同一事件循环内的保护；不要跨事件循环共享实例。
    """

    if TYPE_CHECKING:
        # 该解码器是否还有效
        valid: bool
        width: int
        height: int

    def __init__(self) -> None:
        """初始化内部解码器状态；子类还需要创建原生句柄。

        Raises:
            AdbScrPyH264DecoderException: 如果初始化失败，则抛出此异常
        """
        self.valid = False
        self.width = 0
        self.height = 0

    @abstractmethod
    async def close_decoder(self) -> None:
        """
        关闭解码器句柄
        """
        pass

    @abstractmethod
    def enqueue_frame(self, is_idr: bool, nalu: bytes, pts: int) -> bool:
        """快速提交一帧 H.264 数据供异步解码。

        Args:
            is_idr: 是否为 IDR 关键帧。
            nalu: 以 Annex B 起始码分隔的 H.264 数据。
            pts: 显示时间戳，单位为微秒。

        Returns:
            成功接收或因首个 IDR 尚未到达而跳过时为 True，失败时为 False。
            True 不表示解码已完成。
        """
        pass

    @abstractmethod
    async def get_current_frame_bgra8(self) -> tuple[int, int, bytes] | None:
        """获取当前视频帧的BGRA8数据。

        Returns:
            如果获取成功，则返回一个元组，包含视频的宽度、高度和BGRA8格式的视频帧数据；否则返回None
        """
        pass

    @abstractmethod
    async def get_current_frame_jpg(
        self,
        quality: int = 75,
        scale: float = 1.0,
        roi: tuple[int, int, int, int] | None = None,
    ) -> bytes | None:
        """从最新原生帧直接编码 JPEG；ROI 以原图左上角为原点，先裁剪再缩放。"""

    @abstractmethod
    async def start_recording(
        self, output_file: str, audio_config: bytes | None, source_pts: int, fps: int,
        quality: float = 0.75,
    ) -> "RecordingHandle":
        """从当前原生帧开始录制并订阅后续解码输出，返回原生录制句柄。"""

    @abstractmethod
    async def set_recording(self, recording: "RecordingHandle | None") -> None:
        """在解码器替换或关闭前解绑录制，或将现有录制绑定到新解码器。"""
