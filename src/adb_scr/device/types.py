from dataclasses import dataclass
from typing import Literal, final
from enum import IntEnum

__all__ = []

# 设备的连接类型，目前只支持tcp和usb
type ConnectionType = Literal["tcp", "usb"]


# 手势操作类型
class GestureAction(IntEnum):
    DOWN = 0
    UP = 1
    MOVE = 2


@final
@dataclass
class GestureActionNode:
    """
    一个手势操作节点，用于描述一个手势操作的具体细节。
    """

    x: int
    y: int
    action: GestureAction
    # 相当于发送这个指令之后等待的时间
    duration_ms: int = 50
