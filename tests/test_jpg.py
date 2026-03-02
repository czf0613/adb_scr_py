import os
from adb_scr.media_ext import bgra8_to_jpg
from adb_scr.logger import logger


def test_bgra8_to_jpg():
    # 测试参数
    width = 1024
    height = 1024
    quality = 75

    # 计算 BGRA8 数据大小（每个像素 4 字节）
    data_size = width * height * 4

    # 生成随机的 BGRA8 数据
    bgra8_data = os.urandom(data_size)

    # 调用 C extension 函数
    jpg_data = bgra8_to_jpg(width, height, bgra8_data, quality)
    assert jpg_data is not None, "生成 JPG 数据失败"
    logger.info(f"成功生成 JPG 数据，大小: {len(jpg_data)} 字节")
