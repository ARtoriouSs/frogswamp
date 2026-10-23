import asyncio
import logging

import pytest

from custom_components.cubematrix import connection
from custom_components.cubematrix import player as player_module
from custom_components.cubematrix.connection import CubeConnection
from custom_components.cubematrix.effects import EFFECTS
from custom_components.cubematrix.layout import Layout
from custom_components.cubematrix.player import Player
from custom_components.cubematrix.presets import PresetLibrary
from custom_components.cubematrix.protocol import (
    LampError,
    LanClient,
    MusicServer,
    command,
)

from .fake_lamp import FakeLamp, free_port

RED_FRAME = bytes([255, 0, 0]) * 25
RED_LEDS = "/wAA" * 25
GREEN_FRAME = bytes([0, 255, 0]) * 25
GREEN_LEDS = "AP8A" * 25


@pytest.fixture
async def lamp(loopback):
    fake = FakeLamp("127.0.0.2")
    await fake.start()
    yield fake
    await fake.stop()


@pytest.fixture
async def make_conn():
    made = []

    def make(fake, **kwargs):
        conn = CubeConnection(fake.ip, 0, lan_port=fake.port, **kwargs)
        made.append(conn)
        return conn

    yield make
    for conn in made:
        await conn.close()


async def wait_until(check, timeout=2.0):
    async with asyncio.timeout(timeout):
        while not check():
            await asyncio.sleep(0.01)


def test_command_is_a_json_line():
    assert command(3, "set_power", ["on"]) == (
        b'{"id":3,"method":"set_power","params":["on"]}\r\n'
    )


async def test_ready_sets_power_direct_and_music(lamp, make_conn):
    conn = make_conn(lamp)
    changes = []
    conn.on_change = lambda: changes.append((conn.available, conn.mode))
    await conn.ensure_ready()
    assert lamp.calls[:3] == ["set_power", "activate_fx_mode", "set_music"]
    assert changes == [(True, "music")]
    assert conn.mode == "music"
    assert conn.max_fps == connection.MUSIC_MAX_FPS
    assert conn.available
    assert lamp.power == "on"


async def test_frame_reaches_lamp_via_music(lamp, make_conn):
    conn = make_conn(lamp)
    await conn.send_frame(RED_FRAME)
    await wait_until(lambda: lamp.frames)
    assert lamp.frames == [RED_LEDS]
    assert lamp.frame_sources == ["music"]


async def test_recovers_after_direct_lost(lamp, make_conn):
    conn = make_conn(lamp)
    await conn.ensure_ready()
    lamp.drop_direct()
    # The props message has to arrive before the next frame
    await wait_until(lambda: not conn._direct)
    await conn.send_frame(RED_FRAME)
    await wait_until(lambda: lamp.frames)
    assert lamp.calls.count("activate_fx_mode") == 2
    assert lamp.frames == [RED_LEDS]


async def test_reconnects_after_music_drop(lamp, make_conn):
    conn = make_conn(lamp)
    await conn.ensure_ready()
    await wait_until(lambda: lamp.music_connected)
    lamp.kill_music()
    await wait_until(lambda: conn._link is None or conn._link.closed)
    await conn.send_frame(RED_FRAME)
    await wait_until(lambda: lamp.frames)
    assert lamp.calls.count("set_music") == 2
    assert lamp.frames == [RED_LEDS]
    assert conn.mode == "music"


async def test_two_lamps_share_port(loopback):
    first = FakeLamp("127.0.0.2")
    second = FakeLamp("127.0.0.3")
    await first.start()
    await second.start()
    music_port = free_port("0.0.0.0")
    conn_first = CubeConnection(first.ip, music_port, lan_port=first.port)
    conn_second = CubeConnection(second.ip, music_port, lan_port=second.port)
    try:
        await conn_first.ensure_ready()
        await conn_second.ensure_ready()
        assert conn_first.mode == conn_second.mode == "music"
        assert first.music_target[1] == second.music_target[1] == music_port
        await conn_first.send_frame(RED_FRAME)
        await conn_second.send_frame(GREEN_FRAME)
        await wait_until(lambda: first.frames and second.frames)
        await asyncio.sleep(0.05)
        assert first.frames == [RED_LEDS]
        assert second.frames == [GREEN_LEDS]
    finally:
        await conn_first.close()
        await conn_second.close()
        await first.stop()
        await second.stop()
    # The last user closes the shared server
    assert music_port not in MusicServer._instances


async def test_fallback_normal_when_music_refused(lamp, make_conn, caplog):
    lamp.refuse_music = True
    conn = make_conn(lamp, music_timeout=0.2)
    with caplog.at_level(logging.WARNING):
        await conn.ensure_ready()
    assert conn.mode == "normal"
    assert conn.max_fps == 1.0
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "README: Music mode port" in warnings[0]
    assert "127.0.0.2" in warnings[0]
    await conn.send_frame(RED_FRAME)
    await wait_until(lambda: lamp.frames)
    assert lamp.frames == [RED_LEDS]
    assert lamp.frame_sources == ["lan"]


