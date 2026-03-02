import asyncio
import os
from adb_scr import (
    init_lib,
    deinit_lib,
    list_devices,
    AndroidDevice,
    GestureAction,
    GestureActionNode,
)
from adb_scr.logger import logger
from aiofiles import open as aio_open


async def run(iterations: int = 3600):
    # 初始化
    v_adb, v_scrcpy = await init_lib()
    logger.info(f"ADB版本：{v_adb}，scrcpy版本：{v_scrcpy}")

    # from adb_scr.adb_cmd.base import adb_connect, adb_disconnect

    # # 连接设备，这个铁定是失败的
    # ret = await adb_connect("192.168.1.100:5555")
    # assert ret != 0
    # logger.info(f"连接设备失败，状态码：{ret}")

    # # 断开设备连接
    # await adb_disconnect("192.168.1.100:5555")
    # logger.info("已断开设备连接")

    # 枚举设备
    devices = await list_devices()
    logger.info(f"当前连接的ADB设备列表：{devices}")

    if len(devices) == 0:
        logger.warning("没有连接的ADB设备")
    else:
        # 连接设备，并开始控制
        device = AndroidDevice(devices[0], "usb")
        assert await device.connect()

        # 试一下滚动
        await asyncio.sleep(3)
        await device.swipe(540, 2000, 540, 1000)

        # 测试长时间采图稳定性
        for i in range(iterations):
            jpg_data = await device.get_screenshot_jpg()
            assert jpg_data is not None
            logger.info(f"获取到的第{i+1}张截图，大小：{len(jpg_data)}")

            async with aio_open(
                f'{os.path.join(os.path.dirname(__file__), '__pycache__',f"test_{i%10}.jpg")}',
                "wb",
            ) as f:
                await f.write(jpg_data)

            await asyncio.sleep(1)
            i += 1

        # 断开设备
        await device.disconnect()
        logger.info("已断开设备连接")

    # 清理
    await deinit_lib()
    logger.info("已清理库")


def test_run():
    iterations = int(input("请输入测试迭代次数："))
    asyncio.run(run(iterations))
