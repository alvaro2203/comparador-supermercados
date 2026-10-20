#Requires -Version 5.1
<#
.SYNOPSIS
    Daily catalog download, meant to be launched by Windows Task Scheduler.
.DESCRIPTION
    Runs every registered supermarket scraper and appends output to logs\scraper-YYYY-MM-DD.log.
    A named mutex prevents overlapping runs. Logs older than 30 days are pruned.
    Exit code: 0 = ok, 1 = partial, 2 = failed (shown as "Last Run Result" in Task Scheduler).
#>
$ErrorActionPreference = "Stop"
$ProjectDir = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectDir
$env:PYTHONUTF8 = "1"

$LogDir = Join-Path $ProjectDir "logs"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$LogFile = Join-Path $LogDir ("scraper-{0:yyyy-MM-dd}.log" -f (Get-Date))

$Mutex = New-Object System.Threading.Mutex($false, "Global\SupermarketScraperDaily")
if (-not $Mutex.WaitOne(0)) {
    Add-Content -Path $LogFile -Value "$(Get-Date -Format o) another run is still in progress, skipping"
    exit 0
}

try {
    & uv run --frozen supermarket-scraper run *>> $LogFile
    $Code = $LASTEXITCODE
}
finally {
    $Mutex.ReleaseMutex()
    $Mutex.Dispose()
}

Get-ChildItem $LogDir -Filter "scraper-*.log" |
    Where-Object { $_.LastWriteTime -lt (Get-Date).AddDays(-30) } |
    Remove-Item -Force

exit $Code
