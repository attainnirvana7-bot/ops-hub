# ops-hub

個人專案的 GitHub Actions 看門狗。每天 09:53 推一則心跳（全部正常也會發），23:23 只在有新異常時推播。

```
🛡 ops-hub 心跳｜2026-10-05（一）
✅ 6/6 正常｜本月 Actions 約 412 分（帳單）
```

有異常時，細節放在可展開的引用區塊，底部的「📄 完整報告」按鈕連到最嚴重那筆異常的 Actions 執行頁。
五個專案共用的版面規格見 [`docs/TELEGRAM_STYLE.md`](docs/TELEGRAM_STYLE.md)。

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

## 設定 Telegram 主題群組

五個專案可以改發到同一個開啟「主題（Topics）」的私人群組，每個專案一個主題。
未設定主題變數時，各專案照舊發到原本的 chat，可以逐一切換。

1. 在 Telegram 建立私人群組，群組設定裡開啟「主題（Topics）」，為每個專案建一個主題
   （例如 ops-hub、Fed 總經、衝突熱點、金管會、垃圾清運）。
2. 把各專案用的 bot 都加進群組（看門狗 bot、RegWatch bot，以及其他專案共用的 bot），
   並設為**管理員**（只需要「發送訊息」權限）。不設管理員的話，要先到 BotFather 對該 bot
   `/setprivacy` → Disable，否則 bot 收不到一般訊息、下一步查不到主題。
3. 在**每個主題**各發一句話（任何內容都可以），群組的一般區也發一句。
4. 24 小時內在本機執行（getUpdates 只保留 24 小時內的訊息）：

   ```bash
   export WATCHDOG_TG_TOKEN=...                   # 任何一個已在群組裡的 bot 都可以
   python -m ops_hub.cli --telegram-discover     # 或 --token-env TELEGRAM_BOT_TOKEN 改用別的 bot
   ```

   會列出群組的 chat id（`-100` 開頭）與各主題的 id 和名稱。這個指令只讀不寫，
   不會發送任何訊息，也不會消耗 bot 的更新紀錄。輸出含 chat id，**不要貼到 issue 或 commit**，
   也不要放進 workflow 執行。若顯示 bot 設了 webhook，換一個沒有 webhook 的 bot 執行即可。
5. 依下表到各 repo 的 Settings → Secrets and variables → Actions 填入：

   | repo | Secrets（改成群組 chat id） | Variables（新增：主題 id） |
   |---|---|---|
   | ops-hub | `WATCHDOG_TG_CHAT` | `WATCHDOG_TG_THREAD` |
   | US_Macro_Feeds | `TELEGRAM_CHAT_ID` | `TELEGRAM_THREAD_ID`（選用：`REPORT_BASE_URL`） |
   | conflict-monitor | `TELEGRAM_CHAT_ID` | `TELEGRAM_THREAD_ID`（選用：`REPORT_BASE_URL`） |
   | FSC_Corpus | `REGWATCH_TG_CHAT` | `REGWATCH_TG_THREAD`（選用：`REGWATCH_REPORT_BASE_URL`） |
   | Rubbish_Clearance | `TELEGRAM_CHAT_ID` | `TELEGRAM_THREAD_ID` |

   主題 id 填錯或主題被刪除時，訊息會改發到群組一般區，開頭標示「⚠️ 主題設定有誤」。
6. 每個 repo 都可以用 `--tg-payload` 預覽要送出的內容（JSON），不會真的發送。
