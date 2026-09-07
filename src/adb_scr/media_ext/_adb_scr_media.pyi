"""macOS 原生媒体 API。

调用方必须传入符合注解及参数约束的值；此存根不提供运行时类型校验。
句柄操作以原生互斥锁串行化，关闭后读取和入队安全失败。扩展不声明
free-threaded Python 支持。异步调用方应将创建、读取、销毁放到工作线程。
"""

from typing import final

@final
class DecoderHandle:
    """由 create_decoder() 返回的不透明 Capsule，不能直接实例化。

    Notes:
        必须显式销毁。垃圾回收提供释放兜底，但不保证及时释放。
        关闭操作幂等；关闭后的句柄不能再次打开。
    """

def bgra8_to_jpg(width: int, height: int, bgra8: bytes, quality: int) -> bytes | None:
    """将紧密排列的 BGRA8 像素编码为 JPEG。

    Args:
        width: 正整数像素宽度。
        height: 正整数像素高度。
        bgra8: 每像素按 B、G、R、A 顺序排列，至少 width * height * 4 字节。
        quality: 1 到 100 的整数。

    Returns:
        JPEG bytes；缓冲区过短或编码失败时返回 None。

    Notes:
        不应依赖越界 quality 的转换行为。异步调用方应使用工作线程。
    """

def create_decoder(sps_and_pps: bytes) -> tuple[int, int, DecoderHandle] | None:
    """创建要求硬件加速的 H.264 解码器。

    Args:
        sps_and_pps: Annex B 格式的 SPS/PPS，使用四字节起始码。

    Returns:
        (width, height, handle)，尺寸单位为像素；失败时返回 None。

    Notes:
        包含系统调用；异步调用方必须使用 asyncio.to_thread()，并在
        取消时处理已创建资源的所有权。
    """

def enqueue_frame(handle: DecoderHandle, nalu: bytes, pts: int) -> bool:
    """提交视频包供异步解码。

    Args:
        handle: create_decoder() 返回的句柄。
        nalu: Annex B H.264 数据，使用四字节起始码；第一帧应为 IDR。
        pts: 显示时间戳，单位为微秒。

    Returns:
        提交成功为 True；句柄已关闭或提交失败为 False。

    Notes:
        不等待硬件解码完成；与同一句柄的读取、销毁互斥。
    """

def get_current_frame_bgra8(handle: DecoderHandle) -> tuple[int, int, bytes] | None:
    """读取最新帧并转换为紧密排列的 BGRA8。

    Args:
        handle: create_decoder() 返回的句柄。

    Returns:
        (width, height, pixels)，pixels 长度为 width * height * 4，
        内存通道顺序为 B、G、R、A。无帧、关闭或转换失败时返回 None。

    Notes:
        返回独立 bytes，其存活不依赖解码器。多次调用可能返回同一帧。
        同步等待 GCD 并执行转换，异步调用方必须使用 asyncio.to_thread()。
    """

def destroy_decoder(handle: DecoderHandle) -> None:
    """幂等关闭句柄，等待在途操作、VideoToolbox 回调及 GCD 帧队列结束。

    Args:
        handle: create_decoder() 返回的句柄，允许已关闭句柄。

    Notes:
        必须用 asyncio.to_thread() 从异步路径调用。取消等待不终止原生
        工作；调用方应等待工作结束后再释放生命周期保护。没有安全的
        强制销毁超时，不能释放仍被原生任务引用的内存。
    """
