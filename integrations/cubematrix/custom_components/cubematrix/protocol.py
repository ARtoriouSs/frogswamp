"""Yeelight LAN protocol: JSON commands, replies and the music mode server"""

from __future__ import annotations

import asyncio
import itertools
import json
import logging
from collections.abc import Callable
from typing import Any, ClassVar

_LOGGER = logging.getLogger(__name__)

LAN_PORT = 55443
# How long to wait for a socket to close before giving up on it
CLOSE_TIMEOUT = 2.0


class LampError(Exception):
    """The lamp can't be reached, didn't answer or rejected a command"""


def command(id: int, method: str, params: list) -> bytes:
    message = {"id": id, "method": method, "params": params}
    return (json.dumps(message, separators=(",", ":")) + "\r\n").encode()


async def _close_writer(writer: asyncio.StreamWriter) -> None:
    writer.close()
    try:
        await asyncio.wait_for(writer.wait_closed(), CLOSE_TIMEOUT)
    except (OSError, TimeoutError):
        pass


async def _stop_task(task: asyncio.Task | None) -> None:
    if task is None or task is asyncio.current_task():
        return
    task.cancel()
    # wait() doesn't swallow a cancel of the task that called close()
    await asyncio.wait({task}, timeout=CLOSE_TIMEOUT)


class LanClient:
    """One TCP connection to the lamp's LAN control port"""

    def __init__(self) -> None:
        self.on_notify: Callable[[dict], None] | None = None
        self.local_ip = ""
        self.peer_ip = ""
        self._writer: asyncio.StreamWriter | None = None
        self._task: asyncio.Task | None = None
        self._ids = itertools.count(1)
        self._pending: dict[int, asyncio.Future] = {}
        self._closed = True

    @property
    def closed(self) -> bool:
        return self._closed

    async def connect(
        self, host: str, port: int | None = None, timeout: float = 5
    ) -> None:
        if port is None:
            port = LAN_PORT
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(host, port), timeout
            )
        except (OSError, TimeoutError) as err:
            raise LampError(f"Can't connect to {host}:{port}: {err!r}") from err
        self._writer = writer
        self.local_ip = writer.get_extra_info("sockname")[0]
        self.peer_ip = writer.get_extra_info("peername")[0]
        self._closed = False
        self._task = asyncio.get_running_loop().create_task(
            self._read_loop(reader), name=f"cubematrix LAN {host}"
        )

    async def call(self, method: str, params: list, timeout: float = 3) -> list:
        if self._closed or self._writer is None:
            raise LampError("Not connected to the lamp")
        id = next(self._ids)
        future = asyncio.get_running_loop().create_future()
        self._pending[id] = future
        try:
            async with asyncio.timeout(timeout):
                self._writer.write(command(id, method, params))
                await self._writer.drain()
                reply = await future
        except TimeoutError as err:
            # A lamp that stops answering usually has a dead connection
            await self.close()
            raise LampError(f"No reply to {method} in {timeout} s") from err
        except OSError as err:
            await self.close()
            raise LampError(f"Can't send {method}: {err!r}") from err
        finally:
            self._pending.pop(id, None)
        if "error" in reply:
            raise LampError(f"{method} failed: {reply['error']}")
        return reply.get("result", [])

    def send(self, method: str, params: list) -> None:
        """Send a command that gets no reply, like update_leds"""
        if self._closed or self._writer is None or self._writer.is_closing():
            raise LampError("Not connected to the lamp")
        self._writer.write(command(next(self._ids), method, params))

    async def close(self) -> None:
        self._mark_closed()
        await _stop_task(self._task)
        if self._writer is not None:
            await _close_writer(self._writer)

    async def _read_loop(self, reader: asyncio.StreamReader) -> None:
        try:
            while line := await reader.readline():
                try:
                    message = json.loads(line)
                except ValueError:
                    _LOGGER.debug("Bad line from the lamp: %r", line)
                    continue
                if isinstance(message, dict):
                    self._handle(message)
        except (OSError, ValueError) as err:
            # ValueError: a line longer than the stream limit
            _LOGGER.debug("LAN connection failed: %r", err)
        finally:
            self._mark_closed()

    def _handle(self, message: dict[str, Any]) -> None:
        future = self._pending.get(message.get("id"))  # type: ignore[arg-type]
        if future is not None:
            if not future.done():
                future.set_result(message)
            return
        if message.get("method") == "props" and self.on_notify is not None:
            try:
                self.on_notify(message.get("params") or {})
            except Exception:
                _LOGGER.exception("Error in the lamp notification handler")

    def _mark_closed(self) -> None:
        self._closed = True
        for future in self._pending.values():
            if not future.done():
                future.set_exception(LampError("Connection to the lamp closed"))
        if self._writer is not None:
            self._writer.close()


