"""连接和存活探测的公开配置。"""

import math
from dataclasses import dataclass

__all__ = []


@dataclass(frozen=True)
class ConnectionOptions:
    """会话超时配置，时间单位均为秒。

    Args:
        connect_timeout: 单次连接总预算，默认 30 秒，清理可能额外耗时。
        io_timeout: 握手、媒体初始配置、半包接收、控制写入及 API 级别
            查询/探测的超时，默认 5 秒。
        close_timeout: 流关闭、子进程回收的等待上限，默认 5 秒。
        probe_interval: 设备探测间隔，默认 5 秒；None 禁用主动探测。
        probe_failures: 连续失败达到该次数后断开，默认 3 次。

    Raises:
        ValueError: 时间不是有限正数，或失败阈值不是正整数。
    """

    connect_timeout: float = 30.0
    io_timeout: float = 5.0
    close_timeout: float = 5.0
    probe_interval: float | None = 5.0
    probe_failures: int = 3

    def __post_init__(self) -> None:
        for value in (
            self.connect_timeout,
            self.io_timeout,
            self.close_timeout,
            *(() if self.probe_interval is None else (self.probe_interval,)),
        ):
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or value <= 0
            ):
                raise ValueError("超时和探测间隔必须为有限正数")
        if type(self.probe_failures) is not int or self.probe_failures < 1:
            raise ValueError("probe_failures 必须为正整数")
