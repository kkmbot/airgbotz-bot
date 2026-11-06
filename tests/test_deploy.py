import asyncio
import os

from ai_bot.config import usable_db_path
from ai_bot.health import start_health_server


def test_usable_db_path_keeps_a_writable_folder(tmp_path):
    target = str(tmp_path / "bot.sqlite3")
    assert usable_db_path(target) == target


def test_usable_db_path_falls_back_when_folder_cannot_be_created(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("not a folder")
    result = usable_db_path(str(blocker / "bot.sqlite3"))
    assert result.endswith(os.path.join("data", "bot.sqlite3"))


def test_health_endpoint_answers_ok():
    async def go():
        server = await start_health_server(0, host="127.0.0.1")
        port = server.sockets[0].getsockname()[1]
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write(b"GET / HTTP/1.1\r\nHost: x\r\n\r\n")
        await writer.drain()
        data = await reader.read()
        writer.close()
        server.close()
        return data

    data = asyncio.run(go())
    assert data.startswith(b"HTTP/1.1 200 OK")
    assert data.endswith(b"ok\n")
