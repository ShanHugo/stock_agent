# 專案規格書：
# 以 Azure AI Foundry 建置台股多代理（Multi-Agent）研究與交易系統

> 版本：v1.0（MVP）
> 文件性質：正式規格書（Software Requirements & Architecture Spec）
> 適用範圍：研究與**紙上交易 / 模擬交易**。任何實盤均需另行審核與驗證。本文件不構成投資建議。

---

## 1. 目的與範圍

### 1.1 目的

建立一套以 **Azure AI Foundry Agent Service** 為核心的  \***多代理（multi-agent)**\* 系統，用於台股資料蒐集、研究、回測、投組風控與交易執行（模擬），支援日常運行與可稽核的決策留痕。

### 1.2 範圍

* 研究資料處理、訊號產生、回測引擎（MVP 簡化）、投組風控、執行策略模擬、監控/告警、合規稽核。
* **不包含**：任何直接對接實盤下單的最終責任流程與合規審批；若需上實盤，需另行完成法遵、安全、壓測與風控驗證。

### 1.3 非目標（Out of Scope）

* 保證報酬（例如年化 30%+）
* 未驗證的即時高頻策略
* 衍生性商品（期權）全套微結構撮合（可擴充）

---

## 2. 成功指標（KPI）

* 研究層級：

  * 回測報表可重現（同一資料、同一參數→同一結果）。
  * 風險調整報酬指標可計算（Sharpe、Calmar、Max Drawdown 等）。
* 工程層級：

  * 代理協作與工具呼叫（FunctionTool / OpenAPI / MCP）可用且可監控。
  * 重大錯誤可觸發 **Kill-Switch**：停新單、撤現有單（模擬層級）。
* 營運層級：

  * 每日例行流程（資料→訊號→建議持倉→報表）在 30 分鐘內完成（MVP 目標）。

> 實盤 KPI 需另立，並經法遵與風控核准。

---

## 3. 名詞與縮寫

* **Agent Service**：Azure AI Foundry 的多代理服務。
* **FunctionTool**：以程式函式對外提供工具能力。
* **OpenAPI Tool**：以 OpenAPI/Swagger 描述的外部 API 工具。
* **MCP**：Model Context Protocol，將自建服務（行情/下單/風控）包成工具供代理呼叫。
* **RAG**：Retrieval-Augmented Generation，檢索增強產生。
* **OHLCV**：Open/High/Low/Close/Volume。

---

## 4. 系統總覽

### 4.1 邏輯架構（MVP）

```
+---------------------------+      +-----------------------+
|   Azure AI Foundry        |      |  外部/自建服務        |
|  (Agent Service)          |      |-----------------------|
|                           |      |  行情API / 券商API   |
|  [Data QA Agent]          |<---->|  (OpenAPI/MCP)        |
|  [PM/Risk Agent]          |      |  MCP: market, orders  |
|  [Execution Agent]        |      +-----------------------+
|     ^      ^      ^       |
|     |      |      | Tools  |  (Bing Grounding / Azure AI Search / Logic Apps…)
+-----|------|------|--------+
      |      |      |
      v      v      v
+---------------------------+     +------------------------+
|  計算與資料層              |     |  觀測/告警/稽核       |
|  (Container/AKS/Local)    |     |  (AppInsights/Logs)    |
|  - Backtest Engine        |     |  - Metrics/Logs        |
|  - Feature/Signals        |     |  - Audit Trails        |
+---------------------------+     +------------------------+
```

### 4.2 代理職責（MVP 三代理）

| 代理        | 職責             | 輸入     | 輸出        | 主要工具                     |
| --------- | -------------- | ------ | --------- | ------------------------ |
| Data QA   | 資料匯入/品質檢查/特徵驗證 | 行情、基本面 | 乾淨時間列、特徵表 | FunctionTool、OpenAPI/MCP |
| PM/Risk   | 因子/策略整合、投組與風控  | 訊號、限制  | 目標倉位、風控約束 | FunctionTool、RAG         |
| Execution | 拆單/執行模擬、回報彙整   | 目標倉位   | 委託模擬紀錄    | OpenAPI/MCP（模擬）          |

> 後續可增：Ops/SRE、Compliance 代理。

---

## 5. 專案結構與產出物

