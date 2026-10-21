# 在這台電腦建立每日排程（工作日 08:30 盤前簡報、14:30 收盤後檢查、16:30 前瞻測試、16:45 族群雷達、16:50 模擬帳戶）。
# 以 S4U 方式執行：不論是否登入 Windows 都會跑，且不需要儲存密碼（需以系統管理員身分執行本腳本）。
# 用法（PowerShell，在專案根目錄）：  powershell -ExecutionPolicy Bypass -File scripts\register_tasks.ps1
# 停用：  powershell -ExecutionPolicy Bypass -File scripts\register_tasks.ps1 -Disable
param([switch]$Disable)

$root = Split-Path -Parent $PSScriptRoot
$wrapper = Join-Path $root 'scripts\run_scheduled.cmd'
$tasks = @(@{Name = 'morning_brief'; At = '08:30'}, @{Name = 'check_alerts'; At = '14:30'}, @{Name = 'forward_test'; At = '16:30'}, @{Name = 'sector_radar'; At = '16:45'}, @{Name = 'paper_bot'; At = '16:50'})

if ($Disable) {
    foreach ($t in $tasks) { Disable-ScheduledTask -TaskPath '\stock_agent\' -TaskName $t.Name -ErrorAction SilentlyContinue | Out-Null }
    Get-ScheduledTask -TaskPath '\stock_agent\' -ErrorAction SilentlyContinue | ForEach-Object { '{0}  {1}' -f $_.TaskName, $_.State }
    return
}

if (-not (Test-Path (Join-Path $root '.venv\Scripts\python.exe'))) { throw '找不到 .venv，請先建立虛擬環境並安裝 requirements.txt' }
if (-not (Test-Path (Join-Path $root '.env'))) { Write-Warning '找不到 .env：沒有 DISCORD_WEBHOOK_URL 時不會推送' }
if (-not (Test-Path (Join-Path $root 'portfolio.json'))) { Write-Warning '找不到 portfolio.json：收盤後檢查沒有持股可判斷' }

$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType S4U -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 30)
$days = 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday'
foreach ($t in $tasks) {
    $action = New-ScheduledTaskAction -Execute 'cmd.exe' -Argument ('/c "' + $wrapper + '" ' + $t.Name) -WorkingDirectory $root
    $trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek $days -At $t.At
    Register-ScheduledTask -TaskPath '\stock_agent\' -TaskName $t.Name -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Description 'stock_agent' -Force | Out-Null
}
Get-ScheduledTask -TaskPath '\stock_agent\' | ForEach-Object {
    $i = $_ | Get-ScheduledTaskInfo
    '{0}  state={1}  next={2}' -f $_.TaskName, $_.State, $i.NextRunTime
}
