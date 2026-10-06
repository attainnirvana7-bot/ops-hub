"""Telegram 推播（專用 bot）。repo 公開：log 不得出現 token、chat id 或 API 回應本文。

版面規格見 docs/TELEGRAM_STYLE.md：標題列＋摘要＋可展開引用區塊＋「📄 完整報告」按鈕。
發到群組主題時設 WATCHDOG_TG_THREAD；主題不存在時改發到群組一般區並在開頭標示，不靜默失敗。
"""

from __future__ import annotations

import datetime as dt
import html
import json
import logging
import re
import time
from dataclasses import dataclass, field

import requests

from .config import env

log = logging.getLogger(__name__)

API = "https://api.telegram.org/bot{token}/{method}"
LIMIT = 3800                                    # Telegram 單則上限 4096，留給續則標記與主題警示
QUOTE_OPEN, QUOTE_CLOSE = "<blockquote expandable>", "</blockquote>"
BUTTON_TEXT = "📄 完整報告"
THREAD_KEY = "WATCHDOG_TG_THREAD"
WEEKDAY = "一二三四五六日"
# 主題被刪除或關閉時 Telegram 回 400，description 含下列字串之一
TOPIC_ERRORS = ("thread not found", "topic_closed", "topic_deleted", "topic_id_invalid")
TOPIC_WARNING = "⚠️ 主題設定有誤（{key} 指向的主題不存在或已關閉），本則改發到群組一般區。"
_TAG = re.compile(r"<[^>]+>")


def esc(text) -> str:
    return html.escape(str(text), quote=False)


def link(url: str, label: str) -> str:
    return f'<a href="{html.escape(url, quote=True)}">{esc(label)}</a>'


def title(icon: str, name: str, day: dt.date) -> str:
    """第一行固定格式：圖示＋專案名＋台北日期。"""
    return f"<b>{esc(f'{icon} {name}')}</b>｜{day:%Y-%m-%d}（{WEEKDAY[day.weekday()]}）"


@dataclass
class Message:
    """header 與 blocks 的每一行都必須已是安全的 HTML（內容經 esc()、連結經 link()）。

    header：標題列＋摘要，直接顯示。blocks：細節，放進預設摺疊的引用區塊；
    區塊是切割的最小單位，不會被拆到兩則訊息（除非單一區塊本身就超長）。
    """
    header: list[str]
    blocks: list[list[str]] = field(default_factory=list)
    button_url: str | None = None

    @property
    def text(self) -> str:
        return "\n".join(self.header + [line for b in self.blocks for line in b])


def _hard_cut(line: str, size: int) -> list[str]:
    """單行超長：去掉標籤後對純文字重新跳脫再切，不會切斷標籤或 &amp; 之類的實體。"""
    out, buf, n = [], [], 0
    for ch in html.unescape(_TAG.sub("", line)):
        piece = esc(ch)
        if n + len(piece) > size and buf:
            out.append("".join(buf))
            buf, n = [], 0
        buf.append(piece)
        n += len(piece)
    if buf:
        out.append("".join(buf))
    return out


def _pieces(blocks: list[list[str]], room: int) -> list[str]:
    """區塊 → 片段。正常情況一個區塊就是一個片段；超長區塊才在區塊內按行切。"""
    out = []
    for block in blocks:
        lines = [x for line in block for x in ([line] if len(line) <= room else _hard_cut(line, room))]
        buf: list[str] = []
        for line in lines:
            if buf and len("\n".join(buf + [line])) > room:
                out.append("\n".join(buf))
                buf = []
            buf.append(line)
        if buf:
            out.append("\n".join(buf))
    return out


def render_parts(msg: Message, limit: int = LIMIT) -> list[str]:
    """以區塊為單位裝箱；每則的細節各自包一組引用標籤，切點永遠落在標籤之外。"""
    head = "\n".join(msg.header)
    first_line = msg.header[0] if msg.header else ""
    overhead = len(QUOTE_OPEN) + len(QUOTE_CLOSE) + 1
    cont = len(first_line) + 12                 # 續則開頭「標題（續 2/3）」
    room = max(200, limit - overhead - max(len(head), cont))
    groups: list[list[str]] = [[]]
    used = len(head)
    for piece in _pieces(msg.blocks, room):
        if groups[-1] and used + 1 + len(piece) + overhead > limit:
            groups.append([])
            used = cont
        groups[-1].append(piece)
        used += 1 + len(piece)

    parts = []
    for i, group in enumerate(groups):
        top = head if i == 0 else f"{first_line}（續 {i + 1}/{len(groups)}）"
        quote = f"\n{QUOTE_OPEN}" + "\n".join(group) + QUOTE_CLOSE if group else ""
        parts.append(top + quote)
    return parts


def payloads(msg: Message, chat_id: str, thread_id: int | None = None,
             limit: int = LIMIT) -> list[dict]:
    parts = render_parts(msg, limit)
    out = []
    for i, text in enumerate(parts):
        p: dict = {"chat_id": chat_id, "text": text, "parse_mode": "HTML",
                   "disable_web_page_preview": True}
        if thread_id is not None:
            p["message_thread_id"] = thread_id
        if msg.button_url and i == len(parts) - 1:          # 按鈕只附在最後一則
            p["reply_markup"] = {"inline_keyboard": [[{"text": BUTTON_TEXT, "url": msg.button_url}]]}
        out.append(p)
    return out


def thread_id(key: str = THREAD_KEY) -> int | None:
    raw = env(key).strip()
    if not raw:
        return None
    if not raw.lstrip("-").isdigit():
        log.warning("%s 不是數字，忽略（照舊發到群組一般區）", key)
        return None
    return int(raw)


