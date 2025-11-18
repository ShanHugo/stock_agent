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
python src/app/ops/run_daily.py
```

### 5. 測試
- 以 `sample_data.csv` 驗證端到端流程

---

本專案僅供研究學習用途，非投資建議。
