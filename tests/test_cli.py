import tempfile
import unittest
from pathlib import Path

from ops_hub import cli
from ops_hub.state import State

from .helpers import OWNER, Resp, base, make_cfg, make_gh, make_run, utc, workflow_routes

CM = {"repo": "conflict-monitor", "workflow": "daily-brief.yml",
      "slots": [{"days": "daily", "at": "07:43", "grace_min": 125}],
      "artifact": "reports/{Y}/{date}.md"}
RB = {"repo": "Rubbish_Clearance", "workflow": "daily-notify.yml",
      "slots": [{"days": "daily", "at": "07:00", "grace_min": 45}]}

MORNING = utc("2026-10-02T01:53:00Z")     # 09:53 台北
EVENING = utc("2026-10-02T15:23:00Z")     # 23:23 台北


def routes(cm_runs, rb_runs, rb_broken=False):
    r = {}
    r.update(workflow_routes("conflict-monitor", "daily-brief.yml", cm_runs))
    r.update(workflow_routes("Rubbish_Clearance", "daily-notify.yml", rb_runs, name="Daily NTPC Rubbish Notify"))
    if rb_broken:
        r[f"{base('Rubbish_Clearance')}/actions/workflows/daily-notify.yml"] = Resp(500)
        r[f"{base('Rubbish_Clearance')}/actions/workflows/daily-notify.yml/runs"] = Resp(500)
    r[f"{base('conflict-monitor')}/contents/reports/2026/2026-10-02.md"] = {"type": "file"}
    for repo in ("conflict-monitor", "Rubbish_Clearance"):
        r[base(repo)] = {"private": True}
        r[f"{base(repo)}/actions/runs"] = {"workflow_runs": []}
    r[base("ops-hub")] = {"private": False}
    r[f"/users/{OWNER}/settings/billing/usage"] = {"usageItems": [
        {"product": "actions", "unitType": "Minutes", "quantity": 120, "repositoryName": f"{OWNER}/conflict-monitor"}]}
    return r


GOOD_CM = [make_run(1, "2026-10-01T23:43:00Z")]
GOOD_RB = [make_run(2, "2026-10-01T23:00:00Z", repo="Rubbish_Clearance", title="Daily NTPC Rubbish Notify")]


class Sender:
    def __init__(self, ok=True):
        self.ok, self.sent = ok, []

    def __call__(self, msg):
        self.sent.append(msg)
        return self.ok


