# ops-hub

個人專案的 GitHub Actions 看門狗。每天早晚各檢查一次 6 個 repo 的每日 workflow，
推播到 Telegram：早上一定發一則「心跳」，晚上只在有新異常時才發。
**「早上沒收到心跳」本身就是警訊。**

repo 是**公開**的（不耗 Actions 分鐘數）：repo 名稱、排程時間、異常類型都是公開可見的，
已知並接受。log 和 commit 內容不得出現 token、chat id 或 API 回應本文。

---

## 架構

```
cron-job.org（09:53 morning、23:23 evening 台北時間）→ workflow_dispatch API
GitHub Actions 內建排程（10:47、23:52，僅作備援）
   │
   ├─ 讀 watch.yaml
   ├─ 逐 repo 檢查（WATCHDOG_TOKEN，唯讀）
   │    workflow 狀態 → 近 50 次 runs → 逐時段評估 → 產出物 → 執行時間 → 時段外的異常 run
   ├─ 本月分鐘數：帳單 API，失敗時逐 job 無條件進位估算
   ├─ token 到期：WATCHDOG_TOKEN 讀回應標頭，cron-job.org token 讀 watch.yaml
   ├─ 推播：morning = 心跳（同日一次）；evening / 重複觸發 = 只推 24 小時內未推過的異常
   └─ commit state/state.json（用內建 GITHUB_TOKEN，只寫本 repo）
```

### 檔案結構

```
watch.yaml                       監控對象、門檻、手動記錄的 token 到期日（日常只需改這個）
CRON_JOBS.md                     cron-job.org 全部 9 筆排程，照抄即可
.github/workflows/watch.yml      排程、執行、commit state、看門狗自身失敗通知
ops_hub/
  cli.py        主流程、--dry-run、--check-token
  config.py     watch.yaml → Config / Target / Slot；env() 處理空字串
  gh.py         GitHub API：重試/退避、永久錯誤不重試、分頁、token 到期標頭
  schedule.py   以台北時間展開時段，決定本次要評估哪些
  checks.py     單一 repo 的所有檢查與 Finding 模型
  minutes.py    本月分鐘數（帳單 / 估算 + 快取）
  tokens.py     token 到期提醒
  state.py      心跳日期、已推播警示、分鐘數快取
  report.py     Telegram 訊息（HTML：標題列＋摘要＋可展開細節＋按鈕）
  notify.py     Telegram 推播（專用 bot）、主題退路、切割、--telegram-discover
docs/TELEGRAM_STYLE.md           五個專案共用的推播版面規格
tests/          unittest；fixtures 是錄下來的真實 API 回應
state/state.json
```

---

## 監控對象

| repo | workflow | 時段（台北） | grace | 產出物 | 觸發 |
|---|---|---|---|---|---|
| conflict-monitor | daily-brief.yml | 每日 07:43 | 125 分（含 09:47 備援） | `reports/{Y}/{date}.md` | cron-job.org + GitHub 備援 |
| US_Macro_Feeds | daily.yml | 每日 08:30 | 80 分（含 09:30 備援） | `reports/{Y}/{m}/{date}.md` | 同上 |
| Rubbish_Clearance | daily-notify.yml | 每日 06:30 | 45 分 | 無（只推播） | 只有 cron-job.org |
| FSC_Corpus | daily.yml | 平日 18:07 | 120 分（含 19:37 備援） | `corpus/manifest/{date}.json`（容許 +1 天） | cron-job.org + GitHub 備援 |
| TW_Stock_Investment_Strategy | scrape.yml | nightly 每日 22:40、update 週一 11:03 | 40 / 90 分 | 平日夜間時段後 `data/fetch_log` 要有 commit（警告級） | 同上；另有 09:07 morning 純 GitHub 備援 |
| USA_Stock_Investment_Strategy（公開） | fmp-run.yml | 週三、六 08:17 | 90 分（含 09:27 備援） | `reports/{Y}/{date}.md` | 同上 |

FSC、TW、USA 三個 repo 的外部觸發與 guard 由各自的 PR 加入（`scheduled` input）。
**PR 合併前**：TW 的 run 標題還是 workflow 名稱，看門狗會把它視為可以對應任何時段，不會誤判成未觸發。

---

## 現況與已知問題

- 實測紀錄（寫進 fixtures 的真實資料）：conflict-monitor 的 GitHub 排程 07:43 在 10/2 拖到 15:33 才跑；
  TW 的 22:40 在 9/30、10/1 拖到隔天 03:36、03:46；FSC 的 18:00 自 9/29 起拖到 00:06–00:53。
  這就是主要觸發改用 cron-job.org 的原因。
- TW 籌碼回補期間每次 nightly 約 5.3 小時（每月可能超過 1,500 分鐘）；補完後每次只要一兩分鐘。
  單一 repo 超過 800 分鐘會另外警告。
- 帳單 API 需要 token 有帳號層級的 **Plan: Read**；沒有的話分鐘數改用估算，心跳會標「（估算）」。
  估算只涵蓋 watch.yaml 裡的私有 repo，不含帳號下其他私有 repo。

---

## 重要設計決策（改動前請先理解）

**以「時段」評估，而不是看「最近一次 run」。** 每個時段在
`[at − 10 分, at + grace]` 之間至少要建立一個 run。在 grace 過後的第一次檢查評估
（回看 24 小時，所以每個時段早晚至少各評估一次）。只要時段內有一次成功，就算正常
（備援補上的會以 ℹ️ 註明）。grace 必須涵蓋該 repo 的 GitHub cron 備援時間，
而且 `at + grace` 要早於檢查時間（09:53 / 23:23），否則要到下一次檢查才會評估到。