class MusicLink:
    """The lamp's music mode connection back to us. Commands get no reply"""

    def __init__(
        self, ip: str, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        self.ip = ip
        self.on_close: Callable[[], None] | None = None
        self._writer = writer
        self._ids = itertools.count(1)
        self._closed = False
        self._task = asyncio.get_running_loop().create_task(
            self._watch(reader), name=f"cubematrix music {ip}"
        )

    @property
    def closed(self) -> bool:
        return self._closed

    def send(self, method: str, params: list) -> None:
        if self._closed or self._writer.is_closing():
            raise LampError("Music mode connection is closed")
        self._writer.write(command(next(self._ids), method, params))

    async def close(self) -> None:
        # Closed on purpose, so on_close isn't called
        self._closed = True
        await _stop_task(self._task)
        await _close_writer(self._writer)

    async def _watch(self, reader: asyncio.StreamReader) -> None:
        # The lamp sends nothing in music mode, this only waits for the end
        try:
            while await reader.read(1024):
                pass
        except OSError:
            pass
        finally:
            self._lost()

    def _lost(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._writer.close()
        if self.on_close is not None:
            try:
                self.on_close()
            except Exception:
                _LOGGER.exception("Error in the music link close handler")


class MusicServer:
    """One TCP server per port for all lamps, connections are matched by IP"""

    _instances: ClassVar[dict[int, MusicServer]] = {}

    def __init__(self, port: int) -> None:
        self._port = port
        self._bound_port = port
        self._users = 0
        self._server: asyncio.Server | None = None
        self._links: dict[str, MusicLink] = {}
        self._waiters: dict[str, list[asyncio.Future]] = {}
        self._lock = asyncio.Lock()

    @classmethod
    def shared(cls, port: int) -> MusicServer:
        server = cls._instances.get(port)
        if server is None:
            server = cls._instances[port] = cls(port)
        return server

    @property
    def port(self) -> int:
        """The real port, useful when created with port 0"""
        return self._bound_port

    async def acquire(self) -> None:
        async with self._lock:
            if self._server is None:
                try:
                    server = await asyncio.start_server(
                        self._on_connect, "0.0.0.0", self._port
                    )
                except OSError as err:
                    raise LampError(
                        f"Can't listen on music port {self._port}: {err!r}"
                    ) from err
                self._server = server
                self._bound_port = server.sockets[0].getsockname()[1]
                # release() at zero users removes it, so put it back
                self._instances.setdefault(self._port, self)
            self._users += 1

    async def release(self) -> None:
        async with self._lock:
            self._users = max(self._users - 1, 0)
            if self._users or self._server is None:
                return
            server, self._server = self._server, None
            if self._instances.get(self._port) is self:
                del self._instances[self._port]
            server.close()
            for waiters in self._waiters.values():
                for future in waiters:
                    if not future.done():
                        future.set_exception(LampError("Music server closed"))
            links = list(self._links.values())
            self._links.clear()
            for link in links:
                await link.close()
            try:
                await asyncio.wait_for(server.wait_closed(), CLOSE_TIMEOUT)
            except TimeoutError:
                _LOGGER.debug("Music server on port %s didn't close", self._port)

    async def wait_for(self, ip: str, timeout: float = 5) -> MusicLink:
        link = self._links.get(ip)
        if link is not None and not link.closed:
            return link
        if self._server is None:
            raise LampError("Music server isn't running")
        future = asyncio.get_running_loop().create_future()
        waiters = self._waiters.setdefault(ip, [])
        waiters.append(future)
        try:
            async with asyncio.timeout(timeout):
                return await future
        except TimeoutError as err:
            raise LampError(f"No music connection from {ip} in {timeout} s") from err
        finally:
            waiters.remove(future)

    def _on_connect(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        peer = writer.get_extra_info("peername")
        if self._server is None or not peer:
            writer.close()
            return
        ip = peer[0]
        link = MusicLink(ip, reader, writer)
        old = self._links.get(ip)
        self._links[ip] = link
        if old is not None:
            # A new connection from the same lamp replaces the old one
            old._lost()
        for future in self._waiters.get(ip, []):
            if not future.done():
                future.set_result(link)
