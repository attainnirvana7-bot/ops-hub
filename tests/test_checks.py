import datetime as dt
import unittest

from ops_hub.checks import ERROR, INFO, WARN, check_target
from ops_hub.gh import GitHub

from .helpers import (Resp, base, fixture, make_cfg, make_gh, make_run, shift_runs, utc,
                      workflow_routes)

CM = {"repo": "conflict-monitor", "workflow": "daily-brief.yml",
      "slots": [{"days": "daily", "at": "07:43", "grace_min": 125}],
      "artifact": "reports/{Y}/{date}.md"}
FSC = {"repo": "FSC_Corpus", "workflow": "daily.yml",
       "slots": [{"days": "weekdays", "at": "18:07", "grace_min": 120}],
       "artifact": {"path": "corpus/manifest/{date}.json", "date_slack_days": 1}}
TW = {"repo": "TW_Stock_Investment_Strategy", "workflow": "scrape.yml",
      "slots": [{"name": "nightly", "days": "daily", "at": "22:40", "grace_min": 40,
                 "title": "nightly", "max_run_min": 360},
                {"name": "update", "days": ["mon"], "at": "11:03", "grace_min": 90, "title": "update"}],
      "artifact": {"commit_since_slot": "data/fetch_log", "only": "weekdays", "slot": "nightly",
                   "severity": "warn"}}
TW_NAME = "Scrape MOPS financial data"


def kinds(result, severity=None, slot=None):
    return [f.kind for f in result.findings
            if (severity is None or f.severity == severity) and (slot is None or f"|{slot}|" in f.key)]


