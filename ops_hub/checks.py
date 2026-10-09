"""單一監控對象的檢查：觸發、conclusion、產出物、執行時間、workflow 狀態。

每項檢查各自 try/except，任何一項出錯只會在報告裡多一條「檢查失敗」，
不會中斷同一個 repo 的其他檢查，更不會影響其他 repo。
"""

from __future__ import annotations

import datetime as dt
import logging
import statistics
from dataclasses import dataclass, field

from .config import Config, Target
from .gh import GitHub, GitHubError
from .schedule import SlotInstance, instances

log = logging.getLogger(__name__)

# 視為正常結束的 conclusion（guard 略過的執行也是 success）
OK_CONCLUSIONS = {"success", "skipped", "neutral"}
# 異常：逾時被取消的 conclusion 是 cancelled，只看 failure 會漏掉
BAD_CONCLUSIONS = {"failure", "cancelled", "timed_out", "startup_failure", "action_required", "stale"}

ERROR, WARN, INFO = "error", "warn", "info"


@dataclass
class Finding:
    repo: str
    kind: str
    detail: str = ""
    url: str | None = None
    severity: str = ERROR
    key: str = ""

    def __post_init__(self):
        if not self.key:
            self.key = f"{self.repo}|{self.kind}|{self.detail}"


@dataclass
class TargetResult:
    repo: str
    findings: list[Finding] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not any(f.severity in (ERROR, WARN) for f in self.findings)


def ts(raw: str | None) -> dt.datetime | None:
    if not raw:
        return None
    return dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))


def run_duration(run: dict) -> float | None:
    """completed run 的時長（秒）：run_started_at → updated_at。"""
    if run.get("status") != "completed":
        return None
    start, end = ts(run.get("run_started_at") or run.get("created_at")), ts(run.get("updated_at"))
    if not start or not end:
        return None
    return (end - start).total_seconds()


def _title_matches(run: dict, title: str | None, workflow_name: str) -> bool:
    if not title:
        return True
    shown = run.get("display_title") or ""
    # 尚未設定 run-name 的舊 run，display_title 就是 workflow 名稱：視為可對應任何時段
    return title in shown or shown == workflow_name


def runs_for(inst: SlotInstance, runs: list[dict], workflow_name: str, early_min: int) -> list[dict]:
    lo = inst.start - dt.timedelta(minutes=early_min)
    out = []
    for r in runs:
        created = ts(r.get("created_at"))
        if created and lo <= created <= inst.deadline and _title_matches(r, inst.slot.title, workflow_name):
            out.append(r)
    out.sort(key=lambda r: r["created_at"])
    return out


def expand(template: str, day: dt.date) -> str:
    return template.format(Y=f"{day:%Y}", m=f"{day:%m}", d=f"{day:%d}", date=day.isoformat())


def _guard(result: TargetResult, what: str, fn, *args):
    try:
        return fn(*args)
    except Exception as exc:                     # noqa: BLE001 — 任何錯誤都要進報告
        log.warning("%s：%s 失敗：%s", result.repo, what, exc)
        result.findings.append(Finding(result.repo, "檢查失敗", f"{what}：{exc}", severity=ERROR,
                                       key=f"{result.repo}|檢查失敗|{what}"))
        return None


