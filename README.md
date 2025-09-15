# Stock Pipeline Starter (Taiwan-friendly)

## 快速使用
1. 編輯 `tickers.txt`（台股請加 `.TW`）。
2. 設定 Repo → Settings → Secrets and variables → Actions：
   - `SMTP_USER`、`SMTP_PASS`（Gmail 用應用程式密碼 16 碼）、`SMTP_TO`
3. 到 **Actions** 啟用 workflow，或 **Run workflow** 測試。
4. 每個工作日 **08:30 台北時間** 自動跑，輸出 CSV/HTML 並寄信。

## 變更排程
- 修改 `.github/workflows/daily.yml` 的 `cron`（UTC 時區）。
