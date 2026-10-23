"""One lamp: LAN control, direct mode and the music link, with recovery"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Literal

from . import protocol
from .layout import encode_leds
from .protocol import LampError, LanClient, MusicLink, MusicServer

_LOGGER = logging.getLogger(__name__)

# Placeholder, measured on real hardware later
MUSIC_MAX_FPS: float = 10.0
# Normal mode allows about 60 commands per minute
NORMAL_MAX_FPS = 1.0
# Placeholder: how often a still picture is sent again, set by a hardware check
STATIC_REFRESH_S: float | None = None

# Any of these in a props message means the lamp left direct mode
DIRECT_LOST_PROPS = ("rgb", "ct", "hue", "sat", "color_mode", "flowing")

MUSIC_FALLBACK = (
    "Music mode connection from %s didn't arrive on port %s; using normal mode "
    "(about 1 frame per second). See README: Music mode port"
)

Mode = Literal["music", "normal", "offline"]


class CubeConnection:
    def __init__(
        self,
        host: str,
        music_port: int,
        *,
        lan_port: int | None = None,
        music_timeout: float = 5,
    ) -> None:
        self.host = host
        self.music_port = music_port
        self.on_change: Callable[[], None] | None = None
        self._lan_port = lan_port
        self._music_timeout = music_timeout
        self._lan: LanClient | None = None
        self._server: MusicServer | None = None
        self._link: MusicLink | None = None
        self._power_on = False
        self._direct = False
        # After a fallback, music is tried again only on a new LAN connection
        self._music_failed = False
        self._mode: Mode = "offline"
        self._available = False
        self._lock = asyncio.Lock()
        self._normal_lock = asyncio.Lock()
        self._frame_seq = 0
        self._last_normal_send: float | None = None

    @property
    def mode(self) -> Mode:
        return self._mode

    @property
    def max_fps(self) -> float:
        return MUSIC_MAX_FPS if self._mode == "music" else NORMAL_MAX_FPS

    @property
    def available(self) -> bool:
        return self._available

    async def ensure_ready(self, refresh: bool = False) -> None:
        async with self._lock:
            try:
                await self._prepare(refresh)
            except LampError:
                self._after_failure()
                raise

    async def send_frame(self, raw: bytes) -> None:
        if self._needs_ready():
            await self.ensure_ready()
        data = encode_leds(raw)
        if self._mode == "music":
            if self._send_music(data):
                return
            # The link died between the check and the send
            self._direct = False
            await self.ensure_ready()
            if self._mode == "music":
                if not self._send_music(data):
                    raise LampError("Music mode connection closed again")
                return
        await self._send_normal(data)

    async def power_off(self) -> None:
        async with self._lock:
            try:
                lan = await self._connect_lan()
                await lan.call("set_power", ["off", "smooth", 300])
            except LampError:
                self._after_failure()
                raise
            self._power_on = False
            self._direct = False

    async def close(self) -> None:
        async with self._lock:
            # A frame waiting in normal mode is dropped
            self._frame_seq += 1
            link, self._link = self._link, None
            lan, self._lan = self._lan, None
            server, self._server = self._server, None
            if link is not None:
                await link.close()
            if lan is not None:
                await lan.close()
            if server is not None:
                await server.release()
            self._power_on = False
            self._direct = False
            self._set_state("offline", False)

    async def _prepare(self, refresh: bool) -> None:
        lan = await self._connect_lan()
        if not self._power_on:
            await lan.call("set_power", ["on", "smooth", 300])
            self._power_on = True
        if refresh or not self._direct:
            await lan.call("activate_fx_mode", [{"mode": "direct"}])
            self._direct = True
        music = self._link is not None and not self._link.closed
        if not music and not self._music_failed:
            music = await self._start_music(lan)
            self._music_failed = not music
        self._set_state("music" if music else "normal", True)

    async def _connect_lan(self) -> LanClient:
        if self._lan is not None and not self._lan.closed:
            return self._lan
        if self._lan is not None:
            await self._lan.close()
            self._lan = None
        lan = LanClient()
        lan.on_notify = self._on_notify
        port = self._lan_port if self._lan_port is not None else protocol.LAN_PORT
        await lan.connect(self.host, port)
        self._lan = lan
        # A new connection knows nothing about the lamp state
        self._power_on = False
        self._direct = False
        self._music_failed = False
        return lan

    async def _start_music(self, lan: LanClient) -> bool:
        if self._server is None:
            server = MusicServer.shared(self.music_port)
            try:
                await server.acquire()
            except LampError as err:
                _LOGGER.warning(
                    "%s; using normal mode (about 1 frame per second). "
                    "See README: Music mode port",
                    err,
                )
                return False
            self._server = server
        if self._link is not None:
            await self._link.close()
            self._link = None
        try:
            await lan.call("set_music", [1, lan.local_ip, self._server.port])
            link = await self._server.wait_for(lan.peer_ip, self._music_timeout)
        except LampError:
            if lan.closed:
                raise
            link = None
        if link is None or link.closed:
            _LOGGER.warning(MUSIC_FALLBACK, self.host, self.music_port)
            return False
        link.on_close = lambda: self._on_link_closed(link)
        self._link = link
        return True

    def _send_music(self, data: str) -> bool:
        link = self._link
        if link is None:
            return False
        try:
            link.send("update_leds", [data])
        except LampError:
            return False
        return True

    async def _send_normal(self, data: str) -> None:
        self._frame_seq += 1
        seq = self._frame_seq
        loop = asyncio.get_running_loop()
        async with self._normal_lock:
            if seq != self._frame_seq:
                return
            if self._last_normal_send is not None:
                delay = self._last_normal_send + 1 / NORMAL_MAX_FPS - loop.time()
                if delay > 0:
                    await asyncio.sleep(delay)
                    if seq != self._frame_seq:
                        # A newer frame came in while this one waited
                        return
            if self._needs_ready():
                await self.ensure_ready()
            if self._lan is None:
                raise LampError("Not connected to the lamp")
            self._lan.send("update_leds", [data])
            self._last_normal_send = loop.time()

    def _needs_ready(self) -> bool:
        if self._lan is None or self._lan.closed or not self._direct:
            return True
        if self._mode == "offline":
            return True
        return self._mode == "music" and (self._link is None or self._link.closed)

    def _on_notify(self, params: dict) -> None:
        power = params.get("power")
        if power == "off":
            self._power_on = False
            self._direct = False
        elif power == "on":
            self._power_on = True
        if any(key in params for key in DIRECT_LOST_PROPS):
            self._direct = False

    def _on_link_closed(self, link: MusicLink) -> None:
        if link is self._link:
            self._direct = False

    def _after_failure(self) -> None:
        # An error reply leaves the LAN open, the lamp is still there
        if self._lan is None or self._lan.closed:
            self._set_state("offline", False)

    def _set_state(self, mode: Mode, available: bool) -> None:
        if mode == self._mode and available == self._available:
            return
        self._mode = mode
        self._available = available
        if self.on_change is not None:
            self.on_change()
