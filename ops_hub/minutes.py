"""本月 Actions 分鐘數：帳單 API 優先，否則逐 job 無條件進位估算。"""

from __future__ import annotations

import datetime as dt
import logging
import math
from dataclasses import dataclass, field

from .checks import ts
from .gh import GitHub, GitHubError

log = logging.getLogger(__name__)


@dataclass
class Minutes:
    total: int = 0
    per_repo: dict[str, int] = field(default_factory=dict)
    source: str = "估算"            # 帳單 / 估算
    note: str = ""                  # 帳單 API 不可用的原因


def month_key(now: dt.datetime) -> str:
    return now.astimezone(dt.timezone.utc).strftime("%Y-%m")   # GitHub 以 UTC 曆月計費


def job_minutes(job: dict, now: dt.datetime) -> int:
    """GitHub 逐 job 計費、各自無條件進位到分鐘。"""
    if job.get("conclusion") == "skipped":
        return 0
    start = ts(job.get("started_at"))
    if not start:
        return 0
    end = ts(job.get("completed_at")) or now
    secs = (end - start).total_seconds()
    return math.ceil(secs / 60) if secs > 0 else 0


def from_billing(gh: GitHub, owner: str, now: dt.datetime, exclude: set[str]) -> Minutes:
    u = now.astimezone(dt.timezone.utc)
    data = gh.get(f"/users/{owner}/settings/billing/usage", {"year": u.year, "month": u.month})
    per: dict[str, float] = {}
    for item in data.get("usageItems") or []:
        if str(item.get("product", "")).lower() != "actions":
            continue
        if str(item.get("unitType", "")).lower() != "minutes":
            continue
        repo = str(item.get("repositoryName") or "(未知)").split("/")[-1]
        if repo in exclude:
            continue
        per[repo] = per.get(repo, 0) + float(item.get("quantity") or 0)
    per_int = {k: math.ceil(v) for k, v in per.items()}
    return Minutes(total=sum(per_int.values()), per_repo=per_int, source="帳單")


def estimate(gh: GitHub, owner: str, repos: list[str], now: dt.datetime, cache: dict) -> Minutes:
    """cache 結構 {"month": "YYYY-MM", "runs": {run_id: [repo, minutes]}}，就地更新。

    已完成的 run 分鐘數不會再變，快取後不必重抓 jobs；跨月時整個清除。
    """
    month = month_key(now)
    if cache.get("month") != month:
        cache.clear()
        cache.update({"month": month, "runs": {}})
    cached = cache.setdefault("runs", {})
    since = f"{month}-01T00:00:00Z"
    out = Minutes(source="估算")
    for repo in repos:
        total = 0
        runs = gh.paginate(f"/repos/{owner}/{repo}/actions/runs", "workflow_runs",
                           {"created": f">={since}"})
        for run in runs:
            rid = str(run["id"])
            if rid in cached:
                total += cached[rid][1]
                continue
            jobs = gh.paginate(f"/repos/{owner}/{repo}/actions/runs/{run['id']}/jobs", "jobs",
                               {"filter": "all"})
            m = sum(job_minutes(j, now) for j in jobs)
            if run.get("status") == "completed":
                cached[rid] = [repo, m]
            total += m
        out.per_repo[repo] = total
    out.total = sum(out.per_repo.values())
    return out


def compute(gh: GitHub, owner: str, private: list[str], public: set[str],
            now: dt.datetime, cache: dict) -> Minutes:
    try:
        return from_billing(gh, owner, now, public)
    except GitHubError as exc:
        note = f"帳單 API 無法使用（{'HTTP ' + str(exc.status) if exc.status else '連線失敗'}），改用估算"
        log.info(note)
    result = estimate(gh, owner, private, now, cache)
    result.note = note
    return result
