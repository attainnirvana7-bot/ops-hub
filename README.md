# ops-hub

個人專案的 GitHub Actions 看門狗。每天 09:53 推一則心跳（全部正常也會發），23:23 只在有新異常時推播。

```
✅ 6/6 正常｜本月 Actions 約 412 分（帳單）
```

會檢查：時段內是否有觸發、conclusion（failure / cancelled / timed_out…）、產出物是否存在、
執行時間是否暴增、workflow 是否被停用、本月 Actions 分鐘數，以及 token 是否即將到期。

> 本 repo 是公開的：監控對象的 repo 名稱、排程時間與異常類型會公開可見。

- 設定：`watch.yaml`
- cron-job.org 排程：[`CRON_JOBS.md`](CRON_JOBS.md)
- 架構、設計決策、診斷指令、Secrets：[`CLAUDE.md`](CLAUDE.md)

## 第一次設定

1. 把 repo 設為 Public（Settings → General → Danger Zone → Change visibility）。
2. 建立看門狗專用的 Telegram bot（BotFather），取得 chat id。
3. 建立 `WATCHDOG_TOKEN`（fine-grained token；權限見 CLAUDE.md），連同 bot token、chat id
   一起存成 Secrets：`WATCHDOG_TOKEN`、`WATCHDOG_TG_TOKEN`、`WATCHDOG_TG_CHAT`。
4. 到 Actions → Watchdog → Run workflow，mode 選 morning：應該會收到心跳。
5. 合併 FSC_Corpus、TW_Stock、USA_Stock 的 PR 之後，照 `CRON_JOBS.md` 建立 cron-job.org 排程。
6. 把 cron-job.org token 的到期日寫進 `watch.yaml`。