### 5.1 目錄結構（核心）

```
foundry-twstock-agents/
├─ README.md
├─ requirements.txt
├─ .env.example
├─ foundry/
│  ├─ project.json        # 代理與工具清單（說明性）
│  └─ tools.md            # Foundry 端工具註冊指南
├─ src/app/
│  ├─ config/settings.py  # 環境變數、成本參數
│  ├─ agents/
│  │  ├─ registry.py      # 建立代理、掛工具
│  │  └─ tools.py         # FunctionTool 範例
│  ├─ strategies/         # base / momentum / mean_reversion
│  ├─ backtest/           # engine.py / data.py
│  └─ ops/                # run_daily.py / kill_switch.py
└─ scripts/bootstrap_foundry.py
```

### 5.2 程式語言與相依

* Python 3.11+
* 主要套件：`azure-ai-agents`、`azure-identity`、`pandas`、`numpy`、`python-dotenv`

---

## 6. 功能性需求（FRD）

### FR-1 多代理建立與管理

* 系統應可透過 `AgentsClient` 建立至少三個代理（Data QA / PM\&R / Execution）。
* 代理需可掛載工具（FunctionTool；後續支援 OpenAPI Tool、MCP）。

### FR-2 工具整合

* **FunctionTool**：提供本地計算（示例 `ping()`、`calc_sharpe()`）。
* **OpenAPI Tool（可選）**：可上傳券商/行情 Swagger，設定認證（Header/Query），代理可呼叫。
* **MCP（可選）**：可連線至 MCP Server（如 `market.get_quotes`、`orders.place`、`orders.cancel`、`risk.kill_switch`）。

### FR-3 回測引擎（MVP）

* 提供 bar-based 報酬計算（以收盤價變化 × 部位），並在**換手**時計入成本（手續費/交易稅/滑價）。
* 輸出包含：`ret`、`equity`、`turnover`、`cost` 時間序列。

### FR-4 策略框架

* 提供 `Strategy` 介面與兩種示例策略（動能、均值回歸）。
* 策略輸出部位序列（−1/0/1 或權重）。

### FR-5 日常流程（批次）

* `run_daily.py`：讀取 OHLCV（CSV）、產生部位、回測、輸出末端指標列印。
* 可由 Data QA 代理進行基本檢查（之後擴充為代理間協作）。

### FR-6 稽核與留痕（MVP）

* 保留：資料讀取時間、參數、版本、回測結果（檔案/Log）；代理建立/工具註冊活動紀錄。

---

## 7. 非功能性需求（NFR）

| 類別   | 指標           | 說明                                             |
| ---- | ------------ | ---------------------------------------------- |
| 性能   | 研究批次 ≤ 30 分鐘 | 單檔/單策略/單日例行（可依資料量調整）                           |
| 可用性  | 工具呼叫失敗率 < 1% | 需有重試與降級策略                                      |
| 可維運性 | 可觀測性         | Application Insights / Log Analytics（日誌、延遲、錯誤） |
| 安全   | 機密管理         | 憑證與金鑰放於 Key Vault；最小權限                         |
| 隔離   | 網路安全         | 內網/VNet、Private Link（上雲時）                      |
| 可測試性 | 自動化測試        | 單元/整合/回歸測試；紙上交易驗證                              |

---

## 8. 介面規格

### 8.1 環境變數（`.env`）

| 變數                                        | 必填 | 說明                                     |
| ----------------------------------------- | -- | -------------------------------------- |
| `PROJECT_ENDPOINT`                        | 是  | Azure AI Foundry Project Endpoint      |
| `MODEL_DEPLOYMENT_NAME`                   | 是  | Foundry 綁定可用的模型部署名稱                    |
| `AZURE_TENANT_ID/CLIENT_ID/CLIENT_SECRET` | 否  | 使用 Client Credentials；或以 `az login` 取代 |
| `TX_FEE_BPS`                              | 否  | 手續費（基點）                                |
| `TX_TAX_BPS`                              | 否  | 證交稅（基點）                                |
| `SLIPPAGE_BPS`                            | 否  | 滑價（基點）                                 |

### 8.2 MCP 工具（建議介面）

