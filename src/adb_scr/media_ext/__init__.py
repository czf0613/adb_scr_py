from ._adb_scr_media import bgra8_to_jpg
from .h264.decoder_base import H264DecoderBase
import sys
from adb_scr.logger import logger
from adb_scr.exceptions import AdbScrPyH264DecoderException

__all__ = ["bgra8_to_jpg", "H264DecoderBase", "create_h264_decoder"]


def create_h264_decoder(sps_and_pps: bytes) -> H264DecoderBase | None:
    """
    创建H264解码器，这个函数会根据平台自动选择合适的解码器实现
    :param sps_and_pps: SPS和PPS参数，用于初始化解码器(0x00 0x00 0x00 0x01开头的格式)
    """
    match sys.platform:
        case "darwin":
            from .h264.vtb_decoder import VtbH264Decoder

            try:
                return VtbH264Decoder(sps_and_pps)
            except AdbScrPyH264DecoderException:
                logger.error("创建VtbH264Decoder失败")
                return None
        case _:
            logger.error(f"当前平台{sys.platform}，未实现H264解码器")
            return None
