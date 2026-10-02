"""測試共用：假的 requests.Session、fixture 載入、時間平移。"""

from __future__ import annotations

import copy
import datetime as dt
import json
from pathlib import Path
from urllib.parse import parse_qsl, urlparse

from ops_hub import config
from ops_hub.gh import GitHub

FIXTURES = Path(__file__).parent / "fixtures"
OWNER = "attainnirvana7-bot"
UTC = dt.timezone.utc


def fixture(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def utc(s: str) -> dt.datetime:
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(UTC)


def iso(t: dt.datetime) -> str:
    return t.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def make_run(rid: int, created: str, *, conclusion: str | None = "success", status: str = "completed",
             minutes: float = 1, title: str = "Daily Conflict Brief", event: str = "workflow_dispatch",
             repo: str = "conflict-monitor") -> dict:
    c = utc(created)
    return {
        "id": rid, "name": title, "display_title": title, "event": event,
        "status": status, "conclusion": conclusion if status == "completed" else None,
        "html_url": f"https://github.com/{OWNER}/{repo}/actions/runs/{rid}",
        "created_at": iso(c), "run_started_at": iso(c),
        "updated_at": iso(c + dt.timedelta(minutes=minutes)),
    }


def shift_runs(runs: list[dict], delta: dt.timedelta) -> list[dict]:
    out = copy.deepcopy(runs)
    for r in out:
        for k in ("created_at", "updated_at", "run_started_at"):
            if r.get(k):
                r[k] = iso(utc(r[k]) + delta)
    return out


class Resp:
    def __init__(self, status: int = 200, data=None, headers: dict | None = None):
        self.status_code = status
        self._data = data if data is not None else {}
        self.headers = headers or {}

    def json(self):
        return self._data


class Seq(list):
    """依序回傳的多個回應（最後一個會一直重複）。"""


class FakeSession:
    """routes：API 路徑 → 回應資料 / Resp / Seq（依序回傳）/ callable(params)。

    沒列出的路徑回 404。
    """

    def __init__(self, routes: dict, default_headers: dict | None = None):
        self.routes = routes
        self.headers: dict = {}
        self.default_headers = default_headers or {}
        self.requests: list[tuple[str, dict]] = []
        self.posts: list[dict] = []

    def get(self, url, params=None, timeout=None):
        parsed = urlparse(url)
        merged = dict(parse_qsl(parsed.query))
        merged.update(params or {})
        self.requests.append((parsed.path, merged))
        route = self.routes.get(parsed.path)
        if route is None:
            return Resp(404, {"message": "Not Found"}, dict(self.default_headers))
        if isinstance(route, Seq):
            route = route.pop(0) if len(route) > 1 else route[0]
        if callable(route):
            route = route(merged)
        if isinstance(route, Resp):
            route.headers = {**self.default_headers, **route.headers}
            return route
        return Resp(200, route, dict(self.default_headers))


def make_gh(routes: dict, headers: dict | None = None) -> tuple[GitHub, FakeSession]:
    s = FakeSession(routes, headers)
    return GitHub("test-token", session=s, sleep=lambda _s: None), s


def make_cfg(targets: list[dict], **extra) -> config.Config:
    data = {"owner": OWNER, "timezone": "Asia/Taipei", "targets": targets}
    data.update(extra)
    return config.from_dict(data)


def base(repo: str) -> str:
    return f"/repos/{OWNER}/{repo}"


def workflow_routes(repo: str, wf: str, runs: list[dict], name: str = "Daily Conflict Brief",
                    state: str = "active") -> dict:
    return {
        f"{base(repo)}/actions/workflows/{wf}": {"name": name, "state": state,
                                                 "html_url": f"https://github.com/{OWNER}/{repo}"},
        f"{base(repo)}/actions/workflows/{wf}/runs": {"workflow_runs": runs},
    }