async def test_music_retried_only_when_asked_after_interval(
    lamp, make_conn, monkeypatch, caplog
):
    monkeypatch.setattr(connection, "MUSIC_RETRY_S", 0.3)
    lamp.refuse_music = True
    conn = make_conn(lamp, music_timeout=0.1)
    await conn.ensure_ready()
    assert conn.mode == "normal"
    # Still refused at the first retry: no second warning
    await asyncio.sleep(0.35)
    caplog.clear()
    with caplog.at_level(logging.WARNING):
        await conn.ensure_ready(retry_music=True)
    assert conn.mode == "normal"
    assert [r for r in caplog.records if r.levelno >= logging.WARNING] == []
    lamp.refuse_music = False
    # Too soon after the last try
    await conn.ensure_ready(retry_music=True)
    assert conn.mode == "normal"
    await asyncio.sleep(0.35)
    # Shows and frames never wait for music
    await conn.ensure_ready()
    await conn.ensure_ready(refresh=True)
    await conn.send_frame(RED_FRAME)
    assert conn.mode == "normal"
    assert lamp.calls.count("set_music") == 2
    await conn.ensure_ready(retry_music=True)
    assert conn.mode == "music"
    assert conn.max_fps == connection.MUSIC_MAX_FPS


async def test_normal_mode_keeps_only_newest_waiting_frame(
    lamp, make_conn, monkeypatch
):
    monkeypatch.setattr(connection, "NORMAL_MAX_FPS", 5.0)
    lamp.refuse_music = True
    conn = make_conn(lamp, music_timeout=0.1)
    await conn.send_frame(RED_FRAME)
    # Both wait for the rate limit, the newer one replaces the older one
    await asyncio.gather(
        conn.send_frame(bytes(75)), conn.send_frame(GREEN_FRAME)
    )
    await wait_until(lambda: len(lamp.frames) >= 2)
    await asyncio.sleep(0.05)
    assert lamp.frames == [RED_LEDS, GREEN_LEDS]


async def test_error_reply_raises(lamp, make_conn):
    lamp.errors["activate_fx_mode"] = {"code": -1, "message": "unsupported"}
    conn = make_conn(lamp)
    with pytest.raises(LampError, match="unsupported"):
        await conn.ensure_ready()
    # An error reply means the lamp is still there
    assert not conn._lan.closed


async def test_call_times_out(lamp):
    lamp.silent.add("get_prop")
    client = LanClient()
    await client.connect(lamp.ip, lamp.port)
    try:
        with pytest.raises(LampError):
            await client.call("get_prop", ["power"], timeout=0.1)
        assert client.closed
    finally:
        await client.close()


async def test_notify_callback_and_local_ip(lamp):
    client = LanClient()
    seen = []
    client.on_notify = seen.append
    await client.connect(lamp.ip, lamp.port)
    try:
        assert client.local_ip.startswith("127.")
        assert await client.call("get_prop", ["model"]) == ["CubeMatrix"]
        lamp.drop_direct()
        await wait_until(lambda: seen)
        assert seen == [{"color_mode": 1}]
    finally:
        await client.close()


async def test_power_off_notification_drops_direct(lamp, make_conn):
    conn = make_conn(lamp)
    await conn.ensure_ready()
    lamp.notify({"power": "off"})
    await wait_until(lambda: not conn._direct)
    await conn.send_frame(RED_FRAME)
    await wait_until(lambda: lamp.frames)
    assert lamp.calls.count("set_power") == 2


async def test_lan_port_resolved_at_call_time(lamp, monkeypatch):
    from custom_components.cubematrix import protocol

    monkeypatch.setattr(protocol, "LAN_PORT", lamp.port)
    conn = CubeConnection(lamp.ip, 0)
    try:
        await conn.ensure_ready()
        assert conn.available
    finally:
        await conn.close()


async def test_unreachable_sets_unavailable(loopback):
    conn = CubeConnection("127.0.0.2", 0, lan_port=free_port())
    with pytest.raises(LampError):
        await conn.ensure_ready()
    assert conn.available is False
    assert conn.mode == "offline"
    # The next call tries again
    with pytest.raises(LampError):
        await conn.send_frame(RED_FRAME)
    await conn.close()


async def test_lamp_comes_back_after_failure(loopback, make_conn):
    fake = FakeLamp("127.0.0.2")
    fake.port = free_port()
    conn = make_conn(fake)
    with pytest.raises(LampError):
        await conn.ensure_ready()
    await fake.start(fake.port)
    try:
        await conn.ensure_ready()
        assert conn.available
        assert conn.mode == "music"
    finally:
        await conn.close()
        await fake.stop()


def player_for(conn):
    library = PresetLibrary(dict(EFFECTS), {}, {})
    return Player(conn, Layout(), lambda: library)


async def test_player_background_runs_fast_on_real_connection(lamp, make_conn):
    conn = make_conn(lamp)
    player = player_for(conn)
    try:
        await player.turn_on()
        await asyncio.sleep(1.0)
        # Fire at 10 fps in music mode, not 1 fps from the offline start
        assert len(lamp.frames) >= 7
        assert set(lamp.frame_sources) == {"music"}
    finally:
        await player.shutdown()


async def test_player_brings_music_back_after_failed_connect_back(
    lamp, make_conn, monkeypatch
):
    monkeypatch.setattr(connection, "MUSIC_RETRY_S", 0.3)
    monkeypatch.setattr(player_module, "WATCHDOG_S", 0.2)
    lamp.refuse_music = True
    conn = make_conn(lamp, music_timeout=0.1)
    player = player_for(conn)
    try:
        await player.turn_on()
        await wait_until(lambda: conn.mode == "normal")
        lamp.refuse_music = False
        await wait_until(lambda: conn.mode == "music")
        await wait_until(lambda: "music" in lamp.frame_sources)
    finally:
        await player.shutdown()
