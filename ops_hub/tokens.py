"""token 到期提醒。"""

from __future__ import annotations

import datetime as dt

from .checks import ERROR, WARN, Finding
from .config import Config

WATCHDOG_NAME = "看門狗 token（WATCHDOG_TOKEN）"


def findings(cfg: Config, watchdog_expiry: dt.datetime | None, today: dt.date,
             watchdog_seen: bool = True) -> list[Finding]:
    entries: list[tuple[str, dt.date | None]] = []
    if watchdog_seen:
        entries.append((WATCHDOG_NAME, watchdog_expiry.astimezone(cfg.tz).date() if watchdog_expiry else None))
    entries += [(t.name, t.expires) for t in cfg.tokens]

    out = []
    for name, exp in entries:
        if exp is None:              # 沒有到期標頭：--check-token 會說明，心跳不必每天重複
            continue
        left = (exp - today).days
        if left < 0:
            out.append(Finding("token", "已過期", f"{name} 已於 {exp} 到期", severity=ERROR,
                               key=f"token|{name}|{today}"))
        elif left <= cfg.token_warn_days:
            out.append(Finding("token", "即將到期", f"{name} 將於 {exp} 到期（剩 {left} 天）", severity=WARN,
                               key=f"token|{name}|{today}"))
    return out
