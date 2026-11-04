$ErrorActionPreference = 'Stop'
$projectPattern = [regex]::Escape($PSScriptRoot)
$servicePattern = '(\-m\s+(uvicorn\s+backend\.main:app|backend\.jobs|backend\.tts_worker)(\s|$)|vite[\\/]bin[\\/]vite\.js.*--port\s+5173(\s|$))'
$processes = @(Get-CimInstance Win32_Process | Where-Object {
    $_.CommandLine -and $_.CommandLine -match $projectPattern -and $_.CommandLine -match $servicePattern
})
foreach ($process in $processes) {
    Stop-Process -Id $process.ProcessId -ErrorAction SilentlyContinue
    Write-Host "Stopped SUMX process $($process.ProcessId)."
}
Write-Host 'SUMX stopped. Avoid stopping during active processing; completed data stays in data\.'
