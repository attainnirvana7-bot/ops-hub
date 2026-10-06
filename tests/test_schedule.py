import datetime as dt
import unittest

from ops_hub.config import describe_days, parse_days
from ops_hub.schedule import instances

from .helpers import make_cfg, utc

FSC = {"repo": "FSC_Corpus", "workflow": "daily.yml",
       "slots": [{"days": "weekdays", "at": "18:07", "grace_min": 120}]}
SAMPLE = {"repo": "Sample_Repo", "workflow": "sample.yml",
        "slots": [{"days": ["wed", "sat"], "at": "08:17", "grace_min": 90}]}


class ScheduleTest(unittest.TestCase):
    def setUp(self):
        self.cfg = make_cfg([FSC, SAMPLE])
        self.fsc, self.sample = self.cfg.targets

    def test_parse_days(self):
        self.assertEqual(parse_days("weekdays"), frozenset(range(5)))
        self.assertEqual(parse_days(["Wed", "sat"]), frozenset({2, 5}))
        self.assertEqual(describe_days(parse_days("daily")), "每日")
        self.assertEqual(describe_days(parse_days(["wed", "sat"])), "週三、六")
        with self.assertRaises(ValueError):
            parse_days(["xyz"])

    def test_weekday_slot_evaluated_on_friday_evening(self):
        # 2026-10-02 是週五；23:23 台北 = 15:23 UTC
        got = instances(self.fsc, utc("2026-10-02T15:23:00Z"), self.cfg.tz)
        self.assertEqual([i.date.isoformat() for i in got], ["2026-10-02"])

    def test_weekend_not_expected(self):
        # 週六晚上：週六不應有 FSC 時段；週五的時段已超過 24 小時回看
        self.assertEqual(instances(self.fsc, utc("2026-10-03T15:23:00Z"), self.cfg.tz), [])
        # 週日早上、週一早上都沒有應評估的時段（週一 18:07 尚未到 grace）
        self.assertEqual(instances(self.fsc, utc("2026-10-04T01:53:00Z"), self.cfg.tz), [])
        self.assertEqual(instances(self.fsc, utc("2026-10-05T01:53:00Z"), self.cfg.tz), [])

    def test_friday_slot_rechecked_saturday_morning(self):
        got = instances(self.fsc, utc("2026-10-03T01:53:00Z"), self.cfg.tz)   # 週六 09:53
        self.assertEqual([i.date.isoformat() for i in got], ["2026-10-02"])

    def test_specific_weekdays(self):
        # 週三 09:53：當日 08:17 + 90 分 = 09:47 已過
        got = instances(self.sample, utc("2026-09-30T01:53:00Z"), self.cfg.tz)
        self.assertEqual([i.date.isoformat() for i in got], ["2026-09-30"])
        # 週四 09:53：週三的 deadline 已超過 24 小時，週四沒有時段
        self.assertEqual(instances(self.sample, utc("2026-10-01T01:53:00Z"), self.cfg.tz), [])

    def test_grace_not_yet_passed(self):
        # 週三 09:40：deadline 09:47 還沒到
        self.assertEqual(instances(self.sample, utc("2026-09-30T01:40:00Z"), self.cfg.tz), [])

    def test_slot_uses_taipei_date(self):
        got = instances(self.sample, utc("2026-09-30T01:53:00Z"), self.cfg.tz)[0]
        self.assertEqual(got.start, dt.datetime(2026, 9, 30, 8, 17, tzinfo=self.cfg.tz))
        self.assertEqual(got.start.astimezone(dt.timezone.utc).date().isoformat(), "2026-09-30")


if __name__ == "__main__":
    unittest.main()
