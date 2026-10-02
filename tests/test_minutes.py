import unittest

from ops_hub import minutes

from .helpers import Resp, fixture, make_gh, make_run, utc

OWNER = "attainnirvana7-bot"
TW = "TW_Stock_Investment_Strategy"


def runs_route(all_runs):
    """模擬 GitHub 依 created=>=... 篩選。"""
    def handler(params):
        since = utc(params["created"].lstrip(">="))
        return {"workflow_runs": [r for r in all_runs if utc(r["created_at"]) >= since]}
    return handler


def job(started, completed, conclusion="success"):
    return {"started_at": started, "completed_at": completed, "conclusion": conclusion}


class JobMinutesTest(unittest.TestCase):
    def test_each_job_rounded_up(self):
        now = utc("2026-10-02T00:00:00Z")
        self.assertEqual(minutes.job_minutes(job("2026-10-01T00:00:00Z", "2026-10-01T00:00:10Z"), now), 1)
        self.assertEqual(minutes.job_minutes(job("2026-10-01T00:00:00Z", "2026-10-01T00:01:01Z"), now), 2)
        self.assertEqual(minutes.job_minutes(job("2026-10-01T00:00:00Z", "2026-10-01T00:00:10Z", "skipped"), now), 0)

    def test_recorded_backfill_job(self):
        # 真實的 TW 回補 job：19:46:52 → 00:58:23，5 小時 11 分 31 秒 → 312 分
        j = fixture("tw_jobs_backfill.json")["jobs"][0]
        self.assertEqual(minutes.job_minutes(j, utc("2026-10-02T01:00:00Z")), 312)

    def test_recorded_cancelled_job_still_billed(self):
        j = fixture("conflict_jobs_cancelled.json")["jobs"][0]
        self.assertEqual(minutes.job_minutes(j, utc("2026-09-30T00:00:00Z")), 26)


class EstimateTest(unittest.TestCase):
    def routes(self, runs, jobs):
        r = {f"/repos/{OWNER}/{TW}/actions/runs": runs_route(runs)}
        for rid, js in jobs.items():
            r[f"/repos/{OWNER}/{TW}/actions/runs/{rid}/jobs"] = {"jobs": js}
        return r

    def test_cross_month_and_cache(self):
        runs = [make_run(1, "2026-09-30T23:50:00Z"),        # 台北已是 10/1，但 UTC 還在 9 月：不計入 10 月
                make_run(2, "2026-10-01T00:10:00Z"),
                make_run(3, "2026-10-02T03:00:00Z")]
        jobs = {1: [job("2026-09-30T23:50:00Z", "2026-09-30T23:59:00Z")],
                2: [job("2026-10-01T00:10:00Z", "2026-10-01T00:10:30Z"),     # 1 分
                    job("2026-10-01T00:11:00Z", "2026-10-01T00:13:01Z")],    # 3 分（兩個 job 各自進位）
                3: [job("2026-10-02T03:00:00Z", "2026-10-02T03:04:00Z")]}    # 4 分
        gh, sess = make_gh(self.routes(runs, jobs))
        cache = {"month": "2026-09", "runs": {"1": [TW, 9]}}
        m = minutes.estimate(gh, OWNER, [TW], utc("2026-10-02T05:00:00Z"), cache)
        self.assertEqual(m.total, 8)
        self.assertEqual(m.per_repo[TW], 8)
        self.assertEqual(cache["month"], "2026-10")             # 跨月清除舊快取
        self.assertNotIn("1", cache["runs"])
        self.assertEqual(sess.requests[0][1]["created"], ">=2026-10-01T00:00:00Z")

        # 第二次：已完成的 run 走快取，不再呼叫 jobs API
        gh2, sess2 = make_gh(self.routes(runs, jobs))
        m2 = minutes.estimate(gh2, OWNER, [TW], utc("2026-10-02T06:00:00Z"), cache)
        self.assertEqual(m2.total, 8)
        self.assertFalse(any(p.endswith("/jobs") for p, _ in sess2.requests))

    def test_in_progress_counted_but_not_cached(self):
        runs = [make_run(5, "2026-10-02T03:00:00Z", status="in_progress")]
        jobs = {5: [{"started_at": "2026-10-02T03:00:00Z", "completed_at": None, "conclusion": None}]}
        gh, _ = make_gh(self.routes(runs, jobs))
        cache = {}
        m = minutes.estimate(gh, OWNER, [TW], utc("2026-10-02T03:30:00Z"), cache)
        self.assertEqual(m.total, 30)
        self.assertNotIn("5", cache["runs"])


class BillingTest(unittest.TestCase):
    def test_billing_preferred(self):
        gh, sess = make_gh({f"/users/{OWNER}/settings/billing/usage": fixture("billing_usage.json")})
        m = minutes.compute(gh, OWNER, [TW, "conflict-monitor"], {"USA_Stock_Investment_Strategy", "ops-hub"},
                            utc("2026-10-02T05:00:00Z"), {})
        self.assertEqual(m.source, "帳單")
        self.assertEqual(m.per_repo, {TW: 641, "conflict-monitor": 4})   # 318 + 322.5 進位；排除公開 repo 與儲存量
        self.assertEqual(m.total, 645)
        self.assertEqual(sess.requests[0][1], {"year": 2026, "month": 10})

    def test_billing_forbidden_falls_back_to_estimate(self):
        routes = {f"/users/{OWNER}/settings/billing/usage": Resp(403),
                  f"/repos/{OWNER}/{TW}/actions/runs": {"workflow_runs": []}}
        gh, _ = make_gh(routes)
        m = minutes.compute(gh, OWNER, [TW], set(), utc("2026-10-02T05:00:00Z"), {})
        self.assertEqual(m.source, "估算")
        self.assertIn("403", m.note)


if __name__ == "__main__":
    unittest.main()
