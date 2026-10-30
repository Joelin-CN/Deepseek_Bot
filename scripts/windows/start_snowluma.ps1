# Start SnowLuma (OneBot gateway) with the WebUI bootstrap password from .env.
# Only manages the process it starts (PID recorded in runtime\snowluma.pid).
#
# SNOWLUMA_WEBUI_BOOTSTRAP_PASSWORD seeds the WebUI login password ONLY when
# E:\Softwares\SnowLuma\config\webui.json does not exist (fresh start or the
# old file was moved away); an existing webui.json always wins. After this
# script seeds the password once, the WebUI keeps working across restarts.
# QQ injection (WebUI "Load") is still required after a QQ restart.

param([switch]$Foreground)

$repo = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$snowluma = "E:\Softwares\SnowLuma"
$node = Join-Path $snowluma "node.exe"
$runtimeDir = Join-Path $repo "runtime"
$pidFile = Join-Path $runtimeDir "snowluma.pid"
$envFile = Join-Path $repo ".env"

if (-not (Test-Path $node)) { Write-Error "SnowLuma node.exe not found: $node"; exit 1 }

# 读取 .env 中的 WebUI 预设密码（本地文件，不入库）
$bootstrapPassword = $null
if (Test-Path $envFile) {
    foreach ($line in Get-Content $envFile -Encoding UTF8) {
        if ($line -match '^\s*SNOWLUMA_WEBUI_PASSWORD\s*=\s*(.+?)\s*$') {
            $bootstrapPassword = $Matches[1]
            break
        }
    }
}
if ($bootstrapPassword -and $bootstrapPassword.Length -ge 8) {
    $env:SNOWLUMA_WEBUI_BOOTSTRAP_PASSWORD = $bootstrapPassword
    Write-Host "WebUI bootstrap password loaded from .env"
} else {
    Write-Warning "SNOWLUMA_WEBUI_PASSWORD missing or shorter than 8 chars in .env; SnowLuma will use its existing/random WebUI credential"
}

New-Item -ItemType Directory -Force -Path $runtimeDir | Out-Null
if (Test-Path $pidFile) {
    $oldPid = Get-Content $pidFile
    if (Get-Process -Id $oldPid -ErrorAction SilentlyContinue) {
        Write-Error "SnowLuma already running (PID $oldPid). Stop it first."
        exit 1
    }
    Remove-Item $pidFile | Out-Null
}

if ($Foreground) {
    Set-Location $snowluma
    & $node index.mjs
    exit $LASTEXITCODE
}

$proc = Start-Process -FilePath $node -ArgumentList "index.mjs" -WorkingDirectory $snowluma -PassThru -WindowStyle Minimized
$proc.Id | Set-Content $pidFile
Write-Host "SnowLuma started in background (PID $($proc.Id)). WebUI: http://127.0.0.1:5099"
