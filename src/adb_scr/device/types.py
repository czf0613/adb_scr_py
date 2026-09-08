from dataclasses import dataclass
from enum import IntEnum
from typing import Literal, TypeAlias, final

__all__ = []

# 设备的连接类型，目前只支持tcp和usb
ConnectionType: TypeAlias = Literal["tcp", "usb"]


# 手势操作类型
class GestureAction(IntEnum):
    DOWN = 0
    UP = 1
    MOVE = 2


@final
@dataclass
class GestureActionNode:
    """一个手指的手势操作节点。

    Args:
        x: 原图左上角为原点的横坐标，单位为像素。
        y: 原图左上角为原点的纵坐标，单位为像素。
        action: DOWN、MOVE 或 UP。
        duration_ms: 发送该节点后的等待时间，单位为毫秒，默认 50。
        pointer_id: 手指标识，默认 0；取值为 0 到 2**63 - 1 的整数。
            同一根手指的 DOWN/MOVE/UP 使用相同 ID，不同手指使用不同 ID。
            UP 后可再次使用该 ID 发起下一组动作。
    """

    x: int
    y: int
    action: GestureAction
    # 相当于发送这个指令之后等待的时间
    duration_ms: int = 50
    pointer_id: int = 0
