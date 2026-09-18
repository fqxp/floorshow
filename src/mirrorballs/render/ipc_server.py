"""Unix-socket JSON-Lines server running on its own thread (asyncio). It never touches renderer
state: it only puts events on the inbox and sends what the main thread hands it."""

from __future__ import annotations

import asyncio
import contextlib
import itertools
import logging
import os
import socket
import threading
from pathlib import Path
from queue import SimpleQueue
from typing import Any

from ..common.ipc import LineDecoder, encode

log = logging.getLogger("mirrorballs.ipc")
MAX_BUFFERED = 1 << 20  # drop messages for a client that is not reading


class AlreadyRunning(RuntimeError):
    pass


class IpcServer:
    def __init__(self, path: Path, inbox: SimpleQueue):
        self.path = Path(path)
        self.inbox = inbox
        self._ids = itertools.count(1)
        self._writers: dict[int, asyncio.StreamWriter] = {}
        self._handlers: set[asyncio.Task] = set()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._stop: asyncio.Event | None = None
        self._error: BaseException | None = None

    def start(self) -> None:
        self._claim_socket_path()
        self._thread = threading.Thread(target=self._run, name="ipc", daemon=True)
        self._thread.start()
        self._ready.wait(5)
        if self._error:
            raise self._error

    def _claim_socket_path(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if not self.path.exists():
            return
        probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            probe.settimeout(0.5)
            probe.connect(str(self.path))
        except (ConnectionRefusedError, FileNotFoundError, socket.timeout):
            self.path.unlink(missing_ok=True)  # stale socket from a crashed renderer
        else:
            raise AlreadyRunning(f"a renderer is already listening on {self.path}")
        finally:
            probe.close()

    def _run(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._serve())
        except BaseException as e:  # noqa: BLE001
            self._error = e
            self._ready.set()
        finally:
            pending = asyncio.all_tasks(self._loop)
            for t in pending:
                t.cancel()
            self._loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            self._loop.close()

    async def _serve(self) -> None:
        self._stop = asyncio.Event()
        server = await asyncio.start_unix_server(self._client, path=str(self.path))
        os.chmod(self.path, 0o600)
        self._ready.set()
        await self._stop.wait()
        server.close()
        writers = list(self._writers.values())
        for w in writers:
            w.close()
        await asyncio.gather(*(w.wait_closed() for w in writers), return_exceptions=True)
        if self._handlers:  # let every client handler finish its own cleanup before the server closes
            await asyncio.wait(list(self._handlers), timeout=1.0)
        await asyncio.sleep(0.02)  # run the transports' connection_lost callbacks before the loop stops
        await server.wait_closed()

    async def _client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        cid = next(self._ids)
        task = asyncio.current_task()
        if task:
            self._handlers.add(task)
        self._writers[cid] = writer
        self.inbox.put(("connect", cid))
        decoder = LineDecoder()
        try:
            while data := await reader.read(65536):
                for msg in decoder.feed(data):
                    self.inbox.put(("msg", cid, msg))
        except (ConnectionError, asyncio.IncompleteReadError):
            pass
        finally:
            self._writers.pop(cid, None)
            self.inbox.put(("disconnect", cid))
            writer.close()
            if task:
                self._handlers.discard(task)

    # --- called from the main thread -----------------------------------------------------

    def send(self, cid: int, msg: dict[str, Any]) -> None:
        if self._loop:
            self._loop.call_soon_threadsafe(self._write, cid, encode(msg))

    def broadcast(self, msg: dict[str, Any]) -> None:
        if self._loop and self._writers:
            self._loop.call_soon_threadsafe(self._write_all, encode(msg))

    def _write(self, cid: int, data: bytes) -> None:
        w = self._writers.get(cid)
        if w is None or w.is_closing():
            return
        if w.transport.get_write_buffer_size() > MAX_BUFFERED:
            return
        w.write(data)

    def _write_all(self, data: bytes) -> None:
        for cid in list(self._writers):
            self._write(cid, data)

    def stop(self) -> None:
        if self._loop and self._stop:
            self._loop.call_soon_threadsafe(self._stop.set)
        if self._thread:
            self._thread.join(2)
        with contextlib.suppress(OSError):
            self.path.unlink()
