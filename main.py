# -*- coding: utf-8 -*-
"""
Daily stock scan + email push (Taiwan-friendly)
----------------------------------------------
- 讀取 tickers.txt（每行一檔；台股請加 .TW 例如 2330.TW）
- 以 yfinance 抓 6 個月日資料，計算訊號：
  * 5/20MA 金叉
  * 20 日均量的量能異常（>1.5x）
  * 是否高於 MA60（百分比）
- 產出 CSV 與 HTML 到 ./output
- 以 SMTP（Gmail/其他）寄信，收件人由環境變數設定
  SMTP_USER / SMTP_PASS / SMTP_TO / (可選) SMTP_HOST / SMTP_PORT
"""
import os, io, ssl, smtplib, sys, traceback
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
from datetime import datetime, timezone, timedelta

import pandas as pd
import numpy as np
import yfinance as yf

OUTPUT_DIR = "output"
TICKERS_FILE = "tickers.txt"

def log(msg: str):
    print(f"[{datetime.now().astimezone().isoformat()}] {msg}", flush=True)

def load_tickers(path=TICKERS_FILE):
    if not os.path.exists(path):
        raise FileNotFoundError(f"找不到 {path}，請建立該檔並填入股號（台股加 .TW）。")
    with open(path, "r", encoding="utf-8") as f:
        tickers = [x.strip() for x in f if x.strip() and not x.strip().startswith("#")]
    if not tickers:
        raise ValueError("tickers.txt 為空，請至少放一個股號（台股加 .TW）。")
    return tickers

def fetch_prices(tickers, period="6mo"):
    log(f"抓取 {len(tickers)} 檔標的（期間 {period}）…")
    data = yf.download(tickers, period=period, group_by='ticker', auto_adjust=False, progress=False)
    if data is None or len(data) == 0:
        raise RuntimeError("yfinance 未取得任何資料，請檢查代碼格式（台股需加 .TW）或稍後再試。")
    return data

def compute_signals(data: pd.DataFrame) -> pd.DataFrame:
    rows = []
    is_multi = isinstance(data.columns, pd.MultiIndex)
    universe = [c[0] for c in data.columns if c[1] == 'Close'] if is_multi else ['SINGLE']

    for t in sorted(set(universe)):
        try:
            if is_multi:
                close = data[(t, 'Close')].dropna()
                vol   = data[(t, 'Volume')].dropna()
            else:
                close = data['Close'].dropna()
                vol   = data['Volume'].dropna()

            if len(close) < 60:
                log(f"- {t}: 資料不足（<60 根K），跳過")
                continue

            ma5  = close.rolling(5).mean()
            ma20 = close.rolling(20).mean()
            ma60 = close.rolling(60).mean()
            vol20 = vol.rolling(20).mean()

            last_date = close.index[-1]
            price = float(close.iloc[-1])
            dchg  = float((close.iloc[-1] / close.iloc[-2] - 1) * 100) if len(close) > 1 else np.nan

            golden_cross = int(ma5.iloc[-2] < ma20.iloc[-2] and ma5.iloc[-1] > ma20.iloc[-1])
            vol_spike    = int(vol.iloc[-1] > 1.5 * vol20.iloc[-1]) if not np.isnan(vol20.iloc[-1]) else 0
            above_ma60   = float((price / ma60.iloc[-1] - 1) * 100) if not np.isnan(ma60.iloc[-1]) else np.nan

            rows.append({
                "ticker": t,
                "date": last_date.date().isoformat(),
                "price": round(price, 4),
                "d_change_%": round(dchg, 2) if not np.isnan(dchg) else None,
                "golden_cross_5_20": bool(golden_cross),
                "vol_spike_vs_20d": bool(vol_spike),
                "above_ma60_%": round(above_ma60, 2) if not np.isnan(above_ma60) else None,
            })
        except Exception as e:
            log(f"- {t}: 計算失敗：{e}")

    df = pd.DataFrame(rows)
    if df.empty:
        raise RuntimeError("沒有可用的輸出（可能所有標的資料不足）；請更換/增加股號或加長 period。")
    df = df.sort_values(
        ["golden_cross_5_20", "vol_spike_vs_20d", "above_ma60_%", "d_change_%"],
        ascending=[False, False, False, False]
    )
    return df

