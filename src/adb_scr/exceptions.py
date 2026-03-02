__all__ = ["AdbScrPyException", "AdbScrPyInitException", "AdbScrPyH264DecoderException"]


class AdbScrPyException(Exception):
    """
    所有异常的基类。
    """

    pass


class AdbScrPyInitException(AdbScrPyException):
    """
    初始化异常。
    """

    pass


class AdbScrPyH264DecoderException(AdbScrPyException):
    """
    H264解码器异常。
    """