class TargetChecker:
    def __init__(self, gh: GitHub, cfg: Config, target: Target, now: dt.datetime):
        self.gh, self.cfg, self.t, self.now = gh, cfg, target, now
        self.base = f"/repos/{cfg.owner}/{target.repo}"
        self.result = TargetResult(target.repo)
        self.workflow_name = ""
        self.runs: list[dict] = []

    def add(self, kind: str, detail: str = "", url: str | None = None,
            severity: str = ERROR, key: str = "") -> None:
        self.result.findings.append(Finding(self.t.repo, kind, detail, url, severity, key))

    # ---- 各項檢查 ----
    def check_workflow_state(self) -> None:
        wf = self.gh.get(f"{self.base}/actions/workflows/{self.t.workflow}")
        self.workflow_name = wf.get("name") or ""
        state = wf.get("state", "active")
        if state != "active":
            self.add("workflow 已停用", f"{self.t.workflow} 狀態為 {state}",
                     wf.get("html_url"), key=f"{self.t.repo}|disabled|{state}")

    def load_runs(self) -> bool:
        data = self.gh.get(f"{self.base}/actions/workflows/{self.t.workflow}/runs", {"per_page": 50})
        self.runs = data.get("workflow_runs") or []
        return True

    def check_slot(self, inst: SlotInstance) -> set[int]:
        """評估一個時段，回傳屬於這個時段的 run id（其他檢查不重複回報）。"""
        matched = runs_for(inst, self.runs, self.workflow_name, self.cfg.early_min)
        key = f"{self.t.repo}|{inst.slot.name}|{inst.date}"
        when = f"{inst.label()} 時段"
        if not matched:
            self.add("未觸發", f"{when}沒有任何執行（預期 {inst.deadline:%H:%M} 前觸發）", key=f"{key}|未觸發")
            return set()

        ok = [r for r in matched if r.get("status") == "completed" and r.get("conclusion") in OK_CONCLUSIONS]
        running = [r for r in matched if r.get("status") != "completed"]
        bad = [r for r in matched if r.get("status") == "completed" and r.get("conclusion") in BAD_CONCLUSIONS]

        if ok:
            best = ok[-1]
            if bad:
                kinds = "、".join(sorted({r["conclusion"] for r in bad}))
                self.add("由備援補上", f"{when}另有 {len(bad)} 次 {kinds}", bad[-1].get("html_url"),
                         severity=INFO, key=f"{key}|備援")
            _guard(self.result, "產出物檢查", self.check_artifact, inst, best)
            if self.t.check_duration:
                _guard(self.result, "執行時間檢查", self.check_duration, inst, best)
        elif running:
            best = running[-1]
            started = ts(best.get("run_started_at") or best.get("created_at"))
            elapsed = (self.now - started).total_seconds() / 60 if started else 0
            if elapsed > inst.slot.max_run_min:
                self.add("卡住", f"{when}已執行 {elapsed:.0f} 分仍未結束（上限 {inst.slot.max_run_min} 分）",
                         best.get("html_url"), key=f"{key}|卡住")
            else:
                self.add("執行中", f"{when}已執行 {elapsed:.0f} 分", best.get("html_url"),
                         severity=INFO, key=f"{key}|執行中")
        else:
            best = matched[-1]
            self.add("執行異常", f"{when}結果為 {best.get('conclusion') or best.get('status')}",
                     best.get("html_url"), key=f"{key}|執行異常")
        return {r["id"] for r in matched}

    def check_artifact(self, inst: SlotInstance, run: dict) -> None:
        a = self.t.artifact
        if a is None or (a.slot and a.slot != inst.slot.name):
            return
        if a.only_weekdays and inst.date.weekday() >= 5:
            return
        key = f"{self.t.repo}|{inst.slot.name}|{inst.date}|產出物"
        if a.commit_path:
            since = inst.start.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            commits = self.gh.get(f"{self.base}/commits", {"path": a.commit_path, "since": since, "per_page": 1})
            if not commits and a.ok_if_step and self._step_ran(inst, a.ok_if_step):
                return                    # 例如休市日：抓取與新鮮度檢查都跑完，只是沒有新資料
            if not commits:
                self.add("產出物缺漏", f"{inst.label()} 時段後 {a.commit_path} 沒有新 commit",
                         run.get("html_url"), severity=a.severity, key=key)
            return
        if not a.path:
            return
        tried = []
        for k in range(a.date_slack_days + 1):
            path = expand(a.path, inst.date + dt.timedelta(days=k))
            tried.append(path)
            try:
                self.gh.get(f"{self.base}/contents/{path}")
                return
            except GitHubError as exc:
                if exc.status != 404:
                    raise
        self.add("產出物缺漏", f"找不到 {tried[0]}", run.get("html_url"), severity=a.severity, key=key)

    def _step_ran(self, inst: SlotInstance, step: str) -> bool:
        """時段內是否有成功的 run 真的執行了這個步驟（被 guard 略過的 run 不算）。

        TW 的新鮮度檢查失敗時，最後一步會讓整個 run 變成 failure，所以「run 成功且
        這一步 success」就代表資料是最新的——沒有新 commit 只是因為沒有新交易日。
        """
        runs = runs_for(inst, self.runs, self.workflow_name, self.cfg.early_min)
        for r in reversed(runs):
            if r.get("status") != "completed" or r.get("conclusion") != "success":
                continue
            jobs = self.gh.get(f"{self.base}/actions/runs/{r['id']}/jobs").get("jobs") or []
            if any(s.get("name") == step and s.get("conclusion") == "success"
                   for j in jobs for s in j.get("steps") or []):
                return True
        return False

    def check_duration(self, inst: SlotInstance, run: dict) -> None:
        dur = run_duration(run)
        if dur is None or dur < self.cfg.duration_min_seconds:
            return
        samples = []
        for r in sorted(self.runs, key=lambda r: r.get("created_at", ""), reverse=True):
            if r["id"] == run["id"] or r.get("conclusion") != "success":
                continue
            if not _title_matches(r, inst.slot.title, self.workflow_name):
                continue
            d = run_duration(r)
            if d is not None and d >= self.cfg.duration_min_seconds:   # guard 略過的短 run 不列入
                samples.append(d)
            if len(samples) >= self.cfg.duration_window:
                break
        if len(samples) < self.cfg.duration_min_samples:
            return
        med = statistics.median(samples)
        if dur > self.cfg.duration_factor * med:
            self.add("執行時間異常",
                     f"{inst.label()} 時段執行 {dur / 60:.0f} 分，近 {len(samples)} 次中位數 {med / 60:.1f} 分",
                     run.get("html_url"), severity=WARN, key=f"{self.t.repo}|{inst.slot.name}|{inst.date}|時長")

    def check_other_runs(self, accounted: set[int]) -> None:
        """時段以外的異常 run（例如 GitHub cron 備援、手動執行）。"""
        since = self.now - dt.timedelta(hours=24)
        for r in self.runs:
            created = ts(r.get("created_at"))
            if r["id"] in accounted or not created or created <= since:
                continue
            if r.get("status") != "completed" or r.get("conclusion") not in BAD_CONCLUSIONS:
                continue
            key = f"{self.t.repo}|run|{r['id']}"
            if r["conclusion"] == "cancelled" and self._superseded(r):
                self.add("已被取代而取消", f"{created.astimezone(self.cfg.tz):%m/%d %H:%M} 的執行被取消，鄰近已有成功執行",
                         r.get("html_url"), severity=INFO, key=key)
                continue
            self.add("執行異常", f"{created.astimezone(self.cfg.tz):%m/%d %H:%M}（{r.get('event')}）"
                     f"結果為 {r['conclusion']}", r.get("html_url"), key=key)

    def _superseded(self, run: dict) -> bool:
        """concurrency 佇列排擠造成的取消：前後 2 小時內同類 run 有成功的。"""
        created = ts(run["created_at"])
        for r in self.runs:
            if r["id"] == run["id"] or r.get("conclusion") != "success":
                continue
            if (r.get("display_title") or "") != (run.get("display_title") or ""):
                continue
            other = ts(r.get("created_at"))
            if other and abs((other - created).total_seconds()) <= 7200:
                return True
        return False

    def run(self) -> TargetResult:
        _guard(self.result, "workflow 狀態", self.check_workflow_state)
        if _guard(self.result, "讀取執行紀錄", self.load_runs) is None:
            return self.result        # 讀不到 runs 就無法判斷觸發與否，錯誤已記在報告裡
        accounted: set[int] = set()
        for inst in instances(self.t, self.now, self.cfg.tz):
            ids = _guard(self.result, f"{inst.label()} 時段", self.check_slot, inst)
            accounted |= ids or set()
        _guard(self.result, "其他執行", self.check_other_runs, accounted)
        return self.result


def check_target(gh: GitHub, cfg: Config, target: Target, now: dt.datetime) -> TargetResult:
    try:
        return TargetChecker(gh, cfg, target, now).run()
    except Exception as exc:                     # noqa: BLE001
        log.exception("%s 檢查中斷", target.repo)
        return TargetResult(target.repo, [Finding(target.repo, "檢查失敗", str(exc))])
