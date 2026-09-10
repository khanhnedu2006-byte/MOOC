<#
    Cài MOOC Console chạy tự động mỗi khi đăng nhập Windows.

    Cách chạy (KHÔNG cần quyền admin):
        powershell -ExecutionPolicy Bypass -File tools\autostart.ps1 install
        powershell -ExecutionPolicy Bypass -File tools\autostart.ps1 status
        powershell -ExecutionPolicy Bypass -File tools\autostart.ps1 uninstall

    VÌ SAO DÙNG TASK SCHEDULER CHỨ KHÔNG PHẢI KHÓA REGISTRY "Run":
    khóa Run chỉ chạy chương trình đúng một lần lúc đăng nhập. App chết giữa
    chừng là thôi, đến sáng hôm sau mới biết, mà hàng đợi thì đã dồn cả đêm.
    Task Scheduler chạy lại được, và nói cho biết lần chạy cuối kết thúc ra sao.
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
    # pythonw.exe chứ không phải python.exe: pythonw không mở cửa sổ console
    # đen. Dùng python.exe thì mỗi lần đăng nhập lại có một cửa sổ đen nằm
    # trên màn hình, và người dùng sẽ đóng nó — đóng là job chết.
    $found = Get-Command pythonw.exe -ErrorAction SilentlyContinue
    if ($found) { return $found.Source }

    # Trường hợp hay gặp nhất khi không thấy: dùng conda mà chưa activate env.
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

    # Hoãn 1 phút sau khi đăng nhập. eLIS chặn theo IP nên phải đợi mạng
    # công ty / VPN lên hẳn; chạy ngay lập tức thì mấy vòng đầu chỉ toàn 403
    # rồi bắn cảnh báo giả cho người vận hành.
    $trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
    $trigger.Delay = "PT1M"

    $settings = New-ScheduledTaskSettingsSet `
        -MultipleInstances IgnoreNew `
        -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) `
        -ExecutionTimeLimit ([TimeSpan]::Zero) `
        -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
        -StartWhenAvailable

    # HAI tham số có mặc định SAI với app này, phải đặt lại:
    #   ExecutionTimeLimit   mặc định 3 ngày -> Windows tự giết app vào ngày
    #                        thứ tư. Đặt Zero là chạy không giới hạn.
    #   *OnBatteries         mặc định: rút sạc là dừng tác vụ. Máy này là
    #                        laptop, nên mặc định đó nghĩa là job chết im lặng.
    #
    # MultipleInstances IgnoreNew TRÙNG với mặc định của Windows — viết ra chỉ
    # để nói rõ ý, và làm lớp thứ hai bên cạnh khóa socket trong main_app.py.

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

    # 0 = xong bình thường. 267009 = đang chạy. Còn lại là đáng xem.
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
