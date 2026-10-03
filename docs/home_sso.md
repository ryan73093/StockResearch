

## 2026-10-02 — Home 統一登入整合

使用者於 2026-10-02 核定：以 Home 帳密登入、各系統直接網址仍需驗證與授權，並保留離線／公司內網部署。

- `HOME_SSO_CONFIG` 指向私有 `.runtime/home-sso.json`；啟用時每次受保護請求向 Home 確認工作階段、角色與系統授權。無法確認就拒絕存取。
- 各站用自己的 Secure、HttpOnly、host-only Cookie；授權碼單次使用、90 秒有效、PKCE S256、固定 callback、client 認證。Home 登出、停用帳號、重設密碼與撤銷系統授權會阻擋後续請求。
- 人員、角色及系統進入權限在 Home 管理；系統內的專案／對話／知識資料權限沿用原規則。
- API 分人員登入與有期限、可撤銷的 Bearer 服務憑證；Home 的憑證依系統、讀写範圍及登記路徑限制，不能取得管理員角色或控制服務。既有獨立 API 憑證仍依本系統規則驗證。
- 未設定 Home SSO 或 `enabled:false` 時沿用原獨立登入模式。公司可改接內部 Home（各服務使用不同 HTTPS 主機名、內部 CA、明確私有回傳通道），不依賴 Google 或網際網路；AI 接點維持獨立設定。
- 來源只綁 loopback，內網部署以公司 HTTPS reverse proxy 轉送；不要共用同主機名的不同 port，Cookie 不以 port 隔離。
- 外網切換前仍保留 Cloudflare Access。原 Google／Access 條文作為舊模式及回復方案；新原生登入模式以本條文為準。

回復：先確認 Cloudflare Access 原規則生效，再將 Home SSO 設為 `enabled:false` 並使用本專案正式啟停流程；來源原檔與私有設定備份在 Home 的 `.runtime/sso-backups/`。業務 DB 與 AI 設定不遷移。

一般使用者僅能讀取交易研究分析平台；寫入仍限 Home 管理員。內部 8000 API 的既有權杖保持原規則。此次只用 `scripts/deploy.ps1 -HomeAuthOnly` 更新已安裝的登入模組，不部署研究 WIP、不重算統計。