class ConflictMonitorTest(unittest.TestCase):
    """以 conflict-monitor 真實的執行紀錄為基礎。"""

    def setUp(self):
        self.cfg = make_cfg([CM])
        self.target = self.cfg.targets[0]
        self.runs = fixture("conflict_runs.json")["workflow_runs"]

    def check(self, runs, now, extra=None):
        routes = workflow_routes("conflict-monitor", "daily-brief.yml", runs)
        routes.update(extra or {})
        gh, _ = make_gh(routes)
        return check_target(gh, self.cfg, self.target, now)

    def test_external_trigger_ok(self):
        # 10/02 07:43 台北由 cron-job.org 觸發（真實 run 36942265769），09:53 檢查
        art = {f"{base('conflict-monitor')}/contents/reports/2026/2026-10-02.md": {"type": "file"}}
        res = self.check(self.runs, utc("2026-10-02T01:53:00Z"), art)
        self.assertTrue(res.ok, res.findings)
        self.assertEqual(kinds(res, ERROR), [])

    def test_not_triggered(self):
        # 9/30 的真實情形：GitHub 沒有建立排程事件（只有後來手動補跑）
        res = self.check(self.runs, utc("2026-09-30T01:53:00Z"))
        self.assertFalse(res.ok)
        self.assertIn("未觸發", kinds(res, ERROR))

    def test_cancelled_by_timeout(self):
        # 9/26 08:19 台北的排程 run 逾時被取消（conclusion=cancelled，真實 run 36204400330）
        res = self.check(self.runs, utc("2026-09-26T01:53:00Z"))
        bad = [f for f in res.findings if f.kind == "執行異常"]
        self.assertEqual(len(bad), 1)
        self.assertIn("cancelled", bad[0].detail)
        self.assertTrue(bad[0].url.endswith("/36204400330"))

    def test_timed_out_is_abnormal(self):
        runs = [make_run(1, "2026-10-01T23:43:00Z", conclusion="timed_out")]
        res = self.check(runs, utc("2026-10-02T01:53:00Z"))
        self.assertIn("執行異常", kinds(res, ERROR))

    def test_backup_heals_slot(self):
        runs = [make_run(2, "2026-10-02T01:47:00Z"),                       # 09:47 備援成功
                make_run(1, "2026-10-01T23:43:00Z", conclusion="cancelled")]
        art = {f"{base('conflict-monitor')}/contents/reports/2026/2026-10-02.md": {"type": "file"}}
        res = self.check(runs, utc("2026-10-02T01:53:00Z"), art)
        self.assertTrue(res.ok, res.findings)
        self.assertIn("由備援補上", kinds(res, INFO))

    def test_artifact_missing(self):
        runs = [make_run(1, "2026-10-01T23:43:00Z")]
        res = self.check(runs, utc("2026-10-02T01:53:00Z"))
        miss = [f for f in res.findings if f.kind == "產出物缺漏"]
        self.assertEqual(len(miss), 1)
        self.assertIn("reports/2026/2026-10-02.md", miss[0].detail)

    def test_still_running_is_info_until_limit(self):
        runs = [make_run(1, "2026-10-02T01:40:00Z", status="in_progress")]
        res = self.check(runs, utc("2026-10-02T01:53:00Z"))
        self.assertTrue(res.ok)
        self.assertIn("執行中", kinds(res, INFO))
        res = self.check(runs, utc("2026-10-02T03:30:00Z"))   # 超過 max_run_min 60
        self.assertIn("卡住", kinds(res, ERROR))

    def test_duration_anomaly(self):
        art = {f"{base('conflict-monitor')}/contents/reports/2026/2026-10-02.md": {"type": "file"}}
        history = [make_run(100 + i, f"2026-09-{20 + i}T23:43:00Z", minutes=3) for i in range(8)]
        runs = [make_run(1, "2026-10-01T23:43:00Z", minutes=20)] + history
        res = self.check(runs, utc("2026-10-02T01:53:00Z"), art)
        self.assertIn("執行時間異常", kinds(res, WARN))

    def test_duration_ignores_guard_skips(self):
        # guard 略過的 10 秒 run 不列入中位數，正常的 3 分鐘執行不應被誤判
        art = {f"{base('conflict-monitor')}/contents/reports/2026/2026-10-02.md": {"type": "file"}}
        skips = [make_run(200 + i, f"2026-09-{20 + i}T01:47:00Z", minutes=0.15) for i in range(8)]
        normal = [make_run(100 + i, f"2026-09-{20 + i}T23:43:00Z", minutes=3) for i in range(6)]
        runs = [make_run(1, "2026-10-01T23:43:00Z", minutes=4)] + skips + normal
        res = self.check(runs, utc("2026-10-02T01:53:00Z"), art)
        self.assertNotIn("執行時間異常", kinds(res))

    def test_workflow_disabled(self):
        routes = workflow_routes("conflict-monitor", "daily-brief.yml", [], state="disabled_inactivity")
        gh, _ = make_gh(routes)
        res = check_target(gh, self.cfg, self.target, utc("2026-10-02T01:53:00Z"))
        self.assertIn("workflow 已停用", kinds(res, ERROR))

    def test_other_failed_run_reported_once_per_run(self):
        runs = [make_run(5, "2026-10-01T10:00:00Z", conclusion="failure", event="workflow_dispatch"),
                make_run(1, "2026-10-01T23:43:00Z")]
        res = self.check(runs, utc("2026-10-02T01:53:00Z"))
        other = [f for f in res.findings if f.key.endswith("|run|5")]
        self.assertEqual(len(other), 1)
        self.assertEqual(other[0].severity, ERROR)

    def test_superseded_cancel_is_info(self):
        runs = [make_run(6, "2026-10-01T10:00:00Z", conclusion="cancelled"),
                make_run(7, "2026-10-01T10:05:00Z"),
                make_run(1, "2026-10-01T23:43:00Z")]
        res = self.check(runs, utc("2026-10-02T01:53:00Z"))
        f = [f for f in res.findings if f.key.endswith("|run|6")][0]
        self.assertEqual(f.severity, INFO)

    def test_runs_api_error_reported_not_raised(self):
        routes = workflow_routes("conflict-monitor", "daily-brief.yml", [])
        routes[f"{base('conflict-monitor')}/actions/workflows/daily-brief.yml/runs"] = Resp(500)
        gh, _ = make_gh(routes)
        res = check_target(gh, self.cfg, self.target, utc("2026-10-02T01:53:00Z"))
        self.assertIn("檢查失敗", kinds(res, ERROR))
        self.assertIn("HTTP 500", res.findings[0].detail)


class FscTest(unittest.TestCase):
    def setUp(self):
        self.cfg = make_cfg([FSC])
        self.target = self.cfg.targets[0]

    def test_manifest_dated_next_day_accepted(self):
        # 延遲執行跨過午夜：10/02 的時段產出 10/03 的 manifest
        runs = [make_run(1, "2026-10-02T10:07:00Z", title="RegWatch 每日擷取", repo="FSC_Corpus", minutes=5)]
        routes = workflow_routes("FSC_Corpus", "daily.yml", runs, name="RegWatch 每日擷取")
        routes[f"{base('FSC_Corpus')}/contents/corpus/manifest/2026-10-03.json"] = {"type": "file"}
        gh, _ = make_gh(routes)
        res = check_target(gh, self.cfg, self.target, utc("2026-10-02T15:23:00Z"))
        self.assertTrue(res.ok, res.findings)

    def test_saturday_has_no_expectation(self):
        routes = workflow_routes("FSC_Corpus", "daily.yml", [], name="RegWatch 每日擷取")
        gh, _ = make_gh(routes)
        res = check_target(gh, self.cfg, self.target, utc("2026-10-03T15:23:00Z"))
        self.assertTrue(res.ok)
        self.assertEqual(res.findings, [])


