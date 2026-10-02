"""Telegram 推播（專用 bot）。repo 公開：log 不得出現 token 或 chat id。"""

from __future__ import annotations

import logging
import time

import requests

from .config import env

log = logging.getLogger(__name__)

API = "https://api.telegram.org/bot{token}/sendMessage"
CHUNK = 3500


def chunks(text: str, size: int = CHUNK) -> list[str]:
    """以行為界切割（Telegram 單則上限 4096 字）。"""
    out, buf = [], ""
    for line in text.split("\n"):
        while len(line) > size:                 # 單行過長時硬切
            if buf:
                out.append(buf.rstrip())
                buf = ""
            out.append(line[:size])
            line = line[size:]
        if len(buf) + len(line) + 1 > size and buf:
            out.append(buf.rstrip())
            buf = ""
        buf += line + "\n"
    if buf.strip():
        out.append(buf.rstrip())
    return out


def send(text: str, *, session: requests.Session | None = None, retries: int = 3,
         sleep=time.sleep) -> bool:
    token = env("WATCHDOG_TG_TOKEN")
    chat = env("WATCHDOG_TG_CHAT")
    if not token or not chat:
        log.error("缺少 WATCHDOG_TG_TOKEN / WATCHDOG_TG_CHAT，無法推播。")
        return False
    s = session or requests.Session()
    ok = True
    for part in chunks(text):
        for attempt in range(1, retries + 1):
            try:
                resp = s.post(API.format(token=token), json={
                    "chat_id": chat, "text": part, "disable_web_page_preview": True,
                }, timeout=30)
                if resp.status_code == 200:
                    break
                log.warning("Telegram 回傳 HTTP %d (%d/%d)", resp.status_code, attempt, retries)
                if resp.status_code in (400, 401, 403, 404):
                    attempt = retries               # 設定錯誤，重試無用
            except requests.RequestException as exc:
                log.warning("Telegram 連線失敗 (%d/%d)：%s", attempt, retries, type(exc).__name__)
            if attempt >= retries:
                ok = False
                break
            sleep(2 * attempt)
        sleep(0.5)
    return ok
