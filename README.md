# ⚡ PolySports Quant 70%+ — Polymarket 運動自動交易系統

專為 **Polymarket 運動項目 (Sports Markets)** 打造的 24/7 全自動勝率監測與即時投注系統（含 Web 即時監控儀表板、模擬交易 Paper Trading 與 Polymarket CLOB 真實下單雙模式）。

---

## ✨ 核心功能

1. **運動賽事全自動掃描 (`Gamma API` + `CLOB API`)**
   - 支援 **NBA 籃球、足球 (英超/歐冠/西甲/MLS)、NFL 美足、MLB 棒球、NHL 冰球、網球 (ATP/WTA)、UFC 格鬥、電競 (Esports)** 等運動項目分類。
   - 每 `N` 秒（預設 5 秒）自動輪詢盤口各選項即時隱含勝率 (`outcomePrices` / `bestAsk`)。
2. **70%+ 勝率立即投注與完整風控引擎**
   - **勝率觸發區間**：預設 `70% ~ 95%`（一達到 `>= 70%` 立即買入，同時過濾 `> 95%` 無利潤空間的垃圾尾盤）。
   - **盤口深度與點差過濾**：可設定最低流動性（預設 `$1,500 USDC`）、最低成交量（預設 `$5,000 USDC`）與最大買賣點差（預設 `5.0%`）。
   - **防重複下注與預算保護**：同一場比賽/Condition ID 自動鎖定不重複下單，支援單日總預算上限與最大同時持倉數。
   - **自動停利 / 停損出場**：預設勝率升至 `96%` 自動停利平倉、跌破 `52%` 自動停損出場。
3. **雙模式執行（Paper 模擬交易 + Live CLOB 真實自動下單）**
   - **Paper 模擬交易（預設）**：內建 `$1,000 USDC` 虛擬資金，即時計算持倉市值、未實現/已實現損益 (PnL)、ROI 與勝率，並支援一鍵「📈 測試 70%+ 突破觸發」。
   - **Live CLOB 真實下單**：整合官方 `py-clob-client` (Polygon Chain ID `137` EIP-712 簽名)，填入私鑰與 Proxy 地址後可一鍵切換為真實自動買入。

---

## 🚀 本機快速啟動 (Mac / Linux)

```bash
# 1. 建立虛擬環境並安裝依賴
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# 2. 啟動伺服器 (預設 http://localhost:8080)
.venv/bin/python server.py
```

啟動後打開瀏覽器前往 **`http://localhost:8080`** 即可進入交易監控終端。

---

## ☁️ 部署至 Railway + GitHub (24/7 全天候運行推薦架構)

> **為什麼推薦 Railway + GitHub 而非 Vercel？**
> Vercel 屬於 Serverless 架構，API 請求會在數秒後休眠中斷，無法維持 24/7 每 5 秒不間斷監測盤口與即時下單。**Railway** 支援常駐容器 (Persistent Container)、WebSocket 即時推播與 SQLite 資料持久化。

### 步驟 1：上傳程式碼至 GitHub
```bash
git init
git add .
git commit -m "Initial PolySports 70%+ Auto-Trading System"
git branch -M main
git remote add origin https://github.com/<您的GitHub帳號>/polysports-autobot.git
git push -u origin main
```

### 步驟 2：在 Railway 建立專案
1. 前往 [Railway.app](https://railway.app) 點擊 **New Project → Deploy from GitHub repo**。
2. 選擇 `polysports-autobot`，Railway 會自動讀取專案內的 [`railway.json`](railway.json) 與 [`Dockerfile`](Dockerfile) 完成建置並啟動健康檢查 (`/api/health`)。
3. 在 Railway 服務的 **Settings → Networking** 點擊 **Generate Domain** 取得公開網址。
4. （選用）在 Railway 新增一個 **Volume** 掛載於 `/app/data`，即可永久保存 SQLite 交易紀錄。
