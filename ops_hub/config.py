"""設定載入：watch.yaml → Config / Target / Slot。"""

from __future__ import annotations

import datetime as dt
import os
from dataclasses import dataclass, field
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

WEEKDAYS = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}
DAY_NAMES = "一二三四五六日"


def env(key: str, default: str = "") -> str:
    """GitHub Actions 未設定的 vars 會傳入空字串，所以一律用 `or`。"""
    return os.environ.get(key) or default


def parse_days(raw) -> frozenset[int]:
    if raw in (None, "daily"):
        return frozenset(range(7))
    if raw == "weekdays":
        return frozenset(range(5))
    if isinstance(raw, str):
        raw = [raw]
    days = set()
    for d in raw:
        key = str(d).strip().lower()[:3]
        if key not in WEEKDAYS:
            raise ValueError(f"無法辨識的星期：{d!r}")
        days.add(WEEKDAYS[key])
    return frozenset(days)


def describe_days(days: frozenset[int]) -> str:
    if len(days) == 7:
        return "每日"
    if days == frozenset(range(5)):
        return "平日"
    return "週" + "、".join(DAY_NAMES[d] for d in sorted(days))


@dataclass(frozen=True)
class Slot:
    name: str
    days: frozenset[int]
    at: dt.time
    grace_min: int
    title: str | None = None          # display_title 需包含的字串（區分同一 workflow 的不同時段）
    max_run_min: int = 60             # 執行中超過這個時間視為卡住


@dataclass(frozen=True)
class Artifact:
    path: str | None = None           # 產出物路徑樣板：{Y} {m} {d} {date}
    date_slack_days: int = 0          # 容許產出物日期比時段晚幾天（執行跨過午夜）
    commit_path: str | None = None    # 改為檢查：時段開始後是否有 commit 動到此路徑
    only_weekdays: bool = False
    slot: str | None = None           # 只檢查這個時段
    severity: str = "error"           # error / warn


@dataclass(frozen=True)
class Target:
    repo: str
    workflow: str
    slots: tuple[Slot, ...]
    artifact: Artifact | None = None
    check_duration: bool = True

    @property
    def label(self) -> str:
        return self.repo


@dataclass(frozen=True)
class TokenNote:
    name: str
    expires: dt.date


@dataclass
class Config:
    owner: str
    tz: ZoneInfo
    targets: list[Target]
    tokens: list[TokenNote] = field(default_factory=list)
    minutes_warn_at: int = 1500
    minutes_included: int = 2000
    minutes_per_repo_warn_at: int = 800
    duration_window: int = 14
    duration_factor: float = 3.0
    duration_min_seconds: int = 120
    duration_min_samples: int = 5
    token_warn_days: int = 14
    early_min: int = 10               # 時段開始前多久建立的 run 也算數


def _parse_time(raw) -> dt.time:
    if isinstance(raw, int):          # YAML 1.1 會把 07:43 讀成 463（六十進位）
        return dt.time(raw // 60, raw % 60)
    h, m = str(raw).split(":")
    return dt.time(int(h), int(m))


def _parse_artifact(raw) -> Artifact | None:
    if raw is None:
        return None
    if isinstance(raw, str):
        return Artifact(path=raw)
    return Artifact(
        path=raw.get("path"),
        date_slack_days=int(raw.get("date_slack_days", 0)),
        commit_path=raw.get("commit_since_slot"),
        only_weekdays=raw.get("only") == "weekdays",
        slot=raw.get("slot"),
        severity=raw.get("severity", "error"),
    )


def _parse_target(raw: dict) -> Target:
    slots = []
    for i, s in enumerate(raw.get("slots") or []):
        slots.append(Slot(
            name=s.get("name") or f"slot{i + 1}",
            days=parse_days(s.get("days", "daily")),
            at=_parse_time(s["at"]),
            grace_min=int(s.get("grace_min", 60)),
            title=s.get("title"),
            max_run_min=int(s.get("max_run_min", 60)),
        ))
    return Target(
        repo=raw["repo"],
        workflow=raw["workflow"],
        slots=tuple(slots),
        artifact=_parse_artifact(raw.get("artifact")),
        check_duration=bool(raw.get("check_duration", True)),
    )


def from_dict(data: dict) -> Config:
    minutes = data.get("minutes") or {}
    duration = data.get("duration") or {}
    tokens = []
    for t in data.get("tokens") or []:
        exp = t["expires"]
        if not isinstance(exp, dt.date):
            exp = dt.date.fromisoformat(str(exp))
        tokens.append(TokenNote(name=t["name"], expires=exp))
    return Config(
        owner=data["owner"],
        tz=ZoneInfo(data.get("timezone", "Asia/Taipei")),
        targets=[_parse_target(t) for t in data.get("targets") or []],
        tokens=tokens,
        minutes_warn_at=int(minutes.get("warn_at", 1500)),
        minutes_included=int(minutes.get("included", 2000)),
        minutes_per_repo_warn_at=int(minutes.get("per_repo_warn_at", 800)),
        duration_window=int(duration.get("window", 14)),
        duration_factor=float(duration.get("factor", 3)),
        duration_min_seconds=int(duration.get("min_seconds", 120)),
        duration_min_samples=int(duration.get("min_samples", 5)),
        token_warn_days=int(data.get("token_warn_days", 14)),
        early_min=int(data.get("early_min", 10)),
    )


def load(path: str | Path | None = None) -> Config:
    path = Path(path or env("OPS_HUB_CONFIG", "watch.yaml"))
    with open(path, encoding="utf-8") as f:
        return from_dict(yaml.safe_load(f))
