# 台股持股提醒與研究系統（地端）

在自己電腦上執行：每日 08:30 盤前簡報、14:30 收盤後持股提醒（Discord 推送）、個股研究卡、交易計畫與部位控管。
摘要使用本機 Ollama，數字全部由 Python 計算。原課程的 Azure AI Foundry 多代理版本保留在 `main` 分支。

### 換到另一台電腦

GitHub 上只有程式碼；下面這些刻意不進 git，需要自己搬（請用隨身碟或私人管道，**不要上傳到 GitHub**）：

| 檔案 | 說明 |
|---|---|
| `.env` | Discord webhook 網址（等同密碼） |
| `portfolio.json` | 你的持股與提醒規則 |
| `state/`（選用） | 提醒的連續天數、最後檢查日；不搬就從頭記錄 |
| `data/raw/`（選用） | 歷史資料快取；不搬的話第一次執行研究卡與回測會重新下載（數分鐘到數小時） |

新電腦上的步驟（PowerShell，在專案根目錄）：
```bash
git clone https://github.com/ShanHugo/stock_agent.git
cd stock_agent
git switch feat/agent-pipeline
py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt pytest httpx
.venv\Scripts\python.exe -m pytest -q
```
1. 把 `.env`、`portfolio.json`（以及選用的 `state/`、`data/raw/`）複製到專案根目錄
2. 選用：安裝 Ollama 並 `ollama pull qwen3.5:9b`
3. 試跑：`.venv\Scripts\python.exe scripts/morning_brief.py --dry-run`
4. 建立排程：`powershell -ExecutionPolicy Bypass -File scripts\register_tasks.ps1`
5. **在舊電腦停用排程**，避免兩台都推送：`powershell -ExecutionPolicy Bypass -File scripts\register_tasks.ps1 -Disable`

