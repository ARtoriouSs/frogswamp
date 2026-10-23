import asyncio
import heapq
import itertools

import pytest

from custom_components.cubematrix import connection
from custom_components.cubematrix.artformat import Art, ArtError
from custom_components.cubematrix.effects import EFFECTS
from custom_components.cubematrix.layout import Layout
from custom_components.cubematrix.player import Player
from custom_components.cubematrix.presets import PresetLibrary
from custom_components.cubematrix.protocol import LampError

BLACK = (0, 0, 0)


class FakeTime:
    """Virtual time: sleepers wake only when the test calls advance()"""

    def __init__(self) -> None:
        self.now = 0.0
        self._sleepers: list = []
        self._seq = itertools.count()

    def clock(self) -> float:
        return self.now

    async def sleep(self, delay: float) -> None:
        future = asyncio.get_running_loop().create_future()
        heapq.heappush(self._sleepers, (self.now + max(delay, 0.0), next(self._seq), future))
        await future

    async def advance(self, seconds: float) -> None:
        target = self.now + seconds
        await settle()
        while self._sleepers and self._sleepers[0][0] <= target + 1e-9:
            when, _, future = heapq.heappop(self._sleepers)
            self.now = max(self.now, when)
            if not future.done():
                future.set_result(None)
            await settle()
        self.now = target
        await settle()


async def settle() -> None:
    for _ in range(20):
        await asyncio.sleep(0)


class FakeSink:
    def __init__(self, time: FakeTime) -> None:
        self.time = time
        self.max_fps = 30.0
        self.frames: list[tuple[float, bytes]] = []
        self.ready: list[bool] = []
        self.power_offs = 0
        self.fail = 0

    async def ensure_ready(self, refresh: bool = False) -> None:
        self.ready.append(refresh)

    async def send_frame(self, raw: bytes) -> None:
        if self.fail:
            self.fail -= 1
            raise LampError("lamp is gone")
        self.frames.append((self.time.now, raw))

    async def power_off(self) -> None:
        self.power_offs += 1

    def colours(self, since: float = -1.0) -> list[tuple[int, int, int]]:
        """First pixel colour of each frame sent at or after since"""
        return [tuple(raw[:3]) for at, raw in self.frames if at >= since]

    def last(self) -> tuple[int, int, int]:
        return tuple(self.frames[-1][1][:3])


def solid_art(name, colours, *, fps=5, size=(5, 5), durations=None, spots=None) -> Art:
    width, height = size
    return Art(
        name=name,
        width=width,
        height=height,
        frames=tuple((colour,) * (width * height) for colour in colours),
        durations=durations or (None,) * len(colours),
        fps=fps,
        loop=False,
        spots=spots,
    )


UP = solid_art("arrow_up", [(1, 0, 0), (2, 0, 0), (3, 0, 0), (4, 0, 0), (5, 0, 0)])
DOWN = solid_art("arrow_down", [(0, 1, 0), (0, 2, 0), (0, 3, 0), (0, 4, 0), (0, 5, 0)])
CHECK = solid_art("check", [(0, 0, 200)])
FAST = solid_art("fast", [(n, n, n) for n in range(1, 31)], fps=30)
WIDE = solid_art("wide", [(7, 7, 7)], size=(15, 5))


class Rig:
    def __init__(self, layout: Layout | None = None) -> None:
        self.time = FakeTime()
        self.sink = FakeSink(self.time)
        library = PresetLibrary(
            {art.name: art for art in (UP, DOWN, CHECK, FAST, WIDE)} | EFFECTS, {}, {}
        )
        self.player = Player(
            self.sink,
            layout or Layout(),
            lambda: library,
            sleep=self.time.sleep,
            clock=self.time.clock,
        )


@pytest.fixture
async def rig():
    rig = Rig()
    yield rig
    await rig.player.shutdown()


async def test_turn_on_plays_background(rig):
    player, sink = rig.player, rig.sink
    assert player.power is False
    assert player.background == "fire"
    assert player.playing is None
    await player.turn_on()
    await rig.time.advance(1)
    assert player.power is True
    assert player.playing == "fire"
    # Fire runs at 10 fps
    assert 10 <= len(sink.frames) <= 11