**異常 conclusion 包含 cancelled。** 逾時被取消的 conclusion 是 `cancelled`，不是 `failure`
（conflict-monitor 9/22、9/26、9/29 都是這樣靜默失敗的）。`failure / cancelled / timed_out /
startup_failure / action_required / stale` 一律算異常。例外：被 concurrency 佇列排擠而取消、
前後 2 小時內同類 run 有成功的，降為 ℹ️。

**「沒觸發」只有外部看門狗抓得到。** workflow 內的失敗通知在根本沒觸發時不會執行。
看門狗自己沒觸發的情況，則靠「早上沒收到心跳」發現。

**心跳同日只推一次，送出成功後才記錄。** `state.json` 的 `heartbeat_sent`（台北日期）
在 Telegram 回 200 之後才寫入；送失敗時 exit 1，10:47 的備援會再送一次。
同日的重複觸發會改走 evening 邏輯（只推新異常）。

**新異常以 key 去重 24 小時。** key 是 `repo|時段|日期|類型` 或 `repo|run|id`。
早上心跳列出的異常，晚上不會再推一次；隔天早上的心跳會再列出仍存在的異常。

**執行時間只跟同一時段比。** TW 用 run 標題（`scrape · nightly`）區分時段，
回補期的 5 小時不會被拿去跟 3 分鐘的 morning 比。短於 2 分鐘的 run（guard 略過的）
不列入中位數；樣本少於 5 個時不判斷。

**guard 用「12 小時內有成功 run」判斷，不看日期**（FSC / TW / USA）。延遲的備援可能
跨過台北午夜才跑，用日期判斷會以為今天還沒跑。只有 success 才算，所以主要觸發失敗時，
備援會自動重試一次。

**分鐘數以 UTC 曆月計算，逐 job 無條件進位。** 這跟 GitHub 計費一致。已完成 run 的
分鐘數快取在 state，跨月時清除，所以之後只會增量呼叫 jobs API。

**每項檢查獨立 try/except。** 任何錯誤都會變成「檢查失敗」列在報告裡，不會中斷其他檢查；
該 repo 會被算成不正常（❌），所以「看門狗讀不到資料」不會偽裝成「一切正常」。

**推播版面依 `docs/TELEGRAM_STYLE.md`。** 內容一律經 `notify.esc()`；切割以區塊為單位、
按鈕只在最後一則。主題不存在時不帶主題重送並標示，不得靜默失敗。

**公開 repo 的 log 紀律。** `gh.py` 只記狀態碼與路徑；`notify.py` 不記 token、chat id、回應本文
（requests 例外訊息含帶 token 的網址，只記例外類型）；
urllib3 的 debug log 關閉（它會印完整網址）。state 只存去重必需的資料，詳細報告只走 Telegram。

---

## 診斷指令

```bash
export WATCHDOG_TOKEN=...                        # 本機測試用
python -m ops_hub.cli --check-token              # 逐 repo 驗證 Metadata / Actions / Contents 讀取、帳單 API、到期日
python -m ops_hub.cli --dry-run --mode morning   # 印出心跳內容，不推播、不寫 state
python -m ops_hub.cli --dry-run --mode evening   # 印出晚上會推的新異常
python -m ops_hub.cli --tg-payload --mode morning  # 印出送給 Telegram 的 JSON payload（chat id 以佔位字串代替）
python -m ops_hub.cli --telegram-discover        # 用 getUpdates 列出群組 chat id 與主題 id（只讀；只在本機執行）
python -m ops_hub.cli -v                         # 完整執行（詳細 log）
python -m unittest discover -s tests -t . -v     # 測試（不需網路）
```

排查順序：先跑 `--check-token`，再跑 `--dry-run`，對照各 repo 的 Actions 頁面，最後才跑完整流程。

---

## 環境變數 / Secrets

| 名稱 | 種類 | 必要 | 用途 |
|---|---|---|---|
| `WATCHDOG_TOKEN` | Secret | 是 | fine-grained token：6 個監控 repo + ops-hub，Actions / Contents / Metadata **Read**；帳號層級 Plan: Read（帳單 API） |
| `WATCHDOG_TG_TOKEN` | Secret | 是 | 看門狗專用 Telegram bot（BotFather） |
| `WATCHDOG_TG_CHAT` | Secret | 是 | 推播對象 chat id（主題群組的 chat id） |
| `WATCHDOG_TG_THREAD` | Variable | 否 | 群組主題 id；未設定時發到 chat 本身。主題不存在時改發一般區並標 ⚠️ |
| `OPS_HUB_MODE` | env | 否 | morning / evening / auto（workflow 自動設定） |
| `OPS_HUB_CONFIG` / `OPS_HUB_STATE` | env | 否 | 預設 `watch.yaml` / `state/state.json` |

cron-job.org 使用另一個 token（只有 Actions Read and write），只存在 cron-job.org，
它的到期日要手動寫進 `watch.yaml` 的 `tokens`。

本 repo 對其他 repo **沒有任何寫入權限**：寫入只用內建的 `GITHUB_TOKEN`（`contents: write`，限本 repo）。
fine-grained token 的寫入權限無法用 API 安全地測試，換 token 時請到設定頁人工確認。

注意：GitHub Actions 中未設定的 vars 會傳入**空字串**，而不是不存在，所以一律要寫
`config.env(KEY, default)`（內部是 `os.environ.get(KEY) or default`）。

---

## 慣例

- 對話與註解使用台灣正體中文
- 相依套件只用 requests、PyYAML；測試用 unittest
- 時間一律以 `Asia/Taipei` 解讀 watch.yaml；GitHub API 時間是 UTC
- 新增監控對象：在 `watch.yaml` 加一筆，在 `CRON_JOBS.md` 加對應排程，
  並確認 grace 涵蓋備援、`at + grace` 早於 09:53 或 23:23
