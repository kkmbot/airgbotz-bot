import asyncio
from types import SimpleNamespace as NS

import pytest

from ai_bot.e2b_box import BoxUnavailable, E2BBox


class FakeSandbox:
    def __init__(self):
        self.timeouts, self.files_written, self.killed = [], {}, False
        self.files = NS(list=self._list, read=self._read, write=self._write)
        self.commands = NS(run=self._cmd)

    async def set_timeout(self, t):
        self.timeouts.append(t)

    async def run_code(self, code, timeout=None):
        if "boom" in code:
            return NS(logs=NS(stdout=[], stderr=[]), error=NS(name="ValueError", value="bad"), text=None)
        return NS(logs=NS(stdout=["hi\n"], stderr=[]), error=None, text=None)

    async def _cmd(self, cmd, timeout=None):
        if cmd == "fail":
            raise type("X", (Exception,), {"exit_code": 2, "stdout": "", "stderr": "nope"})("failed")
        return NS(stdout="out\n", stderr="")

    async def _list(self, path):
        return [NS(path="/home/user/a.txt", type="file", size=3)]

    async def _read(self, path, format="bytes"):
        return b"abc"

    async def _write(self, path, data):
        self.files_written[path] = data

    async def kill(self):
        self.killed = True


def mk():
    created = []

    async def factory(key, idle):
        sb = FakeSandbox()
        created.append((key, idle, sb))
        return sb
    return E2BBox("k" * 12, 60, 300, factory), created


def test_run_python_and_error():
    box, created = mk()
    assert asyncio.run(box.run_python(1, "print('hi')")).strip() == "hi"
    assert "ValueError: bad" in asyncio.run(box.run_python(1, "boom"))
    assert len(created) == 1  # same chat reuses its sandbox


def test_separate_sandbox_per_chat_and_nothing_secret_passed():
    box, created = mk()

    async def go():
        await box.run_python(1, "x")
        await box.run_python(2, "x")
    asyncio.run(go())
    assert len(created) == 2
    assert all(c[0] == "k" * 12 and c[1] == 300 for c in created)  # only the E2B key and the idle time go to the factory


def test_shell_files_upload_and_close():
    box, created = mk()

    async def go():
        assert (await box.run_shell(1, "ls")).startswith("out")
        assert "exit 2" in await box.run_shell(1, "fail")
        assert "a.txt" in await box.list_files(1)
        assert await box.read_file(1, "/home/user/a.txt") == b"abc"
        path = await box.write_file(1, "../../evil name.txt", b"z")
        assert path == "/home/user/evilname.txt" or path.startswith("/home/user/")
        assert ".." not in path
        await box.close_all()
    asyncio.run(go())
    assert created[0][2].killed


def test_start_failure_is_polite():
    async def bad(key, idle):
        raise RuntimeError("no credit")
    box = E2BBox("k" * 12, factory=bad)
    with pytest.raises(BoxUnavailable):
        asyncio.run(box.run_python(1, "x"))
