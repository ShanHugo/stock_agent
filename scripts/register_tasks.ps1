# 在這台電腦建立每日排程（工作日 08:30 盤前簡報、14:30 收盤後檢查）。
# 用法（PowerShell，在專案根目錄）：  powershell -ExecutionPolicy Bypass -File scripts\register_tasks.ps1
# 停用：  powershell -ExecutionPolicy Bypass -File scripts\register_tasks.ps1 -Disable
param([switch]$Disable)

$root = Split-Path -Parent $PSScriptRoot
$wrapper = Join-Path $root 'scripts\run_scheduled.cmd'
$tasks = @(@{Name = 'morning_brief'; At = '08:30'}, @{Name = 'check_alerts'; At = '14:30'})

if ($Disable) {
    foreach ($t in $tasks) { Disable-ScheduledTask -TaskPath '\stock_agent\' -TaskName $t.Name -ErrorAction SilentlyContinue | Out-Null }
    Get-ScheduledTask -TaskPath '\stock_agent\' -ErrorAction SilentlyContinue | ForEach-Object { '{0}  {1}' -f $_.TaskName, $_.State }
    return
}

if (-not (Test-Path (Join-Path $root '.venv\Scripts\python.exe'))) { throw '找不到 .venv，請先建立虛擬環境並安裝 requirements.txt' }
if (-not (Test-Path (Join-Path $root '.env'))) { Write-Warning '找不到 .env：沒有 DISCORD_WEBHOOK_URL 時不會推送' }
if (-not (Test-Path (Join-Path $root 'portfolio.json'))) { Write-Warning '找不到 portfolio.json：收盤後檢查沒有持股可判斷' }

$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 30)
$days = 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday'
foreach ($t in $tasks) {
    $action = New-ScheduledTaskAction -Execute 'cmd.exe' -Argument ('/c "' + $wrapper + '" ' + $t.Name) -WorkingDirectory $root
    $trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek $days -At $t.At
    Register-ScheduledTask -TaskPath '\stock_agent\' -TaskName $t.Name -Action $action -Trigger $trigger -Settings $settings -Description 'stock_agent' -Force | Out-Null
}
Get-ScheduledTask -TaskPath '\stock_agent\' | ForEach-Object {
    $i = $_ | Get-ScheduledTaskInfo
    '{0}  state={1}  next={2}' -f $_.TaskName, $_.State, $i.NextRunTime
}
