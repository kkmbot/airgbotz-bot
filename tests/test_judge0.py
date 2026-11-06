import asyncio
import httpx

from ai_bot.judge0_box import Judge0Box


def run(handler, code="print(2+2)"):
    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            return await Judge0Box(c).run_python(1, code)
    return asyncio.run(go())


def test_output_and_errors():
    ok = lambda req: httpx.Response(200, json={"stdout": "4\n", "stderr": None, "status": {"description": "Accepted"}})
    assert run(ok) == "4"
    bad = lambda req: httpx.Response(200, json={"stdout": None, "stderr": "Traceback", "status": {"description": "Runtime Error"}})
    assert "Traceback" in run(bad) and "Runtime Error" in run(bad)
    assert "429" in run(lambda req: httpx.Response(429))
