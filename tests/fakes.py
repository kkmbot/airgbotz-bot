"""Scripted fake providers: no network, no keys."""
from ai_bot.providers import BaseProvider, ProviderError


class FakeProvider(BaseProvider):
    def __init__(self, name, script=None, vision=False, configured=True):
        super().__init__(client=None)
        self.name = name
        self.supports_vision = vision
        self._configured = configured
        self.script = list(script or [])  # each item: str reply, or an Exception to raise
        self.calls = 0
        self.last_messages = None

    def configured(self):
        return self._configured

    async def chat(self, messages, system, image=None, mime=None):
        self.calls += 1
        self.last_messages = messages
        if not self.script:
            return "default reply"
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def http_err(name, status, retry_after=None):
    return ProviderError(name, status, "x", retry_after)


class FakeClock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t
