"""Telegram 訊息組裝（HTML；版面規格見 docs/TELEGRAM_STYLE.md）。

所有內容都經 notify.esc() 跳脫：repo 名稱、detail 都可能含 < 或 &。
"""

from __future__ import annotations

import datetime as dt

from .checks import ERROR, INFO, WARN, Finding, TargetResult
from .minutes import Minutes
from .notify import Message, esc, link, title

ICON = {ERROR: "❌", WARN: "⚠️", INFO: "ℹ️"}
APP_ICON = "🛡"


def _minutes_text(m: Minutes | None) -> str:
    if m is None:
        return "本月 Actions 分鐘數無法取得"
    return f"本月 Actions 約 {m.total:,} 分（{m.source}）"


def _who(f: Finding) -> str:
    return f.repo if f.repo != "token" else "🔑"


def _line(f: Finding) -> str:
    text = f"{ICON.get(f.severity, '•')} {esc(_who(f))} — {esc(f.kind)}"
    if f.detail:
        text += f"：{esc(f.detail)}"
    if f.url:
        text += f"（{link(f.url, '執行紀錄')}）"
    return text


def _sorted(findings: list[Finding]) -> list[Finding]:
    order = {ERROR: 0, WARN: 1, INFO: 2}
    return sorted(findings, key=lambda f: order.get(f.severity, 3))


def _button(findings: list[Finding]) -> str | None:
    """按鈕連到最嚴重那筆異常的 run 頁；其餘的連結在摺疊區塊內逐條可點。"""
    for f in _sorted(findings):
        if f.url and f.severity in (ERROR, WARN):
            return f.url
    return None


def _counts(findings: list[Finding]) -> str:
    parts = [f"{ICON[s]} {n}" for s in (ERROR, WARN, INFO)
             if (n := sum(1 for f in findings if f.severity == s))]
    return "　".join(parts)


def heartbeat(results: list[TargetResult], general: list[Finding], minutes: Minutes | None,
              now: dt.datetime) -> Message:
    """now 須為台北時間（標題日期）。"""
    ok = sum(1 for r in results if r.ok)
    total = len(results)
    bad_general = any(f.severity in (ERROR, WARN) for f in general)
    icon = "✅" if ok == total and not bad_general else "⚠️"
    findings = _sorted([f for r in results for f in r.findings] + general)
    header = [title(APP_ICON, "ops-hub 心跳", now.date()),
              f"{icon} {ok}/{total} 正常｜{esc(_minutes_text(minutes))}"]
    if findings:
        header.append(_counts(findings))
    blocks = [[_line(f)] for f in findings]
    if minutes and minutes.note:
        blocks.append([f"ℹ️ {esc(minutes.note)}"])
    return Message(header, blocks, _button(findings))


def alert(findings: list[Finding], minutes: Minutes | None, now: dt.datetime) -> Message:
    """now 須為台北時間（標題日期）。"""
    findings = _sorted(findings)
    top = findings[0]
    header = [title(APP_ICON, "ops-hub 異常", now.date()),
              f"🌙 發現 {len(findings)} 項新異常｜{esc(_minutes_text(minutes))}",
              f"最嚴重：{ICON.get(top.severity, '•')} {esc(_who(top))} — {esc(top.kind)}"]
    return Message(header, [[_line(f)] for f in findings], _button(findings))