* `market.get_quotes(symbols: string[]) -> Quote[]`
* `orders.place(order: {symbol, side, qty, type, limitPrice?, tif}) -> OrderId`
* `orders.cancel(orderId: string) -> bool`
* `risk.kill_switch(reason: string) -> bool`

### 8.3 OpenAPI（券商/行情）

* 需提供 OpenAPI 3（YAML/JSON）規格。
* 認證用法（Header/Query/Token）與頻率限制需於 Swagger 中明定。
* 最低路由：報價查詢、下單、撤單、查委託/成交。

---

## 9. 資料規格

### 9.1 輸入（OHLCV CSV）

* 欄位：`date,open,high,low,close,volume`
* `date` 可解析為日期型態；大小寫不拘（載入後轉小寫）。

### 9.2 策略輸出（Positions）

* `pandas.Series`，index 同資料日期；值為 −1/0/1 或\[0,1] 權重。

### 9.3 回測輸出（DataFrame）

* 欄位：

  * `ret`：單期損益（含成本）
  * `equity`：累積權益曲線
  * `turnover`：換手率
  * `cost`：期內成本

---

## 10. 流程與時序

### 10.1 每日批次（MVP）

```
[T-1/T日 08:00] Data QA → 讀取OHLCV→品質檢查
     ↓
PM/Risk → 產生部位（以策略/訊號）→ 合併限制 → 目標倉位
     ↓
Execution → 擬合拆單/執行模擬 → 產出委託模擬紀錄
     ↓
報表/稽核輸出（回測指標、留痕、告警）
```

### 10.2 代理互動（示意）

```
User/Job
  └─> Data QA Agent (工具：QualityCheck)
        └─> PM/Risk Agent (工具：SharpeCalc / RAG)
              └─> Execution Agent (工具：orders.place/cancel 模擬)
```

---

## 11. 安全與合規

* 憑證管理：Azure Key Vault；程式取用 Managed Identity 或 Client Credentials。
* 存取控制：最小權限（Least Privilege）、角色分離（研究/執行/運維/稽核）。
* 網路隔離（上雲）：VNet、Private Link、NSG。
* 稽核：保留代理決策指示、工具呼叫參數（去識別化）、輸入輸出哈希值。
* 免責：本系統僅供研究測試，不構成投資建議。

---

## 12. 可觀測性與告警

* 指標：工具成功率、延遲分佈、錯誤率、回測耗時、每日批次完成時間。
* 告警：失敗重試超閾值、資料缺漏、成本/滑價超標、權益回撤超標→觸發 Kill-Switch。
* 日誌：代理互動紀錄、工具呼叫、版本與參數、回測輸出摘要。

---

## 13. 測試計畫

### 13.1 單元測試

* `tools.py`：`ping()`、`calc_sharpe()` 邏輯。
* `engine.py`：成本計算、累積權益曲線正確性。

### 13.2 整合測試

* 以樣本 OHLCV 跑 `run_daily.py`，驗證端到端輸出。
* 代理建立/工具掛載是否成功（mock Foundry 端點或使用沙箱）。

### 13.3 回歸測試

* 版本升級後，指定資料與參數所得結果需一致。

### 13.4 模擬交易測試（Paper）

* 使用固定規則驗證委託模擬路徑；測試 Kill-Switch 觸發。

---

## 14. 部署與環境

### 14.1 本機開發（MVP）

* Python venv
* `.env` 以 `PROJECT_ENDPOINT` 連 Foundry 專案
* `scripts/bootstrap_foundry.py` 建立代理

### 14.2 雲端（建議）

* 計算：Azure Container Apps / AKS
* 儲存：Blob/ADLS Gen2；研究用 DB（Azure SQL / PostgreSQL）
* 監控：Application Insights / Log Analytics
* 安全：Key Vault + Managed Identity；私網連線

---

## 15. 風險與緩解

| 風險      | 影響     | 緩解                     |
| ------- | ------ | ---------------------- |
| 資料品質問題  | 回測失真   | Data QA 代理、完整留痕、來源多樣化  |
| 過度擬合    | 實盤績效不穩 | Walk-forward、交叉驗證、容量分析 |
| API 穩定性 | 執行中斷   | 重試/補償、降級策略、備援供應商       |
| 合規限制    | 法遵風險   | 不實盤；若要實盤需法遵審查與審批       |
| 成本/滑價低估 | 淨報酬驟降  | 更精細的微結構與成交模型、保守參數      |

