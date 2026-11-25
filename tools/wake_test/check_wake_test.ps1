# Bước 2: chạy sau khi máy đã thức. Đọc nhật ký sự kiện, marker, lastwake; ghi kết quả; dọn tác vụ.
# Dùng: powershell -ExecutionPolicy Bypass -File tools\wake_test\check_wake_test.ps1
$ErrorActionPreference = "Continue"
$proj   = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$dir    = Join-Path $proj "tools\wake_test"
$stateFile = Join-Path $dir "state.json"
if (-not (Test-Path $stateFile)) { "Không có state.json - chạy start_wake_test.ps1 trước."; exit 2 }

$st = Get-Content $stateFile -Raw -Encoding utf8 | ConvertFrom-Json
$startedAt = [DateTime]::Parse($st.started_at)
$wakeAt    = [DateTime]::Parse($st.wake_at)
$now = Get-Date

# Chỉ kiểm tra sau giờ đánh thức + 1 phút; chưa đến giờ thì KHÔNG kết luận và KHÔNG xóa tác vụ.
if (-not $st.no_sleep -and $now -lt $wakeAt.AddMinutes(1)) {
    "Chưa đến giờ kiểm tra: đánh thức lúc " + $wakeAt.ToString("HH:mm:ss") + ", bây giờ " + $now.ToString("HH:mm:ss") + ". Chạy lại sau."
    exit 3
}

# Sự kiện ngủ/thức trong khoảng từ lúc bắt đầu đến bây giờ
$events = Get-WinEvent -FilterHashtable @{LogName='System'; StartTime=$startedAt.AddSeconds(-5)} -ErrorAction SilentlyContinue |
    Where-Object { $_.Id -in 42,107,506,507,1 } | Sort-Object TimeCreated
# Chọn cặp ngủ/thức BAO TRÙM giờ đánh thức: lần vào ngủ cuối cùng trước giờ đánh thức + 1 phút,
# rồi lần thức dậy đầu tiên sau đó.
$enters = @($events | Where-Object { $_.Id -in 42,506 -and $_.TimeCreated -le $wakeAt.AddMinutes(1) })
$enter = $enters | Select-Object -Last 1
$resume = $null
if ($enter) {
    $resume = $events | Where-Object { $_.Id -in 107,507 -and $_.TimeCreated -gt $enter.TimeCreated } | Select-Object -First 1
}
# Độ trễ thức dậy so với giờ đặt đánh thức (giây). Âm = thức sớm, dương = thức trễ.
$lateSec = if ($resume) { [int]($resume.TimeCreated - $wakeAt).TotalSeconds } else { $null }

$markerOk = (Test-Path $st.marker_file) -and (Select-String -Path $st.marker_file -Pattern "WAKE_OK" -Quiet)
$lastwake = (powercfg /lastwake | Out-String).Trim()
$taskInfo = Get-ScheduledTaskInfo -TaskName $st.task_name -ErrorAction SilentlyContinue

$slept = [bool]$enter
$gapSec = if ($enter -and $resume) { [int]($resume.TimeCreated - $enter.TimeCreated).TotalSeconds } else { 0 }
$onTime = ($lateSec -ne $null) -and ([math]::Abs($lateSec) -le 60)
$wokeByTimer = $markerOk -and $slept -and ($gapSec -ge 60) -and $onTime

$verdict = if ($st.no_sleep) { "KHÔNG NGỦ (no_sleep)" }
           elseif ($wokeByTimer) { "PASS - máy ngủ và được đánh thức bằng lịch" }
           elseif (-not $slept) { "FAIL - máy không ngủ (không có sự kiện vào ngủ)" }
           elseif (-not $markerOk) { "FAIL - máy ngủ nhưng tác vụ không ghi marker" }
           else { "KHÔNG RÕ - máy ngủ thời gian ngắn ($gapSec giây), cần kiểm tra lastwake" }

$lines = @(
    "# Kiểm tra đánh thức - $($now.ToString('yyyy-MM-dd HH:mm:ss'))",
    "",
    "**Kết quả: $verdict**",
    "",
    "- Bắt đầu test: $($startedAt.ToString('HH:mm:ss')), đặt đánh thức: $($wakeAt.ToString('HH:mm:ss'))",
    "- Sự kiện vào ngủ: " + $(if ($enter) { "$($enter.TimeCreated.ToString('HH:mm:ss')) (ID $($enter.Id))" } else { "không có" }),
    "- Sự kiện thức dậy: " + $(if ($resume) { "$($resume.TimeCreated.ToString('HH:mm:ss')) (ID $($resume.Id))" } else { "không có" }),
    "- Thời gian ngủ (giây): $gapSec",
    "- Thức dậy so với giờ đặt đánh thức (giây, + là trễ): " + $(if ($lateSec -ne $null) { $lateSec } else { 'n/a' }),
    "- Marker WAKE_OK: $(if ($markerOk) { 'có' } else { 'không' })",
    "- Tác vụ: " + $(if ($taskInfo) { "LastRun $($taskInfo.LastRunTime), Result $($taskInfo.LastTaskResult)" } else { "không còn" }),
    "",
    "## powercfg /lastwake",
    '```',
    $lastwake,
    '```',
    "",
    "Ghi chú: chỉ tính PASS khi có sự kiện vào ngủ, thức dậy cách nhau ít nhất 60 giây, và marker được ghi."
)
$report = Join-Path $dir ("result_" + $now.ToString("yyyyMMdd_HHmm") + ".md")
$lines | Set-Content -Path $report -Encoding utf8
$verdict
"Báo cáo: $report"

# Dọn tác vụ test (không đụng lịch PaperDailyCheck / PaperWakeUp0855)
Unregister-ScheduledTask -TaskName $st.task_name -Confirm:$false -ErrorAction SilentlyContinue

