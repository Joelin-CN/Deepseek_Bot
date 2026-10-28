# Stop the bot process recorded in runtime\bot.pid.
# Only terminates that single recorded PID; never touches QQ, SnowLuma or browsers.
# Note: this is a hard kill. For graceful shutdown (cleanup logs) run the bot
# in foreground mode and press Ctrl+C.

$repo = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$pidFile = Join-Path $repo "runtime\bot.pid"

if (-not (Test-Path $pidFile)) { Write-Host "No PID file; nothing to stop."; exit 0 }

$botPid = Get-Content $pidFile
$proc = Get-Process -Id $botPid -ErrorAction SilentlyContinue
if ($null -eq $proc) {
    Write-Host "PID $botPid is not running."
    Remove-Item $pidFile -ErrorAction SilentlyContinue
    exit 0
}
if ($proc.ProcessName -notlike "python*") {
    Write-Error "PID $botPid is '$($proc.ProcessName)', refusing to stop it."
    exit 1
}

Stop-Process -Id $botPid -Force
Remove-Item $pidFile -ErrorAction SilentlyContinue
Write-Host "Bot stopped (PID $botPid)."
