# Bước 1: tạo tác vụ đánh thức sau N phút, ghi trạng thái, rồi đưa máy vào chế độ ngủ.
# Dùng: powershell -ExecutionPolicy Bypass -File tools\wake_test\start_wake_test.ps1 -Minutes 3
param(
    [int]$Minutes = 3,
    [switch]$NoSleep   # chỉ tạo tác vụ, không ngủ (để kiểm tra cấu hình)
)

$ErrorActionPreference = "Stop"
$proj   = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$dir    = Join-Path $proj "tools\wake_test"
$marker = Join-Path $proj "paper\wake_test.txt"
$taskName = "PaperWakeTest"
$stateFile = Join-Path $dir "state.json"

# Xóa tác vụ cũ nếu còn
Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue

$wakeAt = (Get-Date).AddMinutes($Minutes)
$action   = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c echo WAKE_OK %date% %time% >> `"$marker`""
$trigger  = New-ScheduledTaskTrigger -Once -At $wakeAt
$settings = New-ScheduledTaskSettingsSet -WakeToRun -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null

$startedAt = Get-Date
[ordered]@{
    task_name   = $taskName
    started_at  = $startedAt.ToString("o")
    wake_at     = $wakeAt.ToString("o")
    marker_file = $marker
    no_sleep    = [bool]$NoSleep
} | ConvertTo-Json | Set-Content -Path $stateFile -Encoding utf8

"Tác vụ $taskName sẽ đánh thức lúc " + $wakeAt.ToString("HH:mm:ss") + " (bây giờ " + $startedAt.ToString("HH:mm:ss") + ")"

if ($NoSleep) {
    "NoSleep: không đưa máy ngủ. Kiểm tra tác vụ bằng Get-ScheduledTaskInfo."
    exit 0
}

"Đưa máy vào chế độ ngủ..."
Start-Sleep -Seconds 2
rundll32.exe powrprof.dll,SetSuspendState 0,1,0

