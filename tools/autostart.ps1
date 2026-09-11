<#
    Cài MOOC Console chạy tự động mỗi khi đăng nhập Windows.

    Cách chạy (KHÔNG cần quyền admin):
        powershell -ExecutionPolicy Bypass -File tools\autostart.ps1 install
        powershell -ExecutionPolicy Bypass -File tools\autostart.ps1 status
        powershell -ExecutionPolicy Bypass -File tools\autostart.ps1 uninstall

    Dùng Task Scheduler chứ không dùng khóa Registry "Run": khóa Run chỉ
    chạy chương trình một lần lúc đăng nhập, app chết giữa chừng là thôi.
    Task Scheduler chạy lại được và cho biết lần chạy cuối kết thúc ra sao.
#>

[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet("install", "uninstall", "status")]
    [string]$Action = "install",

    # Đường dẫn pythonw.exe. Bỏ trống thì tự tìm trong PATH.
    [string]$Python = ""
)

$ErrorActionPreference = "Stop"

$TaskName = "MOOC Console"
$Root = Split-Path -Parent $PSScriptRoot
$AppPath = Join-Path $Root "main_app.py"

function Find-Pythonw {
    if ($Python) {
        if (-not (Test-Path $Python)) { throw "Không thấy $Python" }
        return (Resolve-Path $Python).Path
    }
    # pythonw.exe không mở cửa sổ console đen. python.exe thì mỗi lần đăng
    # nhập lại có một cửa sổ đen, người dùng đóng nó là job chết.
    $found = Get-Command pythonw.exe -ErrorAction SilentlyContinue
    if ($found) { return $found.Source }

    # Hay gặp: dùng conda mà chưa activate env.
    throw @"
Không tìm thấy pythonw.exe trong PATH.

Đang dùng conda thì chỉ đường tay:
    powershell -ExecutionPolicy Bypass -File tools\autostart.ps1 install ``
        -Python "$env:USERPROFILE\miniconda3\pythonw.exe"
"@
}

function Install-Autostart {
    if (-not (Test-Path $AppPath)) { throw "Không thấy $AppPath" }
    $exe = Find-Pythonw

    $taskAction = New-ScheduledTaskAction -Execute $exe `
        -Argument "`"$AppPath`"" -WorkingDirectory $Root

    # Hoãn 1 phút sau khi đăng nhập, đợi mạng công ty / VPN lên hẳn. eLIS
    # chặn theo IP nên chạy ngay thì mấy vòng đầu chỉ toàn 403.
    $trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
    $trigger.Delay = "PT1M"

    $settings = New-ScheduledTaskSettingsSet `
        -MultipleInstances IgnoreNew `
        -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) `
        -ExecutionTimeLimit ([TimeSpan]::Zero) `
        -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
        -StartWhenAvailable

    # Hai tham số có mặc định sai với app này:
    #   ExecutionTimeLimit  mặc định 3 ngày, Windows tự giết app vào ngày thứ
    #                       tư. Zero là chạy không giới hạn.
    #   *OnBatteries        mặc định rút sạc là dừng tác vụ. Máy vận hành là
    #                       laptop.
    #
    # MultipleInstances IgnoreNew trùng với mặc định của Windows, viết ra để
    # nói rõ ý và làm lớp thứ hai bên cạnh khóa socket trong main_app.py.

    Register-ScheduledTask -TaskName $TaskName -Action $taskAction `
        -Trigger $trigger -Settings $settings -Force `
        -Description "Chạy MOOC Console lúc đăng nhập. Job xử lý chứng chỉ eLIS." | Out-Null

    Write-Host "Đã cài tác vụ '$TaskName'." -ForegroundColor Green
    Write-Host "  python : $exe"
    Write-Host "  app    : $AppPath"
    Write-Host "  chạy   : 1 phút sau khi đăng nhập, tự chạy lại tối đa 3 lần nếu chết"
    Write-Host ""
    Write-Host "Chạy thử ngay không cần đăng xuất:"
    Write-Host "  Start-ScheduledTask -TaskName '$TaskName'"
}

function Uninstall-Autostart {
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if (-not $task) {
        Write-Host "Không có tác vụ '$TaskName' để gỡ."
        return
    }
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "Đã gỡ tác vụ '$TaskName'." -ForegroundColor Green
    Write-Host "App đang chạy thì vẫn chạy tiếp; lần đăng nhập sau mới không tự mở."
}

function Show-Status {
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if (-not $task) {
        Write-Host "Chưa cài. Cài bằng: tools\autostart.ps1 install"
        return
    }
    $info = Get-ScheduledTaskInfo -TaskName $TaskName
    Write-Host "Tác vụ    : $TaskName"
    Write-Host "Trạng thái: $($task.State)"
    Write-Host "Chạy lần cuối : $($info.LastRunTime)"
    Write-Host "Lần chạy sau  : $($info.NextRunTime)"

    # 0 = xong bình thường. 267009 = đang chạy.
    $code = $info.LastTaskResult
    $note = switch ($code) {
        0      { "kết thúc bình thường" }
        267009 { "đang chạy" }
        267011 { "chưa chạy lần nào" }
        default { "MÃ LỖI — xem Event Viewer > Task Scheduler" }
    }
    Write-Host "Kết quả lần cuối: $code ($note)"
}

switch ($Action) {
    "install"   { Install-Autostart }
    "uninstall" { Uninstall-Autostart }
    "status"    { Show-Status }
}
