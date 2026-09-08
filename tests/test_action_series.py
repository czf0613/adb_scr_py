"""Gesture validation through the real control encoder, without ADB or a phone."""

import asyncio
from types import SimpleNamespace

import pytest

from adb_scr import AndroidDevice, GestureAction, GestureActionNode
from adb_scr.device import android_device as module
from adb_scr.device.control_handle import DeviceControlHandle


class RecordingWriter:
    def __init__(self):
        self.packets = []

    def write(self, data):
        self.packets.append(data)

    async def drain(self):
        pass


def connected_device():
    device = AndroidDevice("synthetic", "usb")
    handle = DeviceControlHandle("synthetic", device.scid)
    handle.running = True
    handle.screen_width = handle.screen_height = 64
    writer = RecordingWriter()
    handle.control_socket_writer = writer
    device.control_handle = handle
    device.scrcpy_server_process = SimpleNamespace(returncode=None)
    return device, writer


def nodes(sequence):
    actions = {"D": GestureAction.DOWN, "M": GestureAction.MOVE, "U": GestureAction.UP}
    return [
        GestureActionNode(i + 1, i + 2, actions[action], 0)
        for i, action in enumerate(sequence)
    ]


def pointer_nodes(sequence):
    actions = {"D": GestureAction.DOWN, "M": GestureAction.MOVE, "U": GestureAction.UP}
    return [
        GestureActionNode(i + 1, i + 2, actions[action], 0, pointer_id=pointer_id)
        for i, (action, pointer_id) in enumerate(sequence)
    ]


def run_actions(actions, *, check=True):
    async def run():
        device, writer = connected_device()
        assert await device.action_series(actions, check=check) is None
        return writer.packets

    return asyncio.run(run())


@pytest.mark.parametrize(
    "sequence, expected",
    [
        ("DU", [0, 1]),
        ("DMU", [0, 2, 1]),
        ("DMMU", [0, 2, 2, 1]),
        ("DUDU", [0, 1, 0, 1]),
        ("DMUDMMUDU", [0, 2, 1, 0, 2, 2, 1, 0, 1]),
    ],
)
def test_complete_gesture_groups_preserve_order_and_coordinates(sequence, expected):
    packets = run_actions(nodes(sequence))
    assert [packet[1] for packet in packets] == expected
    assert all(packet[2:10] == b"\0" * 8 for packet in packets)
    assert [int.from_bytes(packet[10:14], "big") for packet in packets] == list(
        range(1, len(expected) + 1)
    )
    assert [int.from_bytes(packet[14:18], "big") for packet in packets] == list(
        range(2, len(expected) + 2)
    )


@pytest.mark.parametrize(
    "sequence",
    [
        [
            ("D", 0),
            ("D", 1),
            ("D", 2),
            ("D", 3),
            ("U", 0),
            ("U", 2),
            ("U", 1),
            ("U", 3),
        ],
        [
            ("D", 10),
            ("D", 20),
            ("M", 10),
            ("M", 20),
            ("U", 10),
            ("D", 10),
            ("U", 20),
            ("M", 10),
            ("U", 10),
            ("D", 20),
            ("U", 20),
        ],
    ],
    ids=["four-fingers-release-out-of-order", "interleaved-moves-and-reused-ids"],
)
def test_multi_touch_preserves_pointer_identity(sequence):
    packets = run_actions(pointer_nodes(sequence))
    decoded = [(packet[1], int.from_bytes(packet[2:10], "big")) for packet in packets]
    wire_actions = {"D": 0, "M": 2, "U": 1}
    assert decoded == [(wire_actions[action], pid) for action, pid in sequence]


@pytest.mark.parametrize(
    "sequence",
    [
        [("D", 0), ("D", 1), ("U", 0)],
        [("D", 0), ("D", 1), ("U", 0), ("U", 0)],
        [("D", 0), ("M", 1), ("U", 0)],
        [("D", 0), ("U", 1)],
        [("D", 0), ("D", 0), ("U", 0), ("U", 0)],
        [("D", 0), ("D", 1), ("U", 0), ("M", 0), ("U", 1)],
    ],
    ids=[
        "missing-up",
        "equal-counts-wrong-id",
        "move-before-down",
        "up-before-down",
        "duplicate-down",
        "move-after-up",
    ],
)
def test_invalid_pointer_lifecycle_sends_nothing(sequence):
    assert run_actions(pointer_nodes(sequence)) == []


