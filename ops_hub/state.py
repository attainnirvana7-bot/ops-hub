"""state/state.json：心跳日期、已推播警示、分鐘數快取。

repo 公開，這裡只存去重所需的最少資料。
"""

from __future__ import annotations

import datetime as dt
import json
import logging
from pathlib import Path

log = logging.getLogger(__name__)

ALERT_TTL = dt.timedelta(hours=24)


class State:
    def __init__(self, data: dict | None = None):
        data = data or {}
        self.heartbeat_sent: str = data.get("heartbeat_sent", "")
        self.alerted: dict[str, str] = dict(data.get("alerted") or {})   # key → 首次推播時間（UTC ISO）
        self.minutes_cache: dict = dict(data.get("minutes_cache") or {})
        self.last_run: str = data.get("last_run", "")

    @classmethod
    def load(cls, path: Path) -> "State":
        try:
            return cls(json.loads(path.read_text(encoding="utf-8")))
        except FileNotFoundError:
            return cls()
        except (OSError, ValueError) as exc:
            log.warning("state 檔損毀，重新開始：%s", exc)
            return cls()

    def recently_alerted(self, key: str, now: dt.datetime) -> bool:
        raw = self.alerted.get(key)
        return bool(raw) and now - dt.datetime.fromisoformat(raw) < ALERT_TTL

    def mark_alerted(self, keys, now: dt.datetime) -> None:
        for k in keys:
            if not self.recently_alerted(k, now):
                self.alerted[k] = now.isoformat()

    def prune(self, now: dt.datetime) -> None:
        self.alerted = {k: v for k, v in self.alerted.items()
                        if now - dt.datetime.fromisoformat(v) < dt.timedelta(days=3)}

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "heartbeat_sent": self.heartbeat_sent,
            "last_run": self.last_run,
            "alerted": dict(sorted(self.alerted.items())),
            "minutes_cache": self.minutes_cache,
        }
        path.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
