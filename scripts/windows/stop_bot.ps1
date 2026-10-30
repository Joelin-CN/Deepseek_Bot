# Stop the bot process recorded in runtime\bot.pid.
# Verifies process identity (python from the Deepseek_bot env running companion)
# before stopping; never touches QQ, SnowLuma or browsers.
# Note: this is a hard kill. For graceful shutdown (cleanup logs) run the bot
# in foreground mode and press Ctrl+C.

$repo = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$pidFile = Join-Path $repo "runtime\bot.pid"
$lockFile = Join-Path $repo "data\companion.lock"

if (-not (Test-Path $pidFile)) { Write-Host "No PID file; nothing to stop."; exit 0 }

$botPid = Get-Content $pidFile
$proc = Get-CimInstance Win32_Process -Filter "ProcessId=$botPid" -ErrorAction SilentlyContinue
if ($null -eq $proc) {
    Write-Host "PID $botPid is not running."
    Remove-Item $pidFile -ErrorAction SilentlyContinue
    exit 0
}

# Identity check: must be python.exe from the project env running companion
$isOk = $proc.ExecutablePath -like "*Deepseek_bot*" -and $proc.CommandLine -like "*companion*"
if (-not $isOk) {
    Write-Error "PID $botPid is '$($proc.Name)' with unexpected command line; refusing to stop it."
    exit 1
}

Stop-Process -Id $botPid -Force
Remove-Item $pidFile -ErrorAction SilentlyContinue
Write-Host "Bot stopped (PID $botPid)."

# Clean up the single-instance lock if it belongs to the stopped PID
if (Test-Path $lockFile) {
    try {
        $lock = Get-Content $lockFile | ConvertFrom-Json
        if ($lock.pid -eq [int]$botPid) { Remove-Item $lockFile -ErrorAction SilentlyContinue }
    } catch { }
}