@pytest.mark.parametrize("pointer_id", [0, 256, (1 << 63) - 1])
def test_pointer_id_is_encoded_as_eight_big_endian_bytes(pointer_id):
    packets = run_actions(pointer_nodes([("D", pointer_id), ("U", pointer_id)]))
    expected = pointer_id.to_bytes(8, "big")
    assert len(packets) == 2
    assert all(len(packet) == 32 and packet[2:10] == expected for packet in packets)


@pytest.mark.parametrize("check", [True, False])
@pytest.mark.parametrize("pointer_id", [-1, 1 << 63, True, 0.5, "0", None, []])
def test_invalid_pointer_id_rejects_all_events_even_with_check_false(pointer_id, check):
    actions = nodes("DUDU")
    actions[-1].pointer_id = pointer_id
    assert run_actions(actions, check=check) == []


@pytest.mark.parametrize("count", [10, 11])
def test_concurrent_pointer_limit_is_checked_before_sending(count):
    sequence = [("D", i) for i in range(count)] + [("U", i) for i in range(count)]
    packets = run_actions(pointer_nodes(sequence))
    assert len(packets) == (20 if count == 10 else 0)


def test_pointer_limit_counts_active_fingers_not_total_ids():
    sequence = [(action, i) for i in range(11) for action in ("D", "U")]
    assert len(run_actions(pointer_nodes(sequence))) == 22


def test_check_false_forwards_explicit_pointer_ids_without_state_checks():
    packets = run_actions(pointer_nodes([("M", 7), ("U", 9)]), check=False)
    assert [(p[1], int.from_bytes(p[2:10], "big")) for p in packets] == [(2, 7), (1, 9)]


@pytest.mark.parametrize(
    "sequence",
    [
        "D",  # Incomplete single press.
        "DM",  # Missing final release.
        "DMM",  # Repeated MOVE still leaves the finger down.
        "DUD",  # A complete first group cannot hide an incomplete second one.
        "DUDM",
        "MDU",  # MOVE before any DOWN.
        "UDU",  # UP before any DOWN.
        "DUMU",  # MOVE after release.
        "DDU",  # DOWN while already pressed.
        "DMDU",  # MOVE does not release the finger.
        "DUU",  # Duplicate UP.
        "DUDUUMDU",
    ],
)
def test_invalid_sequence_sends_nothing_including_valid_prefix(sequence, caplog):
    assert run_actions(nodes(sequence)) == []
    assert "拒绝执行" in caplog.text


@pytest.mark.parametrize(
    "field, value",
    [("x", 64), ("y", -1), ("duration_ms", -1), ("duration_ms", 10001), ("action", 99)],
)
def test_invalid_later_node_rejects_entire_sequence(field, value):
    actions = nodes("DUDU")
    setattr(actions[-1], field, value)
    assert run_actions(actions) == []


@pytest.mark.parametrize(
    "sequence, expected",
    [("D", [0]), ("DM", [0, 2]), ("M", [2]), ("DUMU", [0, 1, 2, 1])],
)
def test_check_false_preserves_unchecked_state_sequences(sequence, expected):
    assert [p[1] for p in run_actions(nodes(sequence), check=False)] == expected


def test_check_false_still_checks_all_coordinates():
    actions = nodes("DU")
    actions[-1].x = 64
    assert run_actions(actions, check=False) == []


@pytest.mark.parametrize("check", [True, False])
def test_empty_sequence_is_a_noop(check):
    assert run_actions([], check=check) == []


def test_long_press_and_action_series_share_the_default_pointer():
    async def run():
        device, writer = connected_device()
        await device.long_press(10, 10, 0)
        await device.action_series(nodes("DU"))
        return writer.packets

    packets = asyncio.run(run())
    assert [(p[1], int.from_bytes(p[2:10], "big")) for p in packets] == [
        (0, 0),
        (1, 0),
        (0, 0),
        (1, 0),
    ]


@pytest.mark.parametrize("end", [(10, 10), (11, 10), (60, 60)])
def test_swipe_produces_a_complete_valid_group(monkeypatch, end):
    async def no_delay(*args):
        pass

    monkeypatch.setattr(module, "random_sleep_ms", no_delay)

    async def run():
        device, writer = connected_device()
        await device.swipe(10, 10, *end)
        return writer.packets

    packets = asyncio.run(run())
    assert packets[0][1] == 0
    assert packets[-1][1] == 1
    assert all(packet[2:10] == b"\0" * 8 for packet in packets)
    assert all(packet[1] == 2 for packet in packets[1:-1])
    assert int.from_bytes(packets[-1][10:14], "big") == end[0]
    assert int.from_bytes(packets[-1][14:18], "big") == end[1]
