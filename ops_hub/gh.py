"""GitHub REST API client：重試、退避、分頁、擷取 token 到期標頭。

repo 是公開的，log 會被任何人看到：這裡只記 HTTP 狀態碼與 API 路徑，
不印 token，也不印回應內容。
"""

from __future__ import annotations

import datetime as dt
import logging
import re
import time
from typing import Callable

import requests

log = logging.getLogger(__name__)

API = "https://api.github.com"
USER_AGENT = "ops-hub-watchdog/1.0"

# 永久性錯誤，重試沒有意義（沿用 conflict-monitor/http.py 的教訓）
NON_RETRY_STATUSES = {400, 401, 403, 404, 405, 410, 422}

EXPIRY_HEADER = "github-authentication-token-expiration"


class GitHubError(Exception):
    def __init__(self, status: int | None, path: str, reason: str = ""):
        self.status = status
        self.path = path
        msg = f"HTTP {status}" if status else (reason or "連線失敗")
        super().__init__(f"{msg}（{path}）")


def parse_expiry(raw: str | None) -> dt.datetime | None:
    """解析到期標頭，例如 '2026-12-31 00:00:00 UTC' 或 '2026-12-31 08:00:00 +0800'。"""
    if not raw:
        return None
    raw = raw.strip()
    for fmt in ("%Y-%m-%d %H:%M:%S %z", "%Y-%m-%d %H:%M:%S UTC", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            parsed = dt.datetime.strptime(raw, fmt)
        except ValueError:
            continue
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=dt.timezone.utc)
        return parsed
    log.warning("無法解析 token 到期標頭格式")
    return None


class GitHub:
    def __init__(self, token: str, *, session: requests.Session | None = None,
                 retries: int = 3, base_delay: float = 2.0,
                 sleep: Callable[[float], None] = time.sleep):
        self.session = session or requests.Session()
        self.session.headers.update({
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": USER_AGENT,
        })
        if token:
            self.session.headers["Authorization"] = f"Bearer {token}"
        self.retries = retries
        self.base_delay = base_delay
        self.sleep = sleep
        self.token_expiry: dt.datetime | None = None
        self.calls = 0

    def _request(self, path: str, params: dict | None = None) -> requests.Response:
        url = path if path.startswith("http") else API + path
        delay = self.base_delay
        for attempt in range(1, self.retries + 1):
            self.calls += 1
            try:
                resp = self.session.get(url, params=params, timeout=30)
            except requests.RequestException as exc:
                log.warning("GET %s 連線失敗 (%d/%d): %s", path, attempt, self.retries, type(exc).__name__)
                if attempt == self.retries:
                    raise GitHubError(None, path, "連線失敗") from None
                self.sleep(delay)
                delay *= 2
                continue

            exp = resp.headers.get(EXPIRY_HEADER)
            if exp:
                self.token_expiry = parse_expiry(exp) or self.token_expiry

            if resp.status_code in NON_RETRY_STATUSES:
                log.info("GET %s 回傳 %d（不重試）", path, resp.status_code)
                raise GitHubError(resp.status_code, path)
            if resp.status_code == 429 or resp.status_code >= 500:
                wait = delay
                try:
                    wait = float(resp.headers.get("Retry-After") or delay)
                except ValueError:
                    pass
                log.warning("GET %s 回傳 %d (%d/%d)", path, resp.status_code, attempt, self.retries)
                if attempt == self.retries:
                    raise GitHubError(resp.status_code, path)
                self.sleep(min(wait, 60))
                delay *= 2
                continue
            return resp
        raise GitHubError(None, path)  # pragma: no cover

    def get(self, path: str, params: dict | None = None):
        return self._request(path, params).json()

    def paginate(self, path: str, key: str, params: dict | None = None,
                 max_pages: int = 20) -> list:
        params = dict(params or {})
        params.setdefault("per_page", 100)
        out: list = []
        url: str | None = path
        for _ in range(max_pages):
            resp = self._request(url, params)
            out.extend(resp.json().get(key) or [])
            nxt = _next_link(resp.headers.get("Link", ""))
            if not nxt:
                break
            url, params = nxt, None   # next 連結已帶有查詢參數
        return out


def _next_link(header: str) -> str | None:
    m = re.search(r'<([^>]+)>;\s*rel="next"', header or "")
    return m.group(1) if m else None
