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
    daily: dict[str, dict[str, float]] = field(default_factory=dict)   # UTC 日期 → {repo: 分鐘}
    forecast: int | None = None     # 照本月平均速度的月底預估（心跳摘要顯示）

    def day_total(self, day: dt.date) -> float:
        return sum(self.daily.get(day.isoformat(), {}).values())

    def before(self, day: dt.date) -> float:
        """本月在 day 之前（不含）已用的分鐘數。"""
        return sum(sum(v.values()) for k, v in self.daily.items() if k < day.isoformat())


@dataclass
class Pace:
    day: dt.date                    # 評估的那一天（UTC，已過完）
    used: int                       # 當天用量
    allowance: int                  # 當天的每日配額：（額度 − 之前已用）÷ 當天起剩餘天數
    avg: int                        # 本月已過完各日的平均
    forecast: int                   # 照平均速度的月底預估
    top: list[tuple[str, int]]      # 當天用量最多的 repo


def days_in_month(day: dt.date) -> int:
    nxt = (day.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
    return (nxt - dt.timedelta(days=1)).day


def pace(m: Minutes, now: dt.datetime, included: int) -> Pace | None:
    """以昨天（UTC，GitHub 計費日）的用量對照每日配額。月初第一天沒有已過完的日子，回 None。"""
    today = now.astimezone(dt.timezone.utc).date()
    day = today - dt.timedelta(days=1)
    if day.month != today.month or not m.daily:       # 月初第一天，或沒有逐日明細
        return None
    before = m.before(day)
    remaining_days = days_in_month(day) - day.day + 1
    used = m.day_total(day)
    elapsed = day.day
    avg = (before + used) / elapsed
    repos = m.daily.get(day.isoformat(), {})
    return Pace(day=day, used=math.ceil(used),
                allowance=max(0, math.floor((included - before) / remaining_days)),
                avg=round(avg), forecast=round(avg * days_in_month(day)),
                top=sorted(((r, math.ceil(v)) for r, v in repos.items() if v), key=lambda x: -x[1])[:2])


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
    daily: dict[str, dict[str, float]] = {}
    for item in data.get("usageItems") or []:
        if str(item.get("product", "")).lower() != "actions":
            continue
        if str(item.get("unitType", "")).lower() != "minutes":
            continue
        repo = str(item.get("repositoryName") or "(未知)").split("/")[-1]
        if repo in exclude:
            continue
        qty = float(item.get("quantity") or 0)
        per[repo] = per.get(repo, 0) + qty
        day = str(item.get("date") or "")[:10]
        if day:
            daily.setdefault(day, {})
            daily[day][repo] = daily[day].get(repo, 0) + qty
    per_int = {k: math.ceil(v) for k, v in per.items()}
    return Minutes(total=sum(per_int.values()), per_repo=per_int, source="帳單", daily=daily)


def estimate(gh: GitHub, owner: str, repos: list[str], now: dt.datetime, cache: dict) -> Minutes:
    """cache 結構 {"month": "YYYY-MM", "runs": {run_id: [repo, minutes, UTC 日期]}}，就地更新。

    已完成的 run 分鐘數不會再變，快取後不必重抓 jobs；跨月時整個清除。
    分鐘數算在 run 建立的 UTC 日期（跨日的長 run 會整筆算在開始那天）。
    """
    month = month_key(now)
    old = cache.get("runs") or {}
    if cache.get("month") != month or any(len(v) < 3 for v in old.values()):   # 舊格式沒有日期：重建
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
            day = str(run.get("created_at") or "")[:10]
            if rid in cached:
                m = cached[rid][1]
                total += m
                out.daily.setdefault(cached[rid][2], {})
                out.daily[cached[rid][2]][repo] = out.daily[cached[rid][2]].get(repo, 0) + m
                continue
            jobs = gh.paginate(f"/repos/{owner}/{repo}/actions/runs/{run['id']}/jobs", "jobs",
                               {"filter": "all"})
            m = sum(job_minutes(j, now) for j in jobs)
            if run.get("status") == "completed":
                cached[rid] = [repo, m, day]
            total += m
            out.daily.setdefault(day, {})
            out.daily[day][repo] = out.daily[day].get(repo, 0) + m
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