class RunTest(unittest.TestCase):
    def setUp(self):
        self.cfg = make_cfg([CM, RB])
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "state.json"

    def tearDown(self):
        self.tmp.cleanup()

    def go(self, now, mode, r, sender, state=None):
        gh, _ = make_gh(r)
        state = state or State.load(self.path)
        code = cli.run(self.cfg, gh, state, now, mode, sender=sender, state_path=self.path)
        return code, State.load(self.path)

    def test_all_ok_heartbeat(self):
        s = Sender()
        code, st = self.go(MORNING, "morning", routes(GOOD_CM, GOOD_RB), s)
        self.assertEqual(code, 0)
        self.assertEqual(s.sent, ["✅ 2/2 正常｜本月 Actions 約 120 分（帳單）"])
        self.assertEqual(st.heartbeat_sent, "2026-10-02")

    def test_heartbeat_once_per_day(self):
        s = Sender()
        self.go(MORNING, "morning", routes(GOOD_CM, GOOD_RB), s)
        self.go(utc("2026-10-02T02:47:00Z"), "morning", routes(GOOD_CM, GOOD_RB), s)   # 10:47 備援
        self.assertEqual(len(s.sent), 1)

    def test_failed_send_is_retried_by_backup(self):
        self.go(MORNING, "morning", routes(GOOD_CM, GOOD_RB), Sender(ok=False))
        s = Sender()
        code, st = self.go(utc("2026-10-02T02:47:00Z"), "morning", routes(GOOD_CM, GOOD_RB), s)
        self.assertEqual(len(s.sent), 1)
        self.assertTrue(s.sent[0].startswith("✅"))

    def test_send_failure_exit_code(self):
        code, st = self.go(MORNING, "morning", routes(GOOD_CM, GOOD_RB), Sender(ok=False))
        self.assertEqual(code, 1)
        self.assertEqual(st.heartbeat_sent, "")

    def test_one_repo_broken_others_still_checked(self):
        s = Sender()
        self.go(MORNING, "morning", routes(GOOD_CM, GOOD_RB, rb_broken=True), s)
        msg = s.sent[0]
        self.assertTrue(msg.startswith("⚠️ 1/2 正常"))
        self.assertIn("Rubbish_Clearance — 檢查失敗", msg)
        self.assertIn("HTTP 500", msg)
        self.assertNotIn("conflict-monitor —", msg)

    def test_anomaly_lists_repo_kind_and_link(self):
        s = Sender()
        bad = [make_run(9, "2026-10-01T23:43:00Z", conclusion="cancelled")]
        self.go(MORNING, "morning", routes(bad, GOOD_RB), s)
        self.assertIn("conflict-monitor — 執行異常", s.sent[0])
        self.assertIn("https://github.com/attainnirvana7-bot/conflict-monitor/actions/runs/9", s.sent[0])

    def test_evening_silent_when_ok(self):
        s = Sender()
        cm = GOOD_CM + [make_run(3, "2026-10-02T23:43:00Z")]
        code, _ = self.go(EVENING, "evening", routes(cm, GOOD_RB), s)
        self.assertEqual(code, 0)
        self.assertEqual(s.sent, [])

    def test_evening_only_new_anomalies(self):
        bad = [make_run(9, "2026-10-01T23:43:00Z", conclusion="failure")]
        self.go(MORNING, "morning", routes(bad, GOOD_RB), Sender())       # 早上已推播
        s = Sender()
        self.go(EVENING, "evening", routes(bad, GOOD_RB), s)
        self.assertEqual(s.sent, [])                                       # 晚上不重複
        rb_fail = GOOD_RB + [make_run(4, "2026-10-02T12:00:00Z", conclusion="failure",
                                      repo="Rubbish_Clearance", title="Daily NTPC Rubbish Notify")]
        self.go(EVENING, "evening", routes(bad, rb_fail), s)
        self.assertEqual(len(s.sent), 1)
        self.assertIn("Rubbish_Clearance — 執行異常", s.sent[0])
        self.assertNotIn("conflict-monitor", s.sent[0].split("\n", 1)[1])

    def test_dry_run_does_not_send_or_save(self):
        s = Sender()
        gh, _ = make_gh(routes(GOOD_CM, GOOD_RB))
        code = cli.run(self.cfg, gh, State(), MORNING, "morning", dry_run=True, sender=s, state_path=None)
        self.assertEqual(code, 0)
        self.assertEqual(s.sent, [])
        self.assertFalse(self.path.exists())

    def test_minutes_warning(self):
        r = routes(GOOD_CM, GOOD_RB)
        r[f"/users/{OWNER}/settings/billing/usage"] = {"usageItems": [
            {"product": "actions", "unitType": "minutes", "quantity": 900, "repositoryName": "TW"},
            {"product": "actions", "unitType": "minutes", "quantity": 700, "repositoryName": "FSC"}]}
        s = Sender()
        self.go(MORNING, "morning", r, s)
        self.assertTrue(s.sent[0].startswith("⚠️ 2/2 正常｜本月 Actions 約 1,600 分"))
        self.assertIn("用量偏高", s.sent[0])
        self.assertIn("TW — 分鐘數偏高", s.sent[0])
        self.assertNotIn("FSC — 分鐘數偏高", s.sent[0])

    def test_auto_mode(self):
        self.assertEqual(cli.resolve_mode("auto", MORNING, self.cfg), "morning")
        self.assertEqual(cli.resolve_mode("auto", EVENING, self.cfg), "evening")


if __name__ == "__main__":
    unittest.main()
