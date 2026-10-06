import datetime as dt
import os
import unittest
from unittest import mock

from ops_hub import config, tokens
from ops_hub.gh import GitHubError, parse_expiry

from .helpers import Resp, Seq, make_cfg, make_gh, utc

TODAY = dt.date(2026, 11, 1)


class TokenTest(unittest.TestCase):
    def cfg(self, expires):
        return make_cfg([], tokens=[{"name": "cron-job.org", "expires": expires}])

    def levels(self, expires):
        return [(f.kind, f.severity) for f in tokens.findings(self.cfg(expires), None, TODAY, watchdog_seen=False)]

    def test_thresholds(self):
        self.assertEqual(self.levels("2026-11-16"), [])                              # 剩 15 天
        self.assertEqual(self.levels("2026-11-15"), [("即將到期", "warn")])          # 剩 14 天
        self.assertEqual(self.levels("2026-11-04"), [("即將到期", "warn")])          # 剩 3 天
        self.assertEqual(self.levels("2026-10-31"), [("已過期", "error")])

    def test_watchdog_expiry_from_header(self):
        gh, _ = make_gh({"/x": {}}, headers={"github-authentication-token-expiration": "2026-11-10 00:00:00 UTC"})
        gh.get("/x")
        out = tokens.findings(make_cfg([]), gh.token_expiry, TODAY)
        self.assertEqual(len(out), 1)
        self.assertIn("剩 9 天", out[0].detail)
        self.assertIn("WATCHDOG_TOKEN", out[0].detail)

    def test_parse_expiry_formats(self):
        self.assertEqual(parse_expiry("2026-12-31 08:00:00 +0800"), utc("2026-12-31T00:00:00Z"))
        self.assertEqual(parse_expiry("2026-12-31 00:00:00 UTC"), utc("2026-12-31T00:00:00Z"))
        self.assertIsNone(parse_expiry("garbage"))


class GitHubClientTest(unittest.TestCase):
    def test_retry_then_success(self):
        gh, sess = make_gh({"/x": Seq([Resp(502), Resp(200, {"ok": 1})])})
        self.assertEqual(gh.get("/x"), {"ok": 1})
        self.assertEqual(len(sess.requests), 2)

    def test_permanent_error_not_retried(self):
        gh, sess = make_gh({"/x": Resp(403)})
        with self.assertRaises(GitHubError) as cm:
            gh.get("/x")
        self.assertEqual(cm.exception.status, 403)
        self.assertEqual(len(sess.requests), 1)

    def test_pagination(self):
        page2 = "https://api.github.com/p2?page=2"
        gh, _ = make_gh({"/p1": Resp(200, {"items": [1, 2]}, {"Link": f'<{page2}>; rel="next"'}),
                         "/p2": {"items": [3]}})
        self.assertEqual(gh.paginate("/p1", "items"), [1, 2, 3])


class EnvTest(unittest.TestCase):
    def test_empty_string_uses_default(self):
        with mock.patch.dict(os.environ, {"OPS_HUB_CONFIG": ""}):
            self.assertEqual(config.env("OPS_HUB_CONFIG", "watch.yaml"), "watch.yaml")
        with mock.patch.dict(os.environ, {"OPS_HUB_CONFIG": "x.yaml"}):
            self.assertEqual(config.env("OPS_HUB_CONFIG", "watch.yaml"), "x.yaml")

    def test_sexagesimal_yaml_time(self):
        cfg = config.from_dict({"owner": "o", "targets": [
            {"repo": "r", "workflow": "w.yml", "slots": [{"at": 463}]}]})     # YAML 1.1 的 07:43
        self.assertEqual(cfg.targets[0].slots[0].at, dt.time(7, 43))


if __name__ == "__main__":
    unittest.main()