class TwTest(unittest.TestCase):
    def setUp(self):
        self.cfg = make_cfg([TW])
        self.target = self.cfg.targets[0]

    def check(self, runs, now, commits=None):
        routes = workflow_routes("TW_Stock_Investment_Strategy", "scrape.yml", runs, name=TW_NAME)
        routes[f"{base('TW_Stock_Investment_Strategy')}/commits"] = commits if commits is not None else [{"sha": "x"}]
        gh, _ = make_gh(routes)
        return check_target(gh, self.cfg, self.target, now)

    def test_recorded_late_schedule_is_not_triggered(self):
        # 真實紀錄：10/01 22:40 的排程 19:46 UTC（03:46 台北）才開始，早已超過 grace
        runs = fixture("tw_runs.json")["workflow_runs"]
        res = self.check(runs, utc("2026-10-02T01:53:00Z"))
        self.assertIn("未觸發", kinds(res, ERROR))

    def test_title_distinguishes_slots(self):
        # 週一 11:03 的 update 時段內只有 nightly 的 run → update 未觸發
        runs = [make_run(1, "2026-10-05T03:05:00Z", title="scrape · nightly", repo="TW_Stock_Investment_Strategy")]
        res = self.check(runs, utc("2026-10-05T15:23:00Z"))
        self.assertIn("未觸發", kinds(res, ERROR, slot="update"))
        runs = [make_run(1, "2026-10-05T03:05:00Z", title="scrape · update", repo="TW_Stock_Investment_Strategy")]
        res = self.check(runs, utc("2026-10-05T15:23:00Z"))
        self.assertNotIn("未觸發", kinds(res, slot="update"))

    def test_untitled_legacy_run_matches_any_slot(self):
        runs = [make_run(1, "2026-10-05T03:05:00Z", title=TW_NAME, repo="TW_Stock_Investment_Strategy")]
        res = self.check(runs, utc("2026-10-05T15:23:00Z"))
        self.assertNotIn("未觸發", kinds(res, slot="update"))

    def test_long_backfill_running_is_not_stuck(self):
        runs = [make_run(1, "2026-10-02T14:40:30Z", status="in_progress", title="scrape · nightly",
                         repo="TW_Stock_Investment_Strategy")]
        res = self.check(runs, utc("2026-10-02T15:23:00Z"))
        self.assertTrue(res.ok, res.findings)

    def test_no_commit_on_weekday_is_warning(self):
        runs = [make_run(1, "2026-10-01T14:40:30Z", title="scrape · nightly", minutes=2,
                         repo="TW_Stock_Investment_Strategy")]
        res = self.check(runs, utc("2026-10-02T01:53:00Z"), commits=[])
        self.assertIn("產出物缺漏", kinds(res, WARN))

    def holiday(self, freshness: str):
        """10/9（五）國慶補假：22:40 的 nightly 完整跑完，但沒有新交易日，fetch_log 沒有 commit。"""
        cfg = make_cfg([{**TW, "artifact": {**TW["artifact"], "ok_if_step": "Check the daily data is current"}}])
        runs = [make_run(7, "2026-10-09T14:40:21Z", title="scrape · nightly", minutes=2,
                         repo="TW_Stock_Investment_Strategy")]
        routes = workflow_routes("TW_Stock_Investment_Strategy", "scrape.yml", runs, name=TW_NAME)
        routes[f"{base('TW_Stock_Investment_Strategy')}/commits"] = []
        routes[f"{base('TW_Stock_Investment_Strategy')}/actions/runs/7/jobs"] = {"jobs": [{"steps": [
            {"name": "Run daily price update", "conclusion": "success" if freshness == "success" else "skipped"},
            {"name": "Check the daily data is current", "conclusion": freshness}]}]}
        gh, _ = make_gh(routes)
        return check_target(gh, cfg, cfg.targets[0], utc("2026-10-09T15:23:00Z"))

    def test_market_holiday_with_fresh_data_is_not_reported(self):
        res = self.holiday("success")
        self.assertNotIn("產出物缺漏", kinds(res))
        self.assertTrue(res.ok, res.findings)

    def test_skipped_run_still_reports_missing_commit(self):
        # 被 guard 略過的 run 沒有跑新鮮度檢查，不能當作「資料是最新的」
        res = self.holiday("skipped")
        self.assertIn("產出物缺漏", kinds(res, WARN))

    def test_no_commit_on_weekend_is_fine(self):
        runs = [make_run(1, "2026-10-03T14:40:30Z", title="scrape · nightly", minutes=2,
                         repo="TW_Stock_Investment_Strategy")]
        res = self.check(runs, utc("2026-10-04T01:53:00Z"), commits=[])
        self.assertTrue(res.ok, res.findings)


if __name__ == "__main__":
    unittest.main()