---

## 16. 擴充路線

* **工具層**：OpenAPI（券商/行情）與 MCP server 上線；Bing Grounding / Azure AI Search 做 RAG。
* **撮合微結構**：加入連續競價、零股 5 秒集合競價、漲跌幅限制、委託型別、撤改單節流。
* **代理擴編**：Ops/SRE（監控/降風險）、Compliance（稽核報表）。
* **投組進階**：風險平價、行業/風格暴露控管、容量分析。
* **部署**：雲端化與 CI/CD（GitHub Actions / Azure DevOps）。

---

## 16.1 新增：資料抓取與視覺化（本次更新）

### 資料抓取現況
* 已新增模組：`src/app/data/fetch.py`，提供 `fetch_ohlcv_yf()` 與 `fetch_multi()` 支援 yfinance。
* 後續擴充：
  * TWSE/OTC 官方 JSON API 封裝（節流 + 重試）
  * 快取層（本地 parquet `data/raw/<symbol>.parquet`）
  * 資料品質檢查：缺值、非交易日、價格跳點、成交量為 0

### 視覺化現況
* 已新增 `src/app/visual/report.py`：`plot_equity()` 產出權益與回撤圖 `equity_curve.png`。
* 後續擴充：
  * 互動報表：Plotly 產出 `report.html`
  * Rolling 指標：rolling Sharpe / Vol / Turnover heatmap
  * 交易事件標註：進出場點、最大回撤區

### 新增績效模組
* `src/app/performance/metrics.py`：`sharpe_ratio`、`max_drawdown`、`basic_report`。

### 工具函式
* `agents/tools.py` 已重構：導出 `ping`, `mean`, `calc_sharpe`, `fetch_prices`, `run_simple_backtest` 作為 FunctionTool 函式集合。

### 待辦（資料與視覺化）
| 優先 | 項目 | 說明 |
| ---- | ---- | ---- |
| 高 | TWSE API fetcher | requests+backoff；支援多日拼接與節流 |
| 高 | 資料快取 | parquet 儲存，避免重複抓取 |
| 高 | Data QA 規則 | 缺值、價格跳空>±15%、零量過多 |
| 中 | 特徵計算模組 | MA / RSI / 波動度 / Z-Score 集中在 features/ |
| 中 | 互動圖表 | Plotly 指標副圖與 drawdown overlay |
| 低 | 報表匯總 | Markdown / JSON 匯出整合績效與圖表路徑 |

### 進度標記（本輪完成）
✅ yfinance 抓取 (單/多 symbol 基礎)
✅ 權益曲線 + 回撤靜態圖
✅ 基礎績效指標計算
✅ 工具函式與代理整合骨架
⏳ TWSE 官方 API 封裝（下一步）
⏳ 快取與 Data QA
⏳ 互動可視化與多資產聚合圖

---

## 17. 接受標準（Acceptance Criteria）

* ✅ 可於本機依 README 完成安裝、設定 `.env`、成功執行 `bootstrap_foundry.py` 建立 3 代理。
* ✅ `run_daily.py` 可在提供的 OHLCV 樣本上輸出回測結果（含 `equity`、`ret`）。
* ✅ 工具（FunctionTool）可被代理呼叫並回傳結果。
* ✅ 產出最小報表與日誌；關鍵步驟具備留痕。
* ✅ 風控樣板（Kill-Switch）具備可被呼叫與觸發的接口。

---

## 18. 附錄：檔案一覽與關鍵片段

* `src/app/agents/registry.py`：建立代理與掛載工具
* `src/app/agents/tools.py`：`ping()`、`calc_sharpe()`
* `src/app/strategies/*`：策略介面與兩個範例
* `src/app/backtest/engine.py`：回測與成本計算
* `src/app/ops/run_daily.py`：每日例行流程
* `foundry/project.json`、`foundry/tools.md`：說明性清單與工具註冊指引

---

### 法律與合規聲明

本文件及樣板僅供工程與研究學習用途，**不構成投資建議**；任何投資或交易行為請自行承擔風險。若涉及實盤或對外提供服務，請先完成內部控管、法遵審核與必要之資安檢測。

