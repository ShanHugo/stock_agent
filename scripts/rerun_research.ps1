# 重新產生所有電子股研究（資料更新或修正後使用）：月營收回補 → 規則驗證 → 短期預測與特徵分組 → 族群雷達驗證 → 今日族群雷達
# 用法：powershell -ExecutionPolicy Bypass -File scripts\rerun_research.ps1    進度：logs\rerun_research.log
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$env:PYTHONIOENCODING = 'utf-8'
$py = Join-Path $root '.venv\Scripts\python.exe'
$log = Join-Path $root 'logs\rerun_research.log'
New-Item -ItemType Directory -Force (Join-Path $root 'logs') | Out-Null
"===== $(Get-Date -Format 'yyyy-MM-dd HH:mm') 開始" | Out-File $log -Encoding utf8
$steps = @(
    @('月營收回補（含 KY 外國企業）', @('-c', "import pandas as pd; from datetime import date; from src.app.research import sources as rs`nfor p in pd.period_range('2015-01', pd.Period(date.today(), 'M') - 1, freq='M'):`n    for mk in ('sii', 'otc'):`n        try: rs.monthly_revenue(p.year, p.month, market=mk)`n        except rs.NoData: pass`n    print('[revenue]', p, flush=True)")),
    @('選股規則驗證', @('scripts/strategy_lab.py', '--universe', 'electronics')),
    @('短期預測與特徵分組', @('scripts/shortterm_lab.py', '--universe', 'electronics')),
    @('族群雷達歷史驗證', @('scripts/sector_radar.py', '--backtest')),
    @('今日族群雷達', @('scripts/sector_radar.py'))
)
foreach ($s in $steps) {
    "===== $(Get-Date -Format 'HH:mm') $($s[0])" | Out-File $log -Append -Encoding utf8
    & $py @($s[1]) 2>&1 | ForEach-Object { "$_" } | Out-File $log -Append -Encoding utf8
    "----- $($s[0]) 結束（代碼 $LASTEXITCODE）" | Out-File $log -Append -Encoding utf8
}
"===== $(Get-Date -Format 'yyyy-MM-dd HH:mm') 全部完成" | Out-File $log -Append -Encoding utf8
