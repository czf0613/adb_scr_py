from typing import final

def bgra8_to_jpg(width: int, height: int, bgra8: bytes, quality: int) -> bytes | None:
    """
    将BGRA8格式的图像数据转换为JPG格式
    :param width: 图像宽度
    :param height: 图像高度
    :param bgra8: BGRA8格式的图像数据
    :param quality: JPG质量，1到100之间的整数
    :return: 如果转换成功，则返回JPG格式的图像数据；否则返回None
    """
    pass

@final
class DecoderHandle:
    """解码器句柄，是一个不透明类型，python侧无需知道它的具体实现"""

    pass

def create_decoder(sps_and_pps: bytes) -> tuple[int, int, DecoderHandle] | None:
    """
    创建一个解码器句柄，顺带获取视频尺寸（在sps和pps里面已经包含了）
    :param sps_and_pps: SPS和PPS参数，用于初始化解码器(0x00 0x00 0x00 0x01开头的格式)
    :return: 如果创建成功，则返回一个元组，包含视频的宽度、高度和解码器句柄；否则返回None
    """
    pass

def enqueue_frame(handle: DecoderHandle, nalu: bytes, pts: int) -> bool:
    """
    向解码器句柄队列中入队一帧NALU数据
    :param handle: 解码器句柄
    :param nalu: NALU数据(0x00 0x00 0x00 0x01开头的格式)
    :param pts: 帧的PTS值，看具体解码器的约定
    :return: 如果入队成功，则返回True；否则返回False
    """
    pass

def get_current_frame_bgra8(handle: DecoderHandle) -> tuple[int, int, bytes] | None:
    """
    获取当前视频帧的BGRA8数据。这个方法可能会被阻塞（被解码器内部的GCD调度）
    所以必须要用asyncio.to_thread调用，避免卡死执行器
    :param handle: 解码器句柄
    :return: 如果获取成功，则返回一个元组，包含视频的宽度、高度和BGRA8格式的视频帧数据；否则返回None
    """
    pass

def destroy_decoder(handle: DecoderHandle) -> None:
    """
    销毁解码器句柄
    跟get_current_frame_bgra8一样的问题，也必须要用asyncio.to_thread调用，避免卡死执行器
    :param handle: 解码器句柄
    """
    pass
