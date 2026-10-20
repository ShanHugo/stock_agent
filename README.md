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

### 5. 測試
- 以 `sample_data.csv` 驗證端到端流程

---

本專案僅供研究學習用途，非投資建議。