async def test_show_then_restore(rig):
    player, sink = rig.player, rig.sink
    await player.turn_on()
    await rig.time.advance(0.5)
    sink.frames.clear()
    task = await player.show(UP, name="arrow_up")
    await rig.time.advance(0.9)
    assert player.playing == "arrow_up"
    assert sink.colours() == [(1, 0, 0), (2, 0, 0), (3, 0, 0), (4, 0, 0), (5, 0, 0)]
    await rig.time.advance(0.2)
    assert player.playing == "fire"
    assert task.done()


async def test_new_show_replaces_and_restores_background(rig):
    player, sink = rig.player, rig.sink
    await player.turn_on()
    up_task = await player.show(UP, name="arrow_up")
    await rig.time.advance(0.5)
    down_task = await player.show(DOWN, name="arrow_down")
    switched = rig.time.now
    await rig.time.advance(0.5)
    assert player.playing == "arrow_down"
    assert up_task.done()
    assert not down_task.done()
    assert all(colour[0] == 0 for colour in sink.colours(since=switched))
    await rig.time.advance(0.6)
    assert player.playing == "fire"
    assert down_task.done()
    assert sink.colours(since=switched)[:5] == [
        (0, 1, 0), (0, 2, 0), (0, 3, 0), (0, 4, 0), (0, 5, 0)
    ]


async def test_show_muted_when_off(rig):
    player, sink = rig.player, rig.sink
    task = await player.show(UP, name="arrow_up")
    assert task.done()
    await rig.time.advance(2)
    assert sink.frames == []
    assert sink.ready == []
    assert player.playing is None


async def test_background_off_event_then_dark(rig):
    player, sink = rig.player, rig.sink
    await player.turn_on()
    await player.set_background(None)
    await rig.time.advance(0.1)
    assert player.background is None
    assert player.playing is None
    assert sink.last() == BLACK
    await player.show(UP, name="arrow_up")
    await rig.time.advance(0.5)
    assert player.playing == "arrow_up"
    await rig.time.advance(0.6)
    assert player.playing is None
    assert sink.last() == BLACK
    assert sink.power_offs == 0


async def test_then_hold_off_and_preset(rig):
    player, sink = rig.player, rig.sink
    await player.turn_on()

    await player.show(UP, name="arrow_up", then="hold")
    await rig.time.advance(1.0)
    held_at = rig.time.now
    await rig.time.advance(3)
    assert player.playing == "arrow_up"
    assert sink.last() == (5, 0, 0)
    assert sink.colours(since=held_at + 0.01) == []

    await player.show(UP, name="arrow_up", then="off")
    await rig.time.advance(1.1)
    assert player.playing is None
    assert player.background == "fire"
    assert sink.last() == BLACK

    await player.show(UP, name="arrow_up", then="rainbow")
    await rig.time.advance(1.1)
    assert player.playing == "rainbow"
    assert player.background == "fire"
    count = len(sink.frames)
    await rig.time.advance(5)
    assert player.playing == "rainbow"
    assert len(sink.frames) > count

    with pytest.raises(KeyError):
        await player.show(UP, name="arrow_up", then="nope")
    assert player.playing == "rainbow"


async def test_fps_capped_by_sink(rig):
    player, sink = rig.player, rig.sink
    sink.max_fps = 10
    await player.turn_on()
    await rig.time.advance(0.05)
    start = rig.time.now
    await player.show(FAST, name="fast", repeat=None)
    await rig.time.advance(0.999)
    assert len(sink.colours(since=start)) == 10
    start = rig.time.now
    await player.show(FAST, name="fast", repeat=None, fps=60)
    await rig.time.advance(0.999)
    assert len(sink.colours(since=start)) == 10


async def test_fps_override_and_frame_durations(rig):
    player = rig.player
    await player.turn_on()
    task = await player.show(UP, name="arrow_up", fps=10)
    await rig.time.advance(0.5)
    assert task.done()
    slow = solid_art("slow", [(9, 0, 0), (8, 0, 0)], durations=(1.0, None))
    task = await player.show(slow, name="slow")
    await rig.time.advance(1.1)
    assert not task.done()
    await rig.time.advance(0.1)
    assert task.done()


