# Telegram 推播版面規格

五個專案（ops-hub、US_Macro_Feeds、conflict-monitor、FSC_Corpus、Rubbish_Clearance）都推播到同一個
開啟「主題（Topics）」的私人群組，每個專案一個主題。不建共用套件：各 repo 依本規格各自實作，
參考實作是本 repo 的 `ops_hub/notify.py`。

## 1. 訊息結構

```
<b>{圖示} {專案名}</b>｜2026-10-05（一）     ← 第一行，固定格式，台北日期
摘要第 1 行                                  ← 2–3 行重點，不摺疊
摘要第 2 行
<blockquote expandable>細節…</blockquote>     ← 可展開引用區塊，預設摺疊
[ 📄 完整報告 ]                               ← inline keyboard URL 按鈕
```

- `parse_mode` 一律 `HTML`，`disable_web_page_preview: true`。
- 標題與摘要直接可見；細節（條目清單、連結、各區段）全部放進引用區塊。
- 沒有細節時不輸出引用區塊；沒有報告網址時不附按鈕。

| 專案 | 標題 | 摘要 | 按鈕 |
|---|---|---|---|
| ops-hub | 🛡 ops-hub 心跳／ops-hub 異常 | `✅ 6/6 正常｜本月約 412 分`、各級異常數 | 最嚴重那筆異常的 Actions run 頁；全部正常不附 |
| US_Macro_Feeds | 🏛 Fed／BLS 總經 | 新項目／重點數、下次 FOMC、10Y 與 10Y−2Y | 當日 `reports/Y/m/date.md` |
| conflict-monitor | 🌐 全球衝突熱點 | 新增則數、熱度前兩區、摘要降級警示 | 當日 `reports/Y/date.md` |
| FSC_Corpus | ⚖️ 金管會監理動態 | 則數（施政計畫對齊數）、發文量異常 | 當日 `briefs/Y/m/date.md` |
| Rubbish_Clearance | 🗑 新北垃圾清運 · {地點} | 清運點筆數 | 無 |

Rubbish_Clearance 訊息很短，只套用第一行格式，維持純文字（不設 parse_mode）、不加引用區塊與按鈕。

## 2. HTML 跳脫

- 所有來自外部的內容（標題、摘要、repo 名稱、錯誤訊息）一律 `html.escape(s, quote=False)`。
- 連結：`<a href="{html.escape(url, quote=True)}">{html.escape(label)}</a>`。
- 只用 Telegram 支援的標籤：`b`、`i`、`a`、`blockquote expandable`。不輸出 `<br>`，換行用 `\n`。

## 3. 長訊息切割

Telegram 單則上限 4096 字，切割上限取 3800（留給續則標記與主題警示）。

1. 細節由「區塊」組成（例如一個區域、一則條目、一筆異常）。區塊是切割的最小單位。
2. 依序把區塊裝進訊息；裝不下就開新的一則。每則的細節各自包一組
   `<blockquote expandable>…</blockquote>`，所以切點永遠落在標籤之外。
3. 單一區塊超長時，才在區塊內按行切開，每段各自補回開合標籤。
4. 單行超長時，去除標籤、對純文字重新跳脫後硬切（不會切斷 `&amp;` 之類的實體）。
5. 第二則起的開頭為「{第一行}（續 2/3）」。
6. **按鈕只附在最後一則。**

## 4. 主題（message_thread_id）

| 專案 | Secret（群組 chat id） | Variable（主題 id） |
|---|---|---|
| ops-hub | `WATCHDOG_TG_CHAT` | `WATCHDOG_TG_THREAD` |
| US_Macro_Feeds | `TELEGRAM_CHAT_ID` | `TELEGRAM_THREAD_ID` |
| conflict-monitor | `TELEGRAM_CHAT_ID` | `TELEGRAM_THREAD_ID` |
| FSC_Corpus | `REGWATCH_TG_CHAT` | `REGWATCH_TG_THREAD` |
| Rubbish_Clearance | `TELEGRAM_CHAT_ID` | `TELEGRAM_THREAD_ID` |

- 主題 id 不是機密，放 GitHub **Variables**；群組 chat id 仍放 **Secrets**。
- 讀取一律 `os.environ.get(KEY) or None`（Actions 未設定的 vars 會傳入空字串）；非數字視同未設定並記警告。
- 未設定時不送 `message_thread_id`，行為與改版前完全相同。
- **退路**：送出回 400 且 description 含 `message thread not found`（或 `TOPIC_CLOSED`／`TOPIC_DELETED`／
  `TOPIC_ID_INVALID`）時，不帶主題重送一次，並在該則開頭加
  `⚠️ 主題設定有誤（{變數名} 指向的主題不存在或已關閉），本則改發到群組一般區。`；
  同次後續各則也不帶主題。不得靜默失敗。log 只記變數名與狀態碼，不印回應本文、chat id、token。
- workflow 內的 curl 失敗通知同樣支援主題 id 與上述退路。

## 5. 按鈕網址

- 基底可設定：Variable `REPORT_BASE_URL`（FSC 為 `REGWATCH_REPORT_BASE_URL`），未設定時預設
  `https://github.com/{GITHUB_REPOSITORY}/blob/main`。按鈕網址 = 基底 + `/` + 報告相對路徑。
  日後改指向儀表板網站時，只需改這個變數。
- ops-hub 的按鈕直接用 GitHub API 回傳的 run `html_url`，沒有報告檔，因此沒有基底設定。

## 6. Dry-run

每個 repo 都有 `--tg-payload`：跑完整流程但不寫檔、不推播，印出要送給 Telegram 的 JSON payload。
chat id 以 `<變數名>` 佔位，不需要 token。FSC_Corpus 的 `brief` 會把簡報印到 stdout，
所以 payload 改印到 stderr。

## 7. 測試清單（每個 repo 都要有）

- 有／無主題 id（含空字串）。
- 主題不存在時的退路：第二次請求不帶主題、開頭有 ⚠️。
- HTML 跳脫：`<`、`&`、`_`、引號。
- 長訊息切割：每則 ≤ 上限、`<blockquote`／`</blockquote>`、`<a`／`</a>`、`<b>`／`</b>` 數量平衡。
- 按鈕只出現在最後一則。
