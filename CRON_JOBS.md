# cron-job.org 排程一覽

所有每日觸發都由 cron-job.org 呼叫 GitHub 的 `workflow_dispatch` API。
GitHub 內建排程只作備援（見最後一節）。照下表在 cron-job.org 逐筆建立即可。

> **先合併 PR 再建立 #2、#6、#7、#8。** Intl_Reg_Feeds 的 `daily.yml`（含 `skip_if_exists` input）、
> FSC_Corpus、TW_Stock 的 `scheduled` input 都是在 PR 裡才加上的；
> 在 workflow 宣告這個 input 之前呼叫，GitHub 會回 **422**（Intl_Reg_Feeds 合併前則是 404）。

## 共同設定（每一筆都一樣）

| 欄位 | 值 |
|---|---|
| Request method（Advanced） | `POST` |
| Time zone | `Asia/Taipei` |
| Notifications | 勾選「執行失敗時通知」 |

Headers（Advanced → Headers，四行）：

```
Accept: application/vnd.github+json
Authorization: Bearer <cron-job.org 用的 token>
X-GitHub-Api-Version: 2022-11-28
Content-Type: application/json
```

URL 格式：`https://api.github.com/repos/attainnirvana7-bot/<repo>/actions/workflows/<workflow 檔名>/dispatches`

## 排程（9 筆，時間已錯開整點與彼此）

| # | 標題建議 | 時間（台北） | URL | Request body |
|---|---|---|---|---|
| 1 | 垃圾清運通知 | 每日 06:30 | `https://api.github.com/repos/attainnirvana7-bot/Rubbish_Clearance/actions/workflows/daily-notify.yml/dispatches` | `{"ref":"main"}` |
| 2 | 國際監理動態 | 每日 07:17 | `https://api.github.com/repos/attainnirvana7-bot/Intl_Reg_Feeds/actions/workflows/daily.yml/dispatches` | `{"ref":"main","inputs":{"skip_if_exists":"true"}}` |
| 3 | 衝突熱點日報 | 每日 07:43 | `https://api.github.com/repos/attainnirvana7-bot/conflict-monitor/actions/workflows/daily-brief.yml/dispatches` | `{"ref":"main","inputs":{"skip_if_exists":"true"}}` |
| 4 | 美國總經日報 | 每日 08:30 | `https://api.github.com/repos/attainnirvana7-bot/US_Macro_Feeds/actions/workflows/daily.yml/dispatches` | `{"ref":"main"}` |
| 5 | 看門狗心跳 | 每日 09:53 | `https://api.github.com/repos/attainnirvana7-bot/ops-hub/actions/workflows/watch.yml/dispatches` | `{"ref":"main","inputs":{"mode":"morning"}}` |
| 6 | 台股財報月營收 | 週一 11:03 | `https://api.github.com/repos/attainnirvana7-bot/TW_Stock_Investment_Strategy/actions/workflows/scrape.yml/dispatches` | `{"ref":"main","inputs":{"mode":"update","scheduled":"true"}}` |
| 7 | 金管會語料擷取 | 週一至週五 18:07 | `https://api.github.com/repos/attainnirvana7-bot/FSC_Corpus/actions/workflows/daily.yml/dispatches` | `{"ref":"main","inputs":{"scheduled":"true"}}` |
| 8 | 台股日線籌碼 | 每日 22:40 | `https://api.github.com/repos/attainnirvana7-bot/TW_Stock_Investment_Strategy/actions/workflows/scrape.yml/dispatches` | `{"ref":"main","inputs":{"mode":"nightly","scheduled":"true"}}` |
| 9 | 看門狗夜間檢查 | 每日 23:23 | `https://api.github.com/repos/attainnirvana7-bot/ops-hub/actions/workflows/watch.yml/dispatches` | `{"ref":"main","inputs":{"mode":"evening"}}` |

#1、#3、#4 是既有的排程；改用下方統一的 token 時，只需要更新 Authorization header。
`watch.yaml` 的時段時間必須和這張表一致，改了一邊就要改另一邊。

## cron-job.org 用的 token

GitHub → Settings → Developer settings → Personal access tokens → Fine-grained tokens → Generate new token

- Repository access：Only select repositories → 上表 7 個 repo（6 個監控對象 + ops-hub）。
  已經建好的 token 不必重建：到 token 設定頁的 Repository access 加入 Intl_Reg_Feeds 即可（到期日不變）
- Repository permissions：**Actions → Read and write**，其餘一律 No access（這個 token 不能讀寫程式碼）
- Expiration：**請設在 2027 年以後**，避免在 11/15–12/8 出國期間到期
- 建立後把到期日寫進 `watch.yaml` 的 `tokens`，看門狗會在到期前 14 天開始每天提醒

## 回應碼對照

成功時 GitHub 回 **HTTP 204**（沒有內容）。

| 狀態碼 | 原因 |
|---|---|
| 401 | token 錯誤或已過期 |
| 403 | token 沒有 Actions 寫入權限 |
| 404 | URL 打錯，或 token 沒有授權該 repo |
| 422 | body 格式錯誤，或 workflow 尚未宣告 body 裡的 input（PR 還沒合併） |

建好後可以按「Test run」測試。#2、#3、#6、#7、#8 在同一時段已經成功跑過的情況下，
run 會在幾秒內自行略過，不會重複推播，也不會重複抓資料。

## GitHub 內建排程（備援，不必設定）

| repo | 備援時間（台北） | 行為 |
|---|---|---|
| Intl_Reg_Feeds | 每日 07:17、08:47 | 當日報告已存在就略過 |
| conflict-monitor | 每日 07:43、09:47 | 當日報告已存在就略過 |
| US_Macro_Feeds | 每日 09:30 | 當日已推播就略過（`--once-per-day`） |
| FSC_Corpus | 平日 19:37 | 同上 |
| TW_Stock nightly | 每日 23:13 | 12 小時內已有成功的 nightly 就略過 |
| TW_Stock update | 週一 12:17 | 12 小時內已有成功的 update 就略過 |
| TW_Stock morning | 每日 09:07 | 只抓日線；12 小時內已有成功的 nightly 就略過 |
| ops-hub | 每日 10:47（morning）、23:52（evening） | 當天已推過心跳就不再推 |
| Rubbish_Clearance | 沒有備援 | 刻意不設，避免重複通知；漏跑由看門狗回報 |
