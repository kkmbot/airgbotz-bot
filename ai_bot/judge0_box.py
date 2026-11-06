"""Free, card-less fallback for admin code running: the public Judge0 CE service (ce.judge0.com).
Stateless: no internet inside, no pip, no files kept between runs, plain Python 3 only. Runs on their machine, not ours.
The code you send goes to that third party, so it is for the admin's own non-secret snippets."""
from __future__ import annotations

import httpx

URL = "https://ce.judge0.com/submissions"
PYTHON_ID = 71  # Python 3.8.1 on the public instance (checked 6 Nov 2026)
MAX_OUT = 3000


class Judge0Box:
    def __init__(self, client: httpx.AsyncClient, url: str = URL) -> None:
        self.client, self.url = client, url

    def configured(self) -> bool:
        return True

    async def run_python(self, chat_id: int, code: str) -> str:
        try:
            r = await self.client.post(self.url, params={"base64_encoded": "false", "wait": "true"},
                                       json={"source_code": code, "language_id": PYTHON_ID, "cpu_time_limit": 5,
                                             "wall_time_limit": 10, "memory_limit": 128000, "enable_network": False},
                                       timeout=30)
        except httpx.HTTPError:
            return "[the free code service did not answer, try again]"
        if r.status_code >= 400:
            return f"[the free code service answered {r.status_code}]"
        d = r.json()
        parts = []
        for key, label in (("stdout", ""), ("stderr", "[stderr]\n"), ("compile_output", "[compile]\n"), ("message", "[message] ")):
            v = d.get(key)
            if v and str(v).strip():
                parts.append(label + str(v).strip())
        status = (d.get("status") or {}).get("description", "")
        if status and status != "Accepted":
            parts.append(f"[{status}]")
        text = "\n".join(parts).strip() or "(no output)"
        return text if len(text) <= MAX_OUT else text[:MAX_OUT] + "\n[output cut]"
