"""Telegram 訊息組裝（純文字，不用 parse_mode，避免跳脫問題）。"""

from __future__ import annotations

import datetime as dt

from .checks import ERROR, INFO, WARN, Finding, TargetResult
from .minutes import Minutes

ICON = {ERROR: "❌", WARN: "⚠️", INFO: "ℹ️"}


def _minutes_text(m: Minutes | None) -> str:
    if m is None:
        return "本月 Actions 分鐘數無法取得"
    return f"本月 Actions 約 {m.total:,} 分（{m.source}）"


def _line(f: Finding) -> str:
    who = f.repo if f.repo != "token" else "🔑"
    text = f"{ICON.get(f.severity, '•')} {who} — {f.kind}"
    if f.detail:
        text += f"：{f.detail}"
    if f.url:
        text += f"\n   {f.url}"
    return text


def _sorted(findings: list[Finding]) -> list[Finding]:
    order = {ERROR: 0, WARN: 1, INFO: 2}
    return sorted(findings, key=lambda f: order.get(f.severity, 3))


def heartbeat(results: list[TargetResult], general: list[Finding], minutes: Minutes | None,
              now: dt.datetime) -> str:
    ok = sum(1 for r in results if r.ok)
    total = len(results)
    bad_general = any(f.severity in (ERROR, WARN) for f in general)
    icon = "✅" if ok == total and not bad_general else "⚠️"
    lines = [f"{icon} {ok}/{total} 正常｜{_minutes_text(minutes)}"]
    findings = [f for r in results for f in r.findings] + general
    for f in _sorted(findings):
        lines.append(_line(f))
    if minutes and minutes.note:
        lines.append(f"ℹ️ {minutes.note}")
    return "\n".join(lines)


def alert(findings: list[Finding], minutes: Minutes | None, now: dt.datetime) -> str:
    lines = [f"🌙 ops-hub 檢查發現 {len(findings)} 項新異常｜{_minutes_text(minutes)}"]
    lines += [_line(f) for f in _sorted(findings)]
    return "\n".join(lines)
