"""Optional tiny HTTP health endpoint.

Some hosts want something listening on $PORT even for a worker. The bot itself never needs a port
(it long-polls Telegram), so this runs only when PORT is set, and it answers "ok" to every GET.
"""
from __future__ import annotations

import asyncio


async def _handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        await reader.readline()
        while True:
            line = await reader.readline()
            if not line or line in (b"\r\n", b"\n"):
                break
        body = b"ok\n"
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nContent-Length: "
                     + str(len(body)).encode() + b"\r\nConnection: close\r\n\r\n" + body)
        await writer.drain()
    except (ConnectionError, asyncio.IncompleteReadError):
        pass
    finally:
        writer.close()


async def start_health_server(port: int, host: str = "0.0.0.0") -> asyncio.base_events.Server:
    return await asyncio.start_server(_handle, host, port)
