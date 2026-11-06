"""Read public GitHub repos through the REST API. GITHUB_TOKEN is optional and only raises the rate limit."""
from __future__ import annotations

import base64
import re

import httpx

API = "https://api.github.com"


class GithubError(Exception):
    pass


_SPEC = re.compile(r"^(?:https?://github\.com/)?([\w.-]+)/([\w.-]+?)(?:\.git)?(?:/(tree|blob)/([^/]+)/?(.*))?(?:/(issues)(?:/(\d+))?)?/?$")


def parse_spec(spec: str) -> dict | None:
    s = spec.strip().split("?")[0].split("#")[0]
    s = re.sub(r"^github\.com/", "https://github.com/", s)
    m = _SPEC.match(s)
    if not m:
        return None
    owner, repo, kind, ref, path, issues, num = m.groups()
    return {"owner": owner, "repo": repo, "ref": ref, "path": (path or "").strip("/"),
            "issues": bool(issues), "number": int(num) if num else None}


class GithubReader:
    def __init__(self, client: httpx.AsyncClient, token: str = "") -> None:
        self.client = client
        self.headers = {"Accept": "application/vnd.github+json", "User-Agent": "sathi-bot"}
        if token:
            self.headers["Authorization"] = f"Bearer {token}"

    async def _get(self, path: str, **params):
        try:
            r = await self.client.get(API + path, headers=self.headers, params=params, timeout=15)
        except httpx.HTTPError as e:
            raise GithubError("GitHub did not answer") from e
        if r.status_code == 404:
            raise GithubError("not found (or private)")
        if r.status_code in (403, 429):
            raise GithubError("GitHub rate limit reached, try later")
        if r.status_code >= 400:
            raise GithubError(f"GitHub answered {r.status_code}")
        return r.json()

    async def read(self, spec: str) -> str:
        p = parse_spec(spec)
        if not p:
            raise GithubError("use owner/repo, owner/repo/path or a github.com link")
        o, rp = p["owner"], p["repo"]
        if p["issues"]:
            if p["number"]:
                it = await self._get(f"/repos/{o}/{rp}/issues/{p['number']}")
                return f"Issue #{it['number']} [{it['state']}] {it['title']}\n\n{(it.get('body') or '')[:3000]}"
            items = await self._get(f"/repos/{o}/{rp}/issues", state="open", per_page=10)
            return "Open issues:\n" + "\n".join(f"#{i['number']} {i['title']}" for i in items if "pull_request" not in i)
        if p["path"]:
            q = {"ref": p["ref"]} if p["ref"] else {}
            item = await self._get(f"/repos/{o}/{rp}/contents/{p['path']}", **q)
            if isinstance(item, list):
                return f"{o}/{rp}/{p['path']}:\n" + "\n".join(f"{'[dir] ' if i['type']=='dir' else ''}{i['name']}" for i in item[:60])
            if item.get("encoding") == "base64" and item.get("size", 0) <= 200_000:
                text = base64.b64decode(item["content"]).decode("utf-8", errors="replace")
                return f"{o}/{rp}/{p['path']}\n\n{text[:6000]}"
            raise GithubError("that file is too large or not text")
        info = await self._get(f"/repos/{o}/{rp}")
        lines = [f"{info['full_name']} - {info.get('description') or 'no description'}",
                 f"stars {info['stargazers_count']}, language {info.get('language')}, default branch {info['default_branch']}, "
                 f"updated {info['pushed_at'][:10]}"]
        try:
            top = await self._get(f"/repos/{o}/{rp}/contents")
            lines.append("Files: " + ", ".join(i["name"] for i in top[:40]))
        except GithubError:
            pass
        try:
            rd = await self._get(f"/repos/{o}/{rp}/readme")
            lines.append("README:\n" + base64.b64decode(rd["content"]).decode("utf-8", errors="replace")[:3500])
        except GithubError:
            pass
        return "\n".join(lines)