async def test_static_duration(rig):
    player, sink = rig.player, rig.sink
    await player.turn_on()
    start = rig.time.now
    await player.show(CHECK, name="check", duration=2)
    await rig.time.advance(1.9)
    assert player.playing == "check"
    assert sink.colours(since=start) == [(0, 0, 200)]
    await rig.time.advance(0.2)
    assert player.playing == "fire"
    # Default hold is 5 s
    await player.show(CHECK, name="check")
    await rig.time.advance(4.9)
    assert player.playing == "check"
    await rig.time.advance(0.2)
    assert player.playing == "fire"


async def test_repeat_forever_until_stop(rig):
    player = rig.player
    await player.turn_on()
    task = await player.show(UP, name="arrow_up", repeat=None)
    await rig.time.advance(60)
    assert player.playing == "arrow_up"
    assert not task.done()
    await player.stop()
    await settle()
    assert task.done()
    assert player.playing == "fire"


async def test_repeat_and_duration(rig):
    player, sink = rig.player, rig.sink
    await player.turn_on()
    start = rig.time.now
    await player.show(UP, name="arrow_up", repeat=2)
    await rig.time.advance(1.9)
    assert player.playing == "arrow_up"
    assert len(sink.colours(since=start)) == 10
    await rig.time.advance(0.2)
    assert player.playing == "fire"
    # Duration cuts the animation even with loops left
    task = await player.show(UP, name="arrow_up", repeat=3, duration=0.5)
    await rig.time.advance(0.5)
    assert task.done()
    assert player.playing == "fire"


async def test_effect_show_runs_for_duration(rig):
    player = rig.player
    await player.turn_on()
    task = await player.show(EFFECTS["rainbow"], name="rainbow", duration=3)
    await rig.time.advance(2.9)
    assert player.playing == "rainbow"
    await rig.time.advance(0.2)
    assert task.done()
    assert player.playing == "fire"


async def test_fit_error_keeps_current():
    rig = Rig(Layout(("matrix", "matrix", "matrix")))
    player, sink = rig.player, rig.sink
    try:
        await player.turn_on()
        await rig.time.advance(0.5)
        with pytest.raises(ArtError):
            await player.show(UP, name="arrow_up", fit="error")
        assert player.playing == "fire"
        count = len(sink.frames)
        await rig.time.advance(0.5)
        assert len(sink.frames) > count
        await player.show(UP, name="arrow_up", fit="center")
        await rig.time.advance(0.1)
        assert player.playing == "arrow_up"
        await player.show(WIDE, name="wide")
        await rig.time.advance(0.1)
        assert player.playing == "wide"
    finally:
        await player.shutdown()


async def test_fit_error_for_small_layout(rig):
    with pytest.raises(ArtError):
        await rig.player.show(WIDE, name="wide", fit="center")


async def test_ensure_ready_refresh_on_each_show(rig):
    player, sink = rig.player, rig.sink
    await player.turn_on()
    await settle()
    assert True not in sink.ready
    await player.show(UP, name="arrow_up")
    await settle()
    await player.show(DOWN, name="arrow_down")
    await settle()
    assert sink.ready.count(True) == 2


async def test_brightness_scales_frames(rig):
    player, sink = rig.player, rig.sink
    await player.turn_on()
    await player.show(CHECK, name="check", then="hold")
    await rig.time.advance(0.1)
    assert sink.last() == (0, 0, 200)
    await player.set_brightness(128)
    assert player.brightness == 128
    # The held frame goes out again at the new level
    assert sink.last() == (0, 0, 100)
    await player.show(UP, name="arrow_up")
    await rig.time.advance(0.1)
    assert sink.last() == (round(1 * 128 / 255), 0, 0)


async def test_sink_error_keeps_player_alive(rig):
    player, sink = rig.player, rig.sink
    sink.fail = 1
    await player.turn_on()
    await rig.time.advance(4.9)
    assert sink.frames == []
    assert player.playing == "fire"
    await rig.time.advance(0.2)
    assert sink.frames


async def test_sink_error_during_event(rig):
    player, sink = rig.player, rig.sink
    await player.turn_on()
    await settle()
    sink.fail = 1
    task = await player.show(UP, name="arrow_up")
    await settle()
    # The event is dropped, the waiter is released
    assert task.done()
    await rig.time.advance(5.1)
    assert player.playing == "fire"
    assert sink.frames


