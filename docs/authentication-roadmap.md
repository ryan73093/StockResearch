# 帳號與資料所有權路線圖

目前預設是**本機研究模式**。只有在設定真實 Google OAuth 憑證與 redirect URI 後才顯示登入按鈕。

## v2.5 已完成

- Google Web Server Authorization Code Flow、PKCE S256、一次性 state 與瀏覽器 nonce 綁定。
- `platform_users`、`oauth_login_states`、`user_sessions`；Session token 只以 SHA-256 保存。
- HttpOnly、SameSite=Lax cookie；正式 HTTPS 可啟用 Secure。
- 只要求 `openid email profile`，不保存 Google access token 或 refresh token。
- 正式 Dashboard 未登入時為唯讀；FastAPI 寫入端點由獨立 Bearer token 保護。
- `/account` 顯示登入狀態、共用研究資料與個人資料邊界。

## Boundary design

- Market bars, macro data, public news and shared feature definitions remain platform-level data.
- User identity is handled at the web/API boundary, not inside factor, model, backtest or portfolio algorithms.
- Watchlists, broker connections, alerts, saved reports and live portfolios will require an immutable `user_id` owner.
- Research artifacts may be shared or private through an explicit workspace/tenant identifier; ownership is never inferred from an email string.
- Google OAuth provides identity only. Authorization roles (`viewer`, `researcher`, `admin`, `trader`) remain controlled by this platform.
- Flask sessions will store only an opaque session identifier. OAuth tokens and broker credentials must be encrypted at rest and never embedded in reports or logs.

## 待完成

1. 建立 workspace、workspace_members、角色（viewer／researcher／admin／trader）。
2. 將自選股、通知偏好、模擬帳戶、交易與券商連線綁定不可變的 `user_id`。
3. 增加明確 CSRF token、登入稽核、session 管理與全裝置登出。
4. 對個人報告、投資組合與研究實驗加入 workspace 可見性。
5. Google Cross-Account Protection 與管理者停權流程。

Broker execution remains disabled until a separate approval and risk-control module is complete.