def _topic_missing(resp) -> bool:
    if resp.status_code != 400:
        return False
    try:
        desc = str(resp.json().get("description", "")).lower()
    except ValueError:
        return False
    return any(k in desc for k in TOPIC_ERRORS)


def _post(s, token: str, p: dict, retries: int, sleep) -> bool | str:
    """回傳 True（成功）、False（失敗）或 "topic"（主題不存在）。"""
    for attempt in range(1, retries + 1):
        try:
            resp = s.post(API.format(token=token, method="sendMessage"), json=p, timeout=30)
            if resp.status_code == 200:
                return True
            if p.get("message_thread_id") is not None and _topic_missing(resp):
                return "topic"
            log.warning("Telegram 回傳 HTTP %d (%d/%d)", resp.status_code, attempt, retries)
            if resp.status_code in (400, 401, 403, 404):
                return False                    # 設定錯誤，重試無用
        except requests.RequestException as exc:
            log.warning("Telegram 連線失敗 (%d/%d)：%s", attempt, retries, type(exc).__name__)
        if attempt < retries:
            sleep(2 * attempt)
    return False


def post_all(token: str, items: list[dict], *, session: requests.Session | None = None,
             retries: int = 3, sleep=time.sleep, thread_key: str = THREAD_KEY) -> bool:
    s = session or requests.Session()
    ok, drop_thread = True, False
    for p in items:
        p = dict(p)
        if drop_thread:
            p.pop("message_thread_id", None)
        result = _post(s, token, p, retries, sleep)
        if result == "topic":
            log.warning("%s 指向的主題不存在或已關閉，改發到群組一般區", thread_key)
            drop_thread = True
            p = {k: v for k, v in p.items() if k != "message_thread_id"}
            p["text"] = TOPIC_WARNING.format(key=thread_key) + "\n" + p["text"]
            result = _post(s, token, p, retries, sleep)
        ok = ok and result is True
        sleep(0.5)
    return ok


def send(msg: Message, *, session: requests.Session | None = None, retries: int = 3,
         sleep=time.sleep) -> bool:
    token = env("WATCHDOG_TG_TOKEN")
    chat = env("WATCHDOG_TG_CHAT")
    if not token or not chat:
        log.error("缺少 WATCHDOG_TG_TOKEN / WATCHDOG_TG_CHAT，無法推播。")
        return False
    return post_all(token, payloads(msg, chat, thread_id()), session=session,
                    retries=retries, sleep=sleep)


def dump_payloads(msg: Message) -> str:
    """dry-run 用：chat id 以佔位字串代替，不需要也不讀 token。"""
    return json.dumps(payloads(msg, "<WATCHDOG_TG_CHAT>", thread_id()), ensure_ascii=False, indent=2)


# --------------------------------------------------------------------------- 查 ID

def _topic_name(m: dict) -> str | None:
    for src in (m, m.get("reply_to_message") or {}):
        for key in ("forum_topic_created", "forum_topic_edited"):
            name = (src.get(key) or {}).get("name")
            if name:
                return name
    return None


def discover(token: str, session: requests.Session | None = None) -> dict[int, dict]:
    """用 getUpdates 整理 bot 看得到的群組與主題。

    只讀：不帶 offset（不確認、不消耗更新）、不帶 allowed_updates（那會改變 bot 的設定）、不發訊息。
    """
    s = session or requests.Session()
    resp = s.get(API.format(token=token, method="getUpdates"), timeout=30)
    if resp.status_code == 409:
        raise RuntimeError("這個 bot 設了 webhook，getUpdates 無法使用（本指令不會替你移除 webhook）")
    if resp.status_code != 200:
        raise RuntimeError(f"getUpdates 回傳 HTTP {resp.status_code}")
    chats: dict[int, dict] = {}
    for upd in resp.json().get("result", []):
        m = (upd.get("message") or upd.get("edited_message") or upd.get("channel_post")
             or upd.get("my_chat_member") or {})
        chat = m.get("chat") or {}
        if "id" not in chat:
            continue
        c = chats.setdefault(chat["id"], {"title": chat.get("title") or chat.get("username") or "",
                                          "type": chat.get("type", ""), "is_forum": bool(chat.get("is_forum")),
                                          "topics": {}, "general": False})
        tid = m.get("message_thread_id")
        if tid and (m.get("is_topic_message") or m.get("forum_topic_created")):
            name = _topic_name(m)
            if name or tid not in c["topics"]:
                c["topics"][tid] = name or c["topics"].get(tid) or "（名稱未知：bot 沒看到建立主題的訊息）"
        elif "text" in m or "caption" in m:
            c["general"] = True
    return chats


def format_discovery(chats: dict[int, dict]) -> str:
    if not chats:
        return ("getUpdates 沒有任何訊息。請確認：bot 已加入群組、已設為管理員（或在 BotFather 關閉 privacy mode）、"
                "最近 24 小時內在各主題發過一句話。")
    lines = []
    for cid, c in chats.items():
        forum = "，已開啟主題" if c["is_forum"] else ""
        lines.append(f"{c['title'] or '（無標題）'}　chat id = {cid}（{c['type']}{forum}）")
        for tid, name in sorted(c["topics"].items()):
            lines.append(f"    主題 id = {tid:<8} {name}")
        if c["general"] and c["is_forum"]:
            lines.append("    （一般區的訊息沒有主題 id；不設主題變數就會發到一般區）")
    lines.append("\n群組 chat id 填進各 repo 的 Secret；主題 id 填進各 repo 的 Variable（見 README）。")
    return "\n".join(lines)
