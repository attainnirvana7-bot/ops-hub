"""主流程與診斷指令。

    python -m ops_hub.cli --mode morning     早上：一定推播心跳（同日只推一次）
    python -m ops_hub.cli --mode evening     晚上：只在有新異常時推播
    python -m ops_hub.cli --dry-run          印出報告，不推播、不寫 state
    python -m ops_hub.cli --tg-payload       同 --dry-run，但印出送給 Telegram 的 JSON payload
    python -m ops_hub.cli --check-token      驗證 token 權限與到期日
    python -m ops_hub.cli --telegram-discover  列出 bot 看得到的群組 chat id 與主題 id（只在本機執行）
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path

from . import config as config_mod
from . import minutes as minutes_mod
from . import notify, report, tokens
from .checks import ERROR, WARN, Finding, TargetResult, check_target
from .config import Config, env
from .gh import GitHub, GitHubError
from .state import State

log = logging.getLogger("ops_hub")

SELF_REPO = "ops-hub"
SELF_WORKFLOW = "watch.yml"


@dataclass
class Outcome:
    results: list[TargetResult] = field(default_factory=list)
    general: list[Finding] = field(default_factory=list)
    minutes: minutes_mod.Minutes | None = None

    @property
    def findings(self) -> list[Finding]:
        return [f for r in self.results for f in r.findings] + self.general


def resolve_mode(mode: str, now: dt.datetime, cfg: Config) -> str:
    if mode in ("morning", "evening"):
        return mode
    return "morning" if now.astimezone(cfg.tz).hour < 15 else "evening"


def collect(cfg: Config, gh: GitHub, now: dt.datetime, state: State) -> Outcome:
    out = Outcome()
    private: list[str] = []
    public: set[str] = set()
    for repo in [t.repo for t in cfg.targets] + [SELF_REPO]:
        try:
            meta = gh.get(f"/repos/{cfg.owner}/{repo}")
            (private.append(repo) if meta.get("private") else public.add(repo))
        except Exception as exc:                 # noqa: BLE001 — 錯誤會在該 repo 的檢查中呈現
            log.warning("%s：讀取 repo 資訊失敗：%s", repo, exc)
            if repo != SELF_REPO:
                private.append(repo)             # 保守起見計入分鐘數估算

    for t in cfg.targets:
        out.results.append(check_target(gh, cfg, t, now))

    month = minutes_mod.month_key(now)
    try:
        m = minutes_mod.compute(gh, cfg.owner, private, public, now, state.minutes_cache)
        out.minutes = m
        if m.total >= cfg.minutes_warn_at:
            out.general.append(Finding("Actions 分鐘數", "用量偏高",
                                       f"本月已用 {m.total:,} 分，超過 {cfg.minutes_warn_at:,}（額度 {cfg.minutes_included:,}）",
                                       severity=WARN, key=f"minutes|{month}|total"))
        for repo, used in sorted(m.per_repo.items()):
            if used >= cfg.minutes_per_repo_warn_at:
                out.general.append(Finding(repo, "分鐘數偏高",
                                           f"本月已用 {used:,} 分，超過單一 repo 門檻 {cfg.minutes_per_repo_warn_at:,}",
                                           severity=WARN, key=f"minutes|{month}|{repo}"))
    except Exception as exc:                     # noqa: BLE001
        log.warning("分鐘數計算失敗：%s", exc)
        out.general.append(Finding("Actions 分鐘數", "檢查失敗", str(exc), key="minutes|error"))

    try:
        today = now.astimezone(cfg.tz).date()
        out.general += tokens.findings(cfg, gh.token_expiry, today, watchdog_seen=gh.calls > 0)
    except Exception as exc:                     # noqa: BLE001
        out.general.append(Finding("token", "檢查失敗", str(exc), key="token|error"))
    return out


def run(cfg: Config, gh: GitHub, state: State, now: dt.datetime, mode: str, *,
        dry_run: bool = False, sender=notify.send, state_path: Path | None = None,
        payload: bool = False) -> int:
    mode = resolve_mode(mode, now, cfg)
    today = now.astimezone(cfg.tz).date().isoformat()
    out = collect(cfg, gh, now, state)
    actionable = [f for f in out.findings if f.severity in (ERROR, WARN)]

    heartbeat_due = mode == "morning" and (dry_run or state.heartbeat_sent != today)
    local = now.astimezone(cfg.tz)
    if heartbeat_due:
        msg = report.heartbeat(out.results, out.general, out.minutes, local)
    else:
        new = [f for f in actionable if not state.recently_alerted(f.key, now)]
        msg = report.alert(new, out.minutes, local) if new else None

    log.info("模式 %s：%d 個對象，%d 項異常，API 呼叫 %d 次", mode, len(out.results), len(actionable), gh.calls)
    if dry_run:
        if msg is None:
            print("（沒有新異常，本次不推播）")
        else:
            print(notify.dump_payloads(msg) if payload else msg.text)
        return 0

    sent = True
    if msg:
        sent = sender(msg)
        if sent:
            if heartbeat_due:
                state.heartbeat_sent = today
            state.mark_alerted([f.key for f in actionable], now)
        else:
            log.error("推播失敗；state 不標記已送出，下次觸發會重送")
    state.last_run = now.isoformat(timespec="seconds")
    state.prune(now)
    if state_path:
        state.save(state_path)
    return 0 if sent else 1


def check_token(cfg: Config, gh: GitHub) -> int:
    ok = True
    pairs = [(t.repo, t.workflow) for t in cfg.targets] + [(SELF_REPO, SELF_WORKFLOW)]
    for repo, wf in pairs:
        cells = []
        for label, path in (("Metadata", f"/repos/{cfg.owner}/{repo}"),
                            ("Actions", f"/repos/{cfg.owner}/{repo}/actions/workflows/{wf}"),
                            ("Contents", f"/repos/{cfg.owner}/{repo}/contents/")):
            try:
                gh.get(path)
                cells.append(f"{label} ✅")
            except GitHubError as exc:
                ok = False
                cells.append(f"{label} ❌ {exc.status or '連線失敗'}")
        print(f"{repo:32s} " + "  ".join(cells))

    u = dt.datetime.now(dt.timezone.utc)
    try:
        gh.get(f"/users/{cfg.owner}/settings/billing/usage", {"year": u.year, "month": u.month})
        print("帳單 API                         ✅ 可用（分鐘數以帳單為準）")
    except GitHubError as exc:
        print(f"帳單 API                         ⚠️ 無法使用（{exc.status or '連線失敗'}）：需要帳號層級 Plan: Read，否則改用估算")

    if gh.token_expiry:
        left = (gh.token_expiry - u).days
        print(f"WATCHDOG_TOKEN 到期日            {gh.token_expiry.astimezone(cfg.tz):%Y-%m-%d %H:%M}（剩 {left} 天）")
    else:
        print("WATCHDOG_TOKEN 到期日            回應中沒有到期標頭（未設到期日，或不是 fine-grained token）")
    for t in cfg.tokens:
        print(f"{t.name:32s} {t.expires}（watch.yaml 手動記錄，剩 {(t.expires - u.date()).days} 天）")
    print("\n提醒：fine-grained token 的寫入權限無法用 API 安全地測試，請到 GitHub 設定頁人工確認 "
          "WATCHDOG_TOKEN 只有 Actions / Contents / Metadata 的 Read（與帳號層級 Plan: Read）。")
    return 0 if ok else 1


def telegram_discover(token_env: str) -> int:
    token = env(token_env)
    if not token:
        log.error("缺少 %s", token_env)
        return 2
    try:
        print(notify.format_discovery(notify.discover(token)))
    except RuntimeError as exc:
        log.error("查詢失敗：%s", exc)
        return 1
    except Exception as exc:                     # noqa: BLE001 — 例外訊息可能含帶 token 的網址，只印類型
        log.error("查詢失敗：%s", type(exc).__name__)
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="ops_hub", description="個人專案 GitHub Actions 看門狗")
    p.add_argument("--mode", choices=["morning", "evening", "auto"], default=env("OPS_HUB_MODE", "auto"))
    p.add_argument("--dry-run", action="store_true", help="印出報告，不推播、不寫 state")
    p.add_argument("--tg-payload", action="store_true",
                   help="同 --dry-run，但印出 Telegram payload（JSON，chat id 以佔位字串代替）")
    p.add_argument("--check-token", action="store_true", help="驗證 token 權限與到期日")
    p.add_argument("--telegram-discover", action="store_true",
                   help="用 getUpdates 列出群組 chat id 與主題 id（只讀、不發訊息；輸出含 chat id，只在本機執行）")
    p.add_argument("--token-env", default="WATCHDOG_TG_TOKEN",
                   help="--telegram-discover 讀哪個環境變數當 bot token（預設 WATCHDOG_TG_TOKEN）")
    p.add_argument("--config", default=env("OPS_HUB_CONFIG", "watch.yaml"))
    p.add_argument("--state", default=env("OPS_HUB_STATE", "state/state.json"))
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(levelname)s %(name)s: %(message)s")
    # urllib3 的 debug log 會印出完整網址，公開 repo 不需要
    logging.getLogger("urllib3").setLevel(logging.WARNING)

    if args.telegram_discover:
        return telegram_discover(args.token_env)

    cfg = config_mod.load(args.config)
    token = env("WATCHDOG_TOKEN")
    if not token:
        log.error("缺少 WATCHDOG_TOKEN")
        return 2
    gh = GitHub(token)
    if args.check_token:
        return check_token(cfg, gh)

    state_path = Path(args.state)
    state = State.load(state_path)
    now = dt.datetime.now(dt.timezone.utc)
    dry_run = args.dry_run or args.tg_payload
    return run(cfg, gh, state, now, args.mode, dry_run=dry_run,
               state_path=None if dry_run else state_path, payload=args.tg_payload)


if __name__ == "__main__":
    sys.exit(main())
