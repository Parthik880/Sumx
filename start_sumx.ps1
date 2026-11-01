$ErrorActionPreference='Stop'
Set-Location -LiteralPath $PSScriptRoot
$sumxPattern=[regex]::Escape($PSScriptRoot)
$sumxApi=Get-CimInstance Win32_Process | Where-Object { $_.Name -match '^python(w)?\.exe$' -and $_.CommandLine -match $sumxPattern -and $_.CommandLine -match 'uvicorn\s+backend\.main:app' }
if (-not $sumxApi) {
    New-Item -ItemType Directory -Force -Path (Join-Path $PSScriptRoot 'data') | Out-Null
    Start-Process -FilePath 'powershell.exe' -ArgumentList @('-NoProfile','-ExecutionPolicy','Bypass','-File',('"'+(Join-Path $PSScriptRoot 'run.ps1')+'"'),'-Service','api') -WorkingDirectory $PSScriptRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $PSScriptRoot 'data\launcher.stdout.log') -RedirectStandardError (Join-Path $PSScriptRoot 'data\launcher.stderr.log')
}
if (Get-NetTCPConnection -LocalPort 5173 -State Listen -ErrorAction SilentlyContinue) {
    Write-Output 'SUMX frontend is already running at http://localhost:5173.'
} else {
    & ./run.ps1 -Service web
}