def export_reports(df: pd.DataFrame):
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    ts = datetime.now(timezone.utc).astimezone().strftime("%Y%m%d_%H%M")
    csv_path = os.path.join(OUTPUT_DIR, f"scan_{ts}.csv")
    html_path = os.path.join(OUTPUT_DIR, f"scan_{ts}.html")

    df.to_csv(csv_path, index=False, encoding="utf-8-sig")

    html = io.StringIO()
    html.write("<html><head><meta charset='utf-8'><title>Daily Scan</title>")
    html.write("<style>table{border-collapse:collapse}td,th{border:1px solid #ddd;padding:6px}</style>")
    html.write("</head><body>")
    html.write("<h2>Daily Stock Scan</h2>")
    html.write(df.to_html(index=False, justify='center'))
    html.write(f"<p style='color:#666'>Generated at: {datetime.now().astimezone().isoformat()}</p>")
    html.write("</body></html>")
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html.getvalue())

    log(f"輸出完成：{csv_path} / {html_path}")
    return csv_path, html_path

def send_email(subject, body_html, attachments=None):
    user = os.environ.get("SMTP_USER")
    pwd  = os.environ.get("SMTP_PASS")
    to   = os.environ.get("SMTP_TO", "")
    host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    port = int(os.environ.get("SMTP_PORT", "587"))

    if not (user and pwd and to):
        log("[WARN] SMTP 環境變數未設定齊全：跳過寄信（需 SMTP_USER/SMTP_PASS/SMTP_TO）。")
        return False

    recipients = [x.strip() for x in to.split(",") if x.strip()]
    if not recipients:
        log("[WARN] SMTP_TO 沒有有效收件者，跳過寄信。")
        return False

    msg = MIMEMultipart()
    msg["From"] = user
    msg["To"] = ", ".join(recipients)
    msg["Subject"] = subject
    msg.attach(MIMEText(body_html, "html", "utf-8"))

    for path in (attachments or []):
        if not os.path.exists(path):
            log(f"[WARN] 附件不存在：{path}，略過。")
            continue
        part = MIMEBase("application", "octet-stream")
        with open(path, "rb") as f:
            part.set_payload(f.read())
        encoders.encode_base64(part)
        part.add_header("Content-Disposition", f'attachment; filename="{os.path.basename(path)}"')
        msg.attach(part)

    context = ssl.create_default_context()
    with smtplib.SMTP(host, port) as server:
        server.starttls(context=context)
        server.login(user, pwd)
        server.sendmail(user, recipients, msg.as_string())

    log(f"[OK] Email 已送出：{recipients}")
    return True

def main():
    try:
        tickers = load_tickers()
        log(f"載入 {len(tickers)} 檔：{', '.join(tickers[:10])}{' …' if len(tickers)>10 else ''}")
        data = fetch_prices(tickers, period='6mo')
        df = compute_signals(data)
        csv_path, html_path = export_reports(df)

        tz = timezone(timedelta(hours=8))
        subject = f"📈 每日選股掃描（台北時間 {datetime.now(tz).strftime('%Y-%m-%d %H:%M')}）"
        body = f"""
        <p>您好，這是自動化每日選股掃描。</p>
        <p><b>Top 10</b>：</p>
        {df.head(10).to_html(index=False)}
        <p>完整結果請見附件（CSV/HTML）。</p>
        """
        send_email(subject, body, [csv_path, html_path])
    except Exception as e:
        log(f"[ERROR] 任務失敗：{e}")
        traceback.print_exc()
        # 任務失敗仍輸出一個錯誤報告，方便排查
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        err_path = os.path.join(OUTPUT_DIR, "error.txt")
        with open(err_path, "w", encoding="utf-8") as f:
            f.write(f"Time: {datetime.now().astimezone().isoformat()}\n")
            f.write(f"Error: {e}\n")
        # 也嘗試寄一封錯誤通知（如果環境變數可用）
        send_email("❌ 每日選股掃描失敗通知", f"<pre>{traceback.format_exc()}</pre>", [err_path])

if __name__ == "__main__":
    main()
