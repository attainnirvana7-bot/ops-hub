"""Telegram 推播：主題、退路、HTML 跳脫、切割、按鈕、查 ID。不需網路。"""

import datetime as dt
import json
import os
import re
import unittest
from unittest import mock

from ops_hub import notify
from ops_hub.notify import Message, esc, link, title


class FakeResp:
    def __init__(self, status=200, body=None):
        self.status_code = status
        self._body = body if body is not None else {"ok": status == 200}

    def json(self):
        return self._body


class FakeSession:
    """依序回傳預先排好的回應，並記下每次送出的 payload。"""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.posts: list[dict] = []
        self.gets: list[tuple] = []

    def post(self, url, json=None, timeout=None):
        self.posts.append(json)
        return self.responses.pop(0) if self.responses else FakeResp()

    def get(self, url, params=None, timeout=None):
        self.gets.append((url, params))
        return self.responses.pop(0)


TOPIC_MISSING = FakeResp(400, {"ok": False, "error_code": 400,
                               "description": "Bad Request: message thread not found"})
ENV = {"WATCHDOG_TG_TOKEN": "123:abc", "WATCHDOG_TG_CHAT": "-1001"}


def balanced(text: str) -> bool:
    return (text.count("<blockquote") == text.count("</blockquote>")
            and len(re.findall(r"<a\b", text)) == text.count("</a>")
            and text.count("<b>") == text.count("</b>"))


def big_message(n=60, width=150) -> Message:
    blocks = [[f"<b>區塊 {i}</b>", esc("x" * width), link(f"https://example.com/{i}?a=1&b=2", "連結")]
              for i in range(n)]
    return Message([title("🛡", "ops-hub 測試", dt.date(2026, 10, 5)), "摘要"], blocks, "https://example.com/run")


def send(msg, session, env=None):
    with mock.patch.dict(os.environ, {**ENV, **(env or {})}):
        return notify.send(msg, session=session, sleep=lambda s: None)


class ThreadTest(unittest.TestCase):
    def test_without_thread_has_no_thread_field(self):
        s = FakeSession()
        self.assertTrue(send(Message(["hi"]), s, {"WATCHDOG_TG_THREAD": ""}))   # Actions 未設定的 vars 是空字串
        self.assertNotIn("message_thread_id", s.posts[0])
        self.assertEqual(s.posts[0]["chat_id"], "-1001")
        self.assertEqual(s.posts[0]["parse_mode"], "HTML")

    def test_with_thread(self):
        s = FakeSession()
        self.assertTrue(send(Message(["hi"]), s, {"WATCHDOG_TG_THREAD": "42"}))
        self.assertEqual(s.posts[0]["message_thread_id"], 42)

    def test_non_numeric_thread_ignored(self):
        with mock.patch.dict(os.environ, {"WATCHDOG_TG_THREAD": "ops-hub"}):
            self.assertIsNone(notify.thread_id())

    def test_missing_topic_falls_back_with_warning(self):
        s = FakeSession(TOPIC_MISSING, FakeResp(), FakeResp())
        msg = big_message(60)
        self.assertTrue(send(msg, s, {"WATCHDOG_TG_THREAD": "999"}))
        first, retry, *rest = s.posts
        self.assertEqual(first["message_thread_id"], 999)
        self.assertNotIn("message_thread_id", retry)
        self.assertTrue(retry["text"].startswith("⚠️ 主題設定有誤（WATCHDOG_TG_THREAD"))
        self.assertTrue(rest, "測試訊息應該被切成多則")
        for p in rest:                                       # 之後各則也不再帶主題
            self.assertNotIn("message_thread_id", p)
            self.assertFalse(p["text"].startswith("⚠️"))
        self.assertTrue(all(len(p["text"]) <= 4096 for p in s.posts))

    def test_other_400_is_failure_not_fallback(self):
        s = FakeSession(FakeResp(400, {"ok": False, "description": "Bad Request: chat not found"}))
        self.assertFalse(send(Message(["hi"]), s, {"WATCHDOG_TG_THREAD": "7"}))
        self.assertEqual(len(s.posts), 1)

    def test_missing_secret_returns_false(self):
        with mock.patch.dict(os.environ, {"WATCHDOG_TG_TOKEN": "", "WATCHDOG_TG_CHAT": ""}):
            self.assertFalse(notify.send(Message(["hi"])))


