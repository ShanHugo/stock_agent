# README.md

## 台股多代理（Multi-Agent）研究與交易系統

### 1. 安裝
```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

### 2. 設定
- 複製 `.env.example` 為 `.env` 並填入 Azure/Foundry 相關參數

### 3. 建立代理
```bash
python scripts/bootstrap_foundry.py
```

### 4. 執行每日例行流程
```bash
python src/app/ops/run_daily.py                       # 有 sample_data.csv 時讀本地檔
python src/app/ops/run_daily.py --ignore-local --start 2025-01-01
python src/app/ops/run_daily.py --ignore-local --narrate   # 請 Foundry 代理講評
```

流程：**Data QA → PM/Risk → Execution（模擬）→ 稽核紀錄**（`src/app/pipeline/`）
- 所有判斷與數字都由 Python 規則算出；代理（`--narrate`）只負責講評，講評失敗不影響結果。
- Data QA 有 FAIL 時停止；Kill-Switch 觸發時 Execution 停止新單。
- 預設只做多（台股放空需融券），`--allow-short` 允許空方；`--strategy mean_reversion` 切換策略。
- 每次執行寫一份稽核 JSON：`reports/<日期>/pipeline_<代號>_<時間>.json`（參數、資料指紋、各階段結果、講評）。
- 網頁介面（`python -m src.web.app`）也有「每日流程」區塊，對應 `POST /api/pipeline`。

### 5. 持股提醒（Discord）
支援台股（上市/上櫃，含零股）與美股（含碎股，例如複委託 VOO）。三種提醒，門檻都由你在 `portfolio.json` 設定：
- **停損 / 停利 / 移動停損**：觸發時附賣出股數與預估損益（台股 ETF 證交稅 0.1%、股票 0.3%，美股無證交稅）。
- **從高點回落**：從近一年高點回落超過你設的幅度（例如 10%、20%），每跨過一個更深的門檻通知一次。
- **配置偏離**：依目標比例（例如台股:美股 = 50:50）偏離超過門檻時通知一次，並算出回到目標需要調整的台幣金額。

設定步驟：
1. 複製 `portfolio.example.json` 為 `portfolio.json`（欄位說明見 `src/app/alerts/portfolio.py`）。`portfolio.json` 不會被提交。
2. Discord 頻道 → 編輯頻道 → 整合 → Webhook → 新增並複製網址，填入 `.env` 的 `DISCORD_WEBHOOK_URL`。
3. 定期定額或買賣後記一筆，股數與成交均價會自動更新：
```bash
python scripts/record_trade.py buy 0050 12 108.5
python scripts/record_trade.py buy VOO 0.0712 701.3
```
4. 每天台股收盤後（14:00 以後）執行：
```bash
python scripts/check_alerts.py --dry-run   # 先看訊息
python scripts/check_alerts.py             # 推送
```
- 只在有提醒或資料抓取失敗時推送；沒有新收盤資料（休市、重複執行）時自動略過。
- 台股 14:00 前、美股紐約 16:30 前，當天的 K 棒只是盤中價，不會拿來判斷。
- 選用本機 Ollama（`OLLAMA_MODEL`，預設 `qwen3.5:9b`）寫一句摘要；摘要出現提醒中沒有的數字，
  或把「接近」寫成「觸發」時會整段捨棄。加 `--no-narrate` 可關閉。
- 提醒依你自己設定的規則產生，非投資建議；是否下單請自行決定。

### 6. 測試
- 以 `sample_data.csv` 驗證端到端流程

---

本專案僅供研究學習用途，非投資建議。
