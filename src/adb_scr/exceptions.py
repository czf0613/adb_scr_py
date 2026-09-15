__all__ = ["AdbScrPyException", "AdbScrPyInitException", "AdbScrPyH264DecoderException",
           "MediaPipelineOverloadedError"]


class AdbScrPyException(Exception):
    """
    所有异常的基类。
    """

    pass


class MediaPipelineOverloadedError(AdbScrPyException, RuntimeError):
    """媒体队列超过数量、内存或等待时间预算，当前管线已失败。"""


class AdbScrPyInitException(AdbScrPyException):
    """
    初始化异常。
    """

    pass


class AdbScrPyH264DecoderException(AdbScrPyException):
    """
    H264解码器异常。
    """