### 1. 安裝
```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

### 2. 設定
- 複製 `.env.example` 為 `.env`，填入 `DISCORD_WEBHOOK_URL`（Discord 頻道 → 整合 → Webhook）
- 複製 `portfolio.example.json` 為 `portfolio.json`，填入持股與提醒規則
- 選用：安裝 [Ollama](https://ollama.com) 並下載 `OLLAMA_MODEL`（預設 `qwen3.5:9b`）以產生一句摘要

### 排程失敗通知
每個排程執行完都會記錄結果（`state/scheduler/<名稱>.json`）；失敗時立刻推送「⚠️ 排程失敗」與最後幾行紀錄到 `DISCORD_WEBHOOK_URL`。
電腦沒開等原因導致排程完全沒執行時，隔天 08:30 盤前簡報的「抓取失敗」區會列出。測試時設環境變數 `TASK_STATUS_NO_SEND=1` 可只印出不推送。

### 本機操作台（網頁）
雙擊 `scripts\start_console.cmd`（或執行 `.venv\Scripts\python.exe -m src.web.console --open`），瀏覽器會開啟 http://127.0.0.1:8765 。
關掉那個黑色視窗就停止。只聽本機 127.0.0.1，同網路的其他裝置連不進來。

- **持股總覽**：最新收盤、未實現損益、台幣市值、停損與停利價、狀態（只看狀態，不推送、不影響每日提醒紀錄）
- **簡報與提醒**：盤前簡報、持股提醒的預覽或推送到 Discord
- **交易與計畫**：記錄買賣、建立個股交易計畫並算可買股數、確認持股理由、交易檢討
- **研究卡**：產生個股研究卡、管理觀察清單
- **選股研究**：下載進度條、閱讀報告、重新執行研究
- **設定與排程**：排程狀態與最近輸出、直接編輯 `portfolio.json`（儲存前檢查格式，舊版自動備份到 `state\portfolio_backups\`）

每個按鈕都是呼叫下面同一支命令列程式，結果完全相同；推送、記錄交易等會改變資料的動作會先跳出確認。

### 3. 每日排程一覽（平日）
| 時間 | 排程 | 內容 |
|---|---|---|
| 08:30 | morning_brief | 盤前簡報（含族群雷達、族群新聞、今天的模擬單） |
| 09:30 | paper_exec | 模擬帳戶以盤中零股五檔成交 |
| 14:30 | check_alerts | 持股提醒 |
| 16:30 | forward_test | 前瞻測試紀錄 |
| 16:45 | sector_radar | 族群雷達 |
| 16:50 | paper_bot | 模擬帳戶收盤記帳、排定下一個交易日的單 |

以系統管理員身分執行 `scripts\register_tasks.ps1` 建立（不需登入即可執行）；加 `-Disable` 全部停用。

### 4. 持股提醒（Discord）
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

### 5. 個股交易計畫與部位控管
操作個股（非 ETF）時，系統要求先寫計畫，並依你在 `portfolio.json` 的 `"risk"` 設定算出最多能買幾股：
```json
"risk": {"capital": 500000, "max_risk_per_trade": 0.01, "max_position_pct": 0.2, "max_total_risk": 0.05}
```
（數字只是格式範例，請依你自己的資金與承受度填寫；欄位說明見 `src/app/alerts/plans.py`）

```bash
# 1. 買前：寫理由、停損與失效條件，算出可買股數
python scripts/plan_trade.py new 2317 --stop 190 --thesis "AI 伺服器出貨成長" --invalid below_ma:60
# 2. 買進後記錄（自動套用計畫；超出股數或風險預算會警告）
python scripts/record_trade.py buy 2317 90 240
# 3. 每日檢查會加上：理由失效條件、總曝險、單一個股占比、定期確認理由
python scripts/plan_trade.py review 2317      # 理由仍成立時確認
# 4. 賣出記錄會寫入交易日誌；每月檢討
python scripts/record_trade.py sell 2317 90 260
python scripts/review_trades.py
```
- 失效條件：`close_below:價格`、`below_ma:天數`、`time_stop:天數:最低報酬`、
  `revenue_yoy_below:年增率門檻%:連續月數`（例 `revenue_yoy_below:0:2` = 連續 2 個月營收年減）。
- ETF 不需計畫、不受單一持股上限限制；台股 00 開頭自動判定，美股 ETF 請在持股標 `"etf": true`（或買進時加 `--etf`）。
- 交易日誌記錄 R 倍數（損益 ÷ 初始每股風險）與出場方式，檢討時會標出停損執行偏慢、虧損超過 1R 的交易。

### 6. 個股研究卡（上市公司）
把月營收、季度獲利、估值位置、法人與融資、價格位置整理成一張卡，只陳述數據、不給買賣結論：
```bash
python scripts/research_card.py 2330 2317
python scripts/research_card.py --watchlist     # portfolio.json 的 "watchlist" + 持有的個股
python scripts/research_card.py 2330 --send     # 推送到 Discord
```
- 資料來源：公開資訊觀測站（月營收、季度損益）、證交所（本益比/淨值比/殖利率、三大法人、融資融券）、Yahoo Finance（價格）。
- 每次請求間隔約 3 秒避免被封鎖；第一次查詢一檔約需 2 分鐘回補歷史，之後讀 `data/raw/research/` 快取。
- 季度數字由累計財報相減得出單季；金融、保險業格式不同，第一版不支援。上櫃公司尚未支援。
- 每日檢查會在觀察清單與持有個股公布新月營收時通知（第一次執行只記錄，不通知）。

### 7. 選股類型研究（歷史驗證）
用歷史資料檢驗「哪一類股票之後比較會漲」，以及幾種選股規則扣除成本後是否勝過直接持有：
```bash
python scripts/shortterm_lab.py --universe electronics   # 短期預測 + 單一特徵分組
python scripts/strategy_lab.py  --universe electronics   # 趨勢、營收動能、價值成長三類規則
```
- 選股範圍：`electronics` = 上市＋上櫃電子 8 類（半導體、電腦及週邊、光電、通信網路、電子零組件、電子通路、資訊服務、其他電子），
  每月依證交所／櫃買中心**當時**的產業分類重建名單，**含之後下市的公司**；`electronics-twse`、`electronics-tpex` 只看上市或上櫃；
  `tw50-pit`（預設）= 臺灣50當時的成分股。
- 電子股範圍的價格不用 Yahoo（沒有下市股票），改由證交所／櫃買每日收盤價自行還原除權息與減資；報告附上與 Yahoo 還原價的比對。
- 已下市公司在公開資訊觀測站查不到月營收與財報，這兩項特徵對它們是缺值（報告會列出檔數）。
- 單一特徵分組：每 20 個交易日把範圍內股票依營收年增、法人買超、近期漲幅、本益比位置等分 5 組，看各組之後 20 日相對表現。
- 報告輸出到 `reports/`。第一次執行需回補 2015 年起的逐日資料（上市、上櫃各一條連線同時抓，約 7～8 小時），
  之後讀 `data/raw/` 快取。
- 只陳述歷史統計；過去有效不代表未來有效，非投資建議。

**族群雷達**（記憶體、面板、IC 設計…哪些族群目前營收、動能、量能比較強）：
```bash
python scripts/sector_radar.py              # 最新一個交易日的族群排名（排程：平日 16:45）
python scripts/sector_radar.py --backtest   # 歷史驗證：每 20 / 60 天持有排名前 3 族群 vs 0050
```
- 族群分類來自櫃買中心產業價值鏈平台（每家公司快取 90 天），定義在 `src/app/lab/themes.py`。
- 排序：營收年增、近 60 日漲跌、量能比、融資 20 日變化，四項族群中位數的跨族群排名平均（這四項在電子股單一特徵分組中有穩定規律）。
- 盤前簡報會列出前 5 名與最後 1 名，並附歷史驗證結果；操作台「選股研究」分頁有完整表格。
- 個股參考標記（`src/app/lab/flags.py`，模擬帳戶每日訊息也會列出持股與明天要買的股票）：▲ 一年新高（近 20 日創 250 日新高）、
  ▼ 外資轉賣（近 5 日外資連買 5 天以上後轉賣）。都經過歷史驗證，但加進機器人規則的回測較差，所以只顯示、不交易。

**族群新聞**：盤前簡報會抓鉅亨網過去 18 小時的台股新聞與頭條，依族群歸類，並由本機模型為新聞最多的 6 個族群各寫 1–2 句整理。
整理中出現新聞沒有的數字、或出現「建議、買進、布局」等字眼時整段捨棄，只列標題與連結。

**模擬帳戶**（依營收動能規則、你的風險設定與券商手續費，用模擬資金逐日操作；不會真的下單）：
```bash
python scripts/paper_bot.py                  # 收盤後：記帳、檢查出場、排定下一個交易日的單（排程：平日 16:50）
python scripts/paper_bot.py --execute-now    # 盤中：用證交所即時報價執行待執行的單（排程：平日 09:30）
python scripts/paper_bot.py --backtest       # 歷史模擬 2018 年至今
python scripts/paper_bot.py --dry-run        # 只顯示，不寫入帳戶、不推送
```
- 進場：最近 2 個已公布月份營收都年增、站上 120 日線，依營收年增率由高到低；部位依 `portfolio.json` 的 `risk` 計算。
- 出場：跌破停損（買價下方 10%）、跌破 120 日線、營收連 2 月年減；收盤漲到成本 +20% 後停損上移到成本價。
- 成交：09:30 依證交所盤中零股五檔模擬市價單（買往上吃賣單、賣往下吃買單）；鎖漲停沒有賣單買不到、鎖跌停沒有買單賣不掉。
  09:30 沒執行到則收盤後以開盤價補成交（開盤較前收 ≥ +9.5% 視為漲停買不到、≤ -9.5% 視為跌停賣不掉；歷史模擬同此規則）。
- 成績單：每月第一個交易日收盤後自動產生上個月的成績單並推送（`reports/paper_scorecard_YYYYMM.md`）；隨時查看：`python scripts/paper_bot.py --scorecard`。
  照抄標準在 2026-10-22 看到任何結果前訂定（`src/app/lab/scorecard.py`）：滿 120 個交易日、完成交易 ≥ 20 筆、扣成本後贏同期 0050、
  最大回撤不比歷史同長度期間最差的 10% 還差，四項都要達成；並與歷史模擬中所有同長度期間的報酬、超額、回撤分布比較。規則修改後評估期重新起算。
- 推送：`.env` 設 `PAPER_BOT_WEBHOOK_URL`（專用頻道）與 `PAPER_BOT_AUTO_SEND=1`；暫停：操作台「模擬帳戶」分頁，或建立 `state/paper_bot/paused` 檔案。
- 前瞻觀察「大盤過濾」：影子帳戶與模擬帳戶同資金、同規則，唯一差別是 0050 收盤低於 200 日線時不買新股（`state/paper_bot/shadow_market_filter.json`），
  每日推送附上兩邊淨值差。歷史回測 8 個起始日年化都較高，但 2018～2021 全輸，未達事先訂的標準，所以只觀察、不影響模擬帳戶。

**技術面訊號研究**（均線、RSI、MACD、布林通道、突破、量能、K 線型態，共 28 個訊號）：
```bash
python scripts/prefetch_ohlc.py     # 先下載 2015 年起的每日開高低收（約 2.5 小時；中斷後重跑會接著抓）
python scripts/technical_lab.py     # 事件研究 → reports/technical_lab_YYYYMMDD.md
```
- 隔天開盤進、持有 5／20 個交易日，與同期全體電子股比較；|t| ≥ 3、前後半期方向一致才算有效（同時檢驗很多訊號，門檻比 2 嚴）。
- 2026-10-23 結果：只有「創 250 日／60 日新高」偏多有效；長紅、爆量上漲、黃金交叉等追漲訊號 5 日內反而落後。
  加進模擬機器人（新高優先、不追高、60 日新低出場、持股 5～8 檔）回測都比現行差，規則不變。

**前瞻測試**（用真實的未來資料驗證回測結果）：
```bash
python scripts/forward_test.py            # 記錄今天的名單並評估之前的紀錄（排程：平日 16:30）
python scripts/forward_test.py --dry-run  # 只顯示
```
- 規則固定為「營收動能、營收年增最高 20 檔」（上市＋上櫃電子股、最近 2 個已公布月份營收年增都 > 0、站上 120 日線），規則代號 `rev_momentum_top20_v1`。
- 紀錄存在 `state/forward/records.jsonl`，只新增、不修改；每筆在 5 天、20 天後自動以真實股價扣成本、與同期 0050 比較。
- 操作台「選股研究」分頁可看最新名單與累積結果。這是驗證規則用的紀錄，不是買賣建議。

### 8. 測試
```bash
.venv\Scripts\python.exe -m pytest -q
```
網路請求一律以假資料替代，不會連線或推送。

---

本專案僅供研究學習用途，非投資建議。