async def test_wait_task_ends_after_event(rig):
    player = rig.player
    await player.turn_on()
    task = await player.show(UP, name="arrow_up")
    await rig.time.advance(0.99)
    assert not task.done()
    await rig.time.advance(0.01)
    assert task.done()
    await task


async def test_turn_off_powers_off_and_stops(rig):
    player, sink = rig.player, rig.sink
    await player.turn_on()
    task = await player.show(UP, name="arrow_up", repeat=None)
    await rig.time.advance(1)
    await player.turn_off()
    assert sink.power_offs == 1
    assert player.power is False
    assert player.playing is None
    await settle()
    assert task.done()
    count = len(sink.frames)
    await rig.time.advance(5)
    assert len(sink.frames) == count
    # On again: the background comes back
    await player.turn_on()
    await rig.time.advance(0.5)
    assert player.playing == "fire"
    assert len(sink.frames) > count


async def test_set_background(rig):
    player = rig.player
    with pytest.raises(KeyError):
        await player.set_background("nope")
    await player.set_background("rainbow")
    assert player.playing is None
    await player.turn_on()
    await settle()
    assert player.playing == "rainbow"
    await player.set_background("plasma")
    await settle()
    assert player.playing == "plasma"
    # A change during an event waits for the event to end
    await player.show(UP, name="arrow_up")
    await rig.time.advance(0.5)
    await player.set_background("fire")
    await settle()
    assert player.playing == "arrow_up"
    await rig.time.advance(0.6)
    assert player.playing == "fire"
    # A change replaces a "then" preset
    await player.show(UP, name="arrow_up", then="rainbow")
    await rig.time.advance(1.1)
    assert player.playing == "rainbow"
    await player.set_background("plasma")
    await settle()
    assert player.playing == "plasma"


async def test_static_refresh(rig, monkeypatch):
    player, sink = rig.player, rig.sink
    monkeypatch.setattr(connection, "STATIC_REFRESH_S", 2.0)
    await player.turn_on()
    await player.set_background("check")
    await rig.time.advance(0.1)
    start = rig.time.now
    await rig.time.advance(4)
    assert sink.colours(since=start) == [(0, 0, 200), (0, 0, 200)]
    monkeypatch.setattr(connection, "STATIC_REFRESH_S", None)
    await player.set_background(None)
    await rig.time.advance(0.1)
    start = rig.time.now
    await rig.time.advance(10)
    assert sink.colours(since=start) == []


async def test_listeners(rig):
    player = rig.player
    calls = []
    remove = player.add_listener(lambda: calls.append(player.playing))
    await player.turn_on()
    await settle()
    assert calls
    calls.clear()
    await player.show(UP, name="arrow_up")
    await rig.time.advance(1.1)
    assert calls == ["arrow_up", "fire"]
    remove()
    await player.set_background(None)
    assert calls == ["arrow_up", "fire"]


async def test_shutdown_stops_everything(rig):
    player, sink = rig.player, rig.sink
    await player.turn_on()
    waiter = await player.show(UP, name="arrow_up", repeat=None)
    await rig.time.advance(1)
    await player.shutdown()
    await settle()
    assert waiter.done()
    count = len(sink.frames)
    offs = sink.power_offs
    await rig.time.advance(10)
    task = await player.show(UP, name="arrow_up")
    assert task.done()
    await player.turn_on()
    await player.set_background("rainbow")
    await player.set_brightness(10)
    await player.stop()
    await rig.time.advance(10)
    assert len(sink.frames) == count
    assert sink.power_offs == offs
    current = asyncio.current_task()
    assert [task for task in asyncio.all_tasks() if task is not current] == []


async def test_back_to_back_shows_release_waiters(rig):
    player = rig.player
    await player.turn_on()
    # No yield between the calls, the first task never gets to start
    first = await player.show(UP, name="arrow_up")
    second = await player.show(DOWN, name="arrow_down")
    await settle()
    assert first.done()
    assert not second.done()
    assert rig.player.playing == "arrow_down"


async def test_set_background_right_after_show_keeps_show(rig):
    player = rig.player
    await player.turn_on()
    await settle()
    await player.show(UP, name="arrow_up")
    await player.set_background("rainbow")
    await rig.time.advance(0.5)
    assert player.playing == "arrow_up"
    await rig.time.advance(0.6)
    assert player.playing == "rainbow"