class RenderTest(unittest.TestCase):
    def test_escape(self):
        self.assertEqual(esc("a<b & c_d"), "a&lt;b &amp; c_d")
        self.assertEqual(link('https://x/?a=1&b="2"', "<t>"),
                         '<a href="https://x/?a=1&amp;b=&quot;2&quot;">&lt;t&gt;</a>')

    def test_title_format(self):
        self.assertEqual(title("🛡", "ops-hub", dt.date(2026, 10, 5)), "<b>🛡 ops-hub</b>｜2026-10-05（一）")

    def test_short_message_single_part_with_expandable_quote(self):
        parts = notify.render_parts(Message(["T", "S"], [["a"], ["b"]]))
        self.assertEqual(parts, ["T\nS\n<blockquote expandable>a\nb</blockquote>"])

    def test_no_blocks_no_quote(self):
        self.assertEqual(notify.render_parts(Message(["T", "S"])), ["T\nS"])

    def test_long_message_split_keeps_tags_balanced(self):
        msg = big_message(80)
        parts = notify.render_parts(msg)
        self.assertGreater(len(parts), 1)
        for i, p in enumerate(parts):
            self.assertLessEqual(len(p), notify.LIMIT)
            self.assertTrue(balanced(p), p[:200])
            self.assertEqual(p.count("<blockquote expandable>"), 1)
            if i:
                self.assertTrue(p.startswith(f"{msg.header[0]}（續 {i + 1}/{len(parts)}）"))
        joined = "".join(parts)
        for i in range(80):                                  # 區塊不被拆開，也沒有遺漏
            self.assertEqual(joined.count(f"<b>區塊 {i}</b>"), 1)

    def test_oversized_block_and_line(self):
        long_line = esc("&<" * 3000)                        # 跳脫後 18,000 字的單行
        msg = Message(["T"], [[f"<b>{'y' * 10}</b>", long_line, link("https://e.com", "L")]])
        parts = notify.render_parts(msg)
        self.assertGreater(len(parts), 2)
        for p in parts:
            self.assertLessEqual(len(p), notify.LIMIT)
            self.assertTrue(balanced(p))
            self.assertNotRegex(p, r"&(?!amp;|lt;|gt;|quot;)")     # 實體沒有被切斷

    def test_button_only_on_last_part(self):
        ps = notify.payloads(big_message(80), "-1", None)
        self.assertGreater(len(ps), 1)
        self.assertTrue(all("reply_markup" not in p for p in ps[:-1]))
        self.assertEqual(ps[-1]["reply_markup"],
                         {"inline_keyboard": [[{"text": "📄 完整報告", "url": "https://example.com/run"}]]})

    def test_no_button_without_url(self):
        ps = notify.payloads(Message(["T"]), "-1", 5)
        self.assertNotIn("reply_markup", ps[0])

    def test_dump_payloads_hides_chat_id(self):
        with mock.patch.dict(os.environ, {**ENV, "WATCHDOG_TG_THREAD": "3"}):
            out = json.loads(notify.dump_payloads(Message(["T"], [], "https://e.com")))
        self.assertEqual(out[0]["chat_id"], "<WATCHDOG_TG_CHAT>")
        self.assertEqual(out[0]["message_thread_id"], 3)
        self.assertNotIn("-1001", json.dumps(out))


class DiscoverTest(unittest.TestCase):
    UPDATES = {"ok": True, "result": [
        {"update_id": 1, "message": {"message_id": 10, "chat": {"id": -100123, "title": "Ops", "type": "supergroup",
                                                               "is_forum": True},
                                     "message_thread_id": 10, "forum_topic_created": {"name": "ops-hub"}}},
        {"update_id": 2, "message": {"message_id": 11, "chat": {"id": -100123, "title": "Ops", "type": "supergroup",
                                                               "is_forum": True},
                                     "message_thread_id": 10, "is_topic_message": True, "text": "hi",
                                     "reply_to_message": {"message_id": 10, "forum_topic_created": {"name": "ops-hub"}}}},
        {"update_id": 3, "message": {"message_id": 12, "chat": {"id": -100123, "title": "Ops", "type": "supergroup",
                                                               "is_forum": True},
                                     "message_thread_id": 20, "is_topic_message": True, "text": "hi"}},
        {"update_id": 4, "message": {"message_id": 13, "chat": {"id": -100123, "title": "Ops", "type": "supergroup",
                                                               "is_forum": True}, "text": "一般區"}},
    ]}

    def test_lists_chat_and_topics_read_only(self):
        s = FakeSession(FakeResp(200, self.UPDATES))
        chats = notify.discover("123:abc", session=s)
        self.assertEqual(s.posts, [])                       # 不發任何訊息
        self.assertIsNone(s.gets[0][1])                     # 不帶 offset / allowed_updates
        c = chats[-100123]
        self.assertTrue(c["is_forum"])
        self.assertEqual(c["topics"][10], "ops-hub")
        self.assertIn("名稱未知", c["topics"][20])
        self.assertTrue(c["general"])
        out = notify.format_discovery(chats)
        self.assertIn("chat id = -100123", out)
        self.assertIn("主題 id = 10", out)

    def test_webhook_conflict(self):
        with self.assertRaises(RuntimeError):
            notify.discover("t", session=FakeSession(FakeResp(409)))

    def test_empty(self):
        self.assertIn("getUpdates 沒有任何訊息", notify.format_discovery({}))


if __name__ == "__main__":
    unittest.main()
