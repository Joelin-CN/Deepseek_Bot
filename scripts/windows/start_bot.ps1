# Start the companion bot with the Deepseek_bot conda environment.
# Only manages the process it starts (PID recorded in runtime\bot.pid).
# Prerequisites: SnowLuma running and the bot QQ instance logged in.

param([switch]$Foreground)

$repo = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$python = "E:\Softwares\Anaconda\envs\Deepseek_bot\python.exe"
$runtimeDir = Join-Path $repo "runtime"
$pidFile = Join-Path $runtimeDir "bot.pid"

if (-not (Test-Path $python)) { Write-Error "Python not found: $python"; exit 1 }

New-Item -ItemType Directory -Force -Path $runtimeDir | Out-Null
if (Test-Path $pidFile) {
    $oldPid = Get-Content $pidFile
    if (Get-Process -Id $oldPid -ErrorAction SilentlyContinue) {
        Write-Error "Bot already running (PID $oldPid). Run stop_bot.ps1 first."
        exit 1
    }
    Remove-Item $pidFile | Out-Null
}

if ($Foreground) {
    Set-Location $repo
    & $python -m companion
    exit $LASTEXITCODE
}

$proc = Start-Process -FilePath $python -ArgumentList "-m", "companion" -WorkingDirectory $repo -PassThru -WindowStyle Minimized
$proc.Id | Set-Content $pidFile
Write-Host "Bot started in background (PID $($proc.Id)). Logs: $repo\logs\bot.log"
