"""A fake Cube Matrix that speaks the Yeelight LAN protocol on 127.0.0.x"""

from __future__ import annotations

import asyncio
import json
import socket

CLOSE_TIMEOUT = 2.0


def free_port(host: str = "127.0.0.2") -> int:
    with socket.socket() as sock:
        sock.bind((host, 0))
        return sock.getsockname()[1]


class FakeLamp:
    def __init__(self, ip: str) -> None:
        self.ip = ip
        self.port = 0
        self.model = "CubeMatrix"
        self.power = "off"
        self.direct = False
        self.refuse_music = False
        # Method name -> error object sent back instead of a result
        self.errors: dict[str, dict] = {}
        # Methods that get no reply at all
        self.silent: set[str] = set()
        self.calls: list[str] = []
        self.frames: list[str] = []
        # "music" or "lan" for each recorded frame
        self.frame_sources: list[str] = []
        self.music_target: tuple[str, int] | None = None
        self._server: asyncio.Server | None = None
        self._lan_writers: list[asyncio.StreamWriter] = []
        self._music_writer: asyncio.StreamWriter | None = None
        self._tasks: set[asyncio.Task] = set()

    async def start(self, port: int | None = None) -> int:
        self._server = await asyncio.start_server(
            self._on_lan, self.ip, port if port is not None else self.port
        )
        self.port = self._server.sockets[0].getsockname()[1]
        return self.port

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
        writers = list(self._lan_writers)
        if self._music_writer is not None:
            writers.append(self._music_writer)
        for writer in writers:
            writer.close()
        for task in list(self._tasks):
            task.cancel()
        if self._tasks:
            await asyncio.wait(self._tasks, timeout=CLOSE_TIMEOUT)
        if self._server is not None:
            await asyncio.wait_for(self._server.wait_closed(), CLOSE_TIMEOUT)

    @property
    def music_connected(self) -> bool:
        return self._music_writer is not None

    def notify(self, params: dict) -> None:
        line = json.dumps({"method": "props", "params": params}) + "\r\n"
        for writer in self._lan_writers:
            writer.write(line.encode())

    def drop_direct(self) -> None:
        self.direct = False
        self.notify({"color_mode": 1})

    def kill_music(self) -> None:
        if self._music_writer is not None:
            self._music_writer.close()
            self._music_writer = None

    def _spawn(self, coro) -> None:
        task = asyncio.get_running_loop().create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _on_lan(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        self._tasks.add(asyncio.current_task())
        self._lan_writers.append(writer)
        try:
            while line := await reader.readline():
                message = json.loads(line)
                reply = self._handle(message, "lan")
                if reply is not None:
                    writer.write((json.dumps(reply) + "\r\n").encode())
        except (OSError, asyncio.CancelledError):
            pass
        finally:
            self._lan_writers.remove(writer)
            writer.close()
            self._tasks.discard(asyncio.current_task())

    def _handle(self, message: dict, source: str) -> dict | None:
        method = message["method"]
        params = message["params"]
        if method == "update_leds":
            # No reply, and frames are ignored outside direct mode
            if self.direct and self.power == "on":
                self.frames.append(params[0])
                self.frame_sources.append(source)
            return None
        self.calls.append(method)
        if method in self.silent:
            return None
        if method in self.errors:
            return {"id": message["id"], "error": self.errors[method]}
        result: list = ["ok"]
        if method == "get_prop":
            values = {"model": self.model, "power": self.power}
            result = [values.get(name, "") for name in params]
        elif method == "set_power":
            self.power = params[0]
            if self.power == "off":
                self.direct = False
        elif method == "activate_fx_mode":
            self.direct = params[0] == {"mode": "direct"}
        elif method == "set_music":
            if params[0] == 1:
                self.music_target = (params[1], params[2])
                if not self.refuse_music:
                    self._spawn(self._connect_music(params[1], params[2]))
            else:
                self.kill_music()
        return {"id": message["id"], "result": result}

    async def _connect_music(self, host: str, port: int) -> None:
        # Connect back from the lamp's own IP, like the real lamp does
        reader, writer = await asyncio.open_connection(
            host, port, local_addr=(self.ip, 0)
        )
        self.kill_music()
        self._music_writer = writer
        try:
            while line := await reader.readline():
                self._handle(json.loads(line), "music")
        except (OSError, asyncio.CancelledError):
            pass
        finally:
            writer.close()
            if self._music_writer is writer:
                self._music_writer = None
