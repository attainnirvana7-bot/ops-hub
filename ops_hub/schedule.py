"""以台北時間展開預期執行時段，判斷哪些時段在本次檢查應該評估。"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from zoneinfo import ZoneInfo

from .config import Slot, Target


@dataclass(frozen=True)
class SlotInstance:
    slot: Slot
    date: dt.date                 # 時段的當地（台北）日期，產出物路徑以此展開
    start: dt.datetime            # 預定觸發時間（aware）
    deadline: dt.datetime         # start + grace：此時之前應該要有 run 建立

    def label(self) -> str:
        return f"{self.date:%m/%d} {self.slot.at:%H:%M}"


def instances(target: Target, now: dt.datetime, tz: ZoneInfo,
              lookback: dt.timedelta = dt.timedelta(hours=24)) -> list[SlotInstance]:
    """回傳 deadline 落在 (now - lookback, now] 的時段。

    早晚兩次檢查各自回看 24 小時，所以每個時段在「grace 過後的第一次檢查」一定會被評估到；
    weekday 判斷用的是台北日期（FSC 週六、USA 週四都不會被預期）。
    """
    now_local = now.astimezone(tz)
    out = []
    for offset in (-2, -1, 0):
        day = now_local.date() + dt.timedelta(days=offset)
        for slot in target.slots:
            if day.weekday() not in slot.days:
                continue
            start = dt.datetime.combine(day, slot.at, tzinfo=tz)
            deadline = start + dt.timedelta(minutes=slot.grace_min)
            if now - lookback < deadline <= now:
                out.append(SlotInstance(slot, day, start, deadline))
    out.sort(key=lambda i: i.start)
    return out
