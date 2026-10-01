# LINE 通知設定

目標：投入日收盤後把「今日投入建議」推到你的 LINE；每日資料流程或夜間備份失敗時也通知。

做法：LINE Notify 已於 2025-03-31 停止服務，改用 LINE Messaging API 的 push 訊息——由你自己的 LINE 官方帳號（Messaging API channel）傳給你自己的 LINE 帳號。網站只保存 channel access token 與你的 user ID（`.env`），不經過第三方。

## 你需要做的設定（一次）

1. 用 LINE 帳號登入 [LINE Official Account Manager](https://manager.line.biz/)，建立一個官方帳號（名稱例如「盤後決策台」；免費方案即可）。
2. 在該官方帳號的「設定 → Messaging API」按「啟用 Messaging API」，選擇或建立一個 Provider。
3. 到 [LINE Developers Console](https://developers.line.biz/console/) 找到剛建立的 channel：
   - 「Messaging API」分頁最下方 **Channel access token (long-lived)** 按 Issue，複製 token。
   - 「Basic settings」分頁的 **Your user ID**（U 開頭、33 個字元），複製下來。
4. 用手機 LINE 掃描「Messaging API」分頁的 QR code，把官方帳號加為好友（沒加好友就收不到）。
5. 在專案的 `.env` 加入（不要放進版本庫）：

   ```
   LINE_ENABLED=true
   LINE_CHANNEL_ACCESS_TOKEN=<貼上 token>
   LINE_TO=<貼上 U 開頭的 user ID>
   ```

6. 重新啟動服務：`powershell -NoProfile -ExecutionPolicy Bypass -File scripts\deploy.ps1`（13:30–14:40 不要重啟）。
7. 到網站「系統」頁按「傳送 LINE 測試訊息」，手機應立即收到。

## 會收到哪些訊息

| 訊息 | 時機 | 頻率 |
|---|---|---|
| 今天不需操作（下次投入日） | 非投入日的交易日 13:45；`LINE_DAILY_SUMMARY=false` 可關閉 | 每個交易日一則 |
| 今日投入建議（股數、限價、手續費估計） | 投入日 13:45–14:25，收盤資料到了就發 | 每個投入日一則 |
| 投入日提醒（14:15 仍缺收盤，或現金不足 1 股） | 投入日 14:15 後 | 每個投入日最多一則 |
| 每日資料流程失敗 | 13:50 流程或補跑失敗時 | 每天最多一則 |
| 夜間資料庫備份失敗 | 03:00 備份失敗時 | 每天最多一則 |
| 測試訊息 | 系統頁按鈕 | 每按一次一則 |

開啟每日摘要時每月約 20–25 則（每個交易日一則，失敗另計），關閉後約 1–3 則，都在免費方案的每月則數上限內。每則傳送結果記在資料庫 `notification_deliveries`（channel=`line`），系統頁「LINE 通知」方塊顯示最近一則。

## 驗收與疑難

- 系統頁顯示「未設定」：`.env` 的 `LINE_CHANNEL_ACCESS_TOKEN` 或 `LINE_TO` 空白，或服務未重啟。
- 「最近一則失敗：LINE 回應 401」：token 錯誤或已重發（舊 token 失效），重新複製後重啟。
- 「LINE 回應 400」：`LINE_TO` 不是你的 user ID（要用 Basic settings 的 Your user ID，不是官方帳號 ID）。
- 「LINE 回應 429」：超過頻率或本月則數上限。
- 傳送使用 `X-Line-Retry-Key`：網路逾時或 LINE 5xx 時自動重試，同一則不會重複送達；4xx 不重試。

## 待確認事項

- 非投入日的「今天不需操作」是否保留（需求 §11 要求每個交易日都推送；嫌多可設 `LINE_DAILY_SUMMARY=false`）。
- 是否需要研究週報：目前不發。
