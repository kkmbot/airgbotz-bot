"""Admin sandbox on E2B: one separate Linux VM per chat, closed when idle. Secrets never enter it:
the bot token and provider keys are not passed, and nothing from this process's environment is copied in."""
from __future__ import annotations

import logging
import time
from typing import Any, Awaitable, Callable

log = logging.getLogger(__name__)

MAX_OUT = 3000
MAX_FILE = 45_000_000  # Telegram bots can send up to 50 MB


class BoxUnavailable(Exception):
    pass


def _cut(text: str) -> str:
    text = (text or "").strip()
    return text if len(text) <= MAX_OUT else text[:MAX_OUT] + "\n[output cut]"


async def _default_factory(api_key: str, timeout: int):
    from e2b_code_interpreter import AsyncSandbox
    return await AsyncSandbox.create(timeout=timeout, api_key=api_key, allow_internet_access=True)


class E2BBox:
    """factory(api_key, idle_seconds) -> sandbox object; tests pass a fake."""

    def __init__(self, api_key: str, run_timeout: int = 60, idle_seconds: int = 300,
                 factory: Callable[[str, int], Awaitable[Any]] = _default_factory) -> None:
        self.api_key = api_key
        self.run_timeout = run_timeout
        self.idle = idle_seconds
        self.factory = factory
        self._boxes: dict[int, tuple[Any, float]] = {}

    def configured(self) -> bool:
        return bool(self.api_key)

    async def _get(self, chat_id: int):
        self._boxes = {k: v for k, v in self._boxes.items() if time.monotonic() - v[1] < self.idle}
        entry = self._boxes.get(chat_id)
        if entry:
            box = entry[0]
            try:
                await box.set_timeout(self.idle)  # idle sandboxes shut themselves down at the provider
            except Exception:  # noqa: BLE001
                box = None
            if box:
                self._boxes[chat_id] = (box, time.monotonic())
                return box
        try:
            box = await self.factory(self.api_key, self.idle)
        except Exception as e:  # noqa: BLE001
            log.warning("sandbox could not start: %s", type(e).__name__)
            raise BoxUnavailable("the sandbox could not start (key wrong, or the free credit is used up)") from e
        self._boxes[chat_id] = (box, time.monotonic())
        return box

    async def run_python(self, chat_id: int, code: str) -> str:
        box = await self._get(chat_id)
        try:
            ex = await box.run_code(code, timeout=self.run_timeout)
        except Exception as e:  # noqa: BLE001
            return f"[run failed: {type(e).__name__}]"
        parts = []
        out = "".join(getattr(ex.logs, "stdout", []) or [])
        err = "".join(getattr(ex.logs, "stderr", []) or [])
        if out:
            parts.append(out)
        if err:
            parts.append("[stderr]\n" + err)
        if getattr(ex, "error", None):
            parts.append(f"[error] {ex.error.name}: {ex.error.value}")
        elif getattr(ex, "text", None) and ex.text not in out:
            parts.append(str(ex.text))
        return _cut("\n".join(parts)) or "(no output)"

    async def run_shell(self, chat_id: int, cmd: str) -> str:
        box = await self._get(chat_id)
        try:
            res = await box.commands.run(cmd, timeout=self.run_timeout)
            text = (res.stdout or "") + (("\n[stderr]\n" + res.stderr) if res.stderr else "")
        except Exception as e:  # noqa: BLE001 - a non-zero exit raises with the output attached
            text = f"[exit {getattr(e, 'exit_code', '?')}] " + (getattr(e, "stdout", "") or "") + (getattr(e, "stderr", "") or "")
            if text.strip().endswith("]"):
                text += str(e)[:300]
        return _cut(text) or "(no output)"

    async def list_files(self, chat_id: int, path: str = "/home/user") -> str:
        box = await self._get(chat_id)
        try:
            items = await box.files.list(path)
        except Exception:  # noqa: BLE001
            return f"[cannot list {path}]"
        lines = [f"{'[dir] ' if str(getattr(i.type, 'value', i.type)) == 'dir' else ''}{i.path}  ({getattr(i, 'size', 0)} B)" for i in items[:80]]
        return _cut("\n".join(lines)) or "(empty folder)"

    async def read_file(self, chat_id: int, path: str) -> bytes:
        box = await self._get(chat_id)
        try:
            data = await box.files.read(path, format="bytes")
        except Exception as e:  # noqa: BLE001
            raise BoxUnavailable(f"cannot read {path}") from e
        data = bytes(data)
        if len(data) > MAX_FILE:
            raise BoxUnavailable("that file is over 45 MB")
        return data

    async def write_file(self, chat_id: int, name: str, data: bytes) -> str:
        box = await self._get(chat_id)
        safe = ("".join(ch for ch in name if ch.isalnum() or ch in "._-").lstrip(".") or "upload.bin")[:80]
        path = f"/home/user/{safe}"
        try:
            await box.files.write(path, data)
        except Exception as e:  # noqa: BLE001
            raise BoxUnavailable("upload to the sandbox failed") from e
        return path

    async def close_all(self) -> None:
        for box, _ in list(self._boxes.values()):
            try:
                await box.kill()
            except Exception:  # noqa: BLE001
                pass
        self._boxes.clear()
