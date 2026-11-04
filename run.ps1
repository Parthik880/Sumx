param([ValidateSet('api','worker','tts','web')][string]$Service = 'web')
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
# Existing terminals may predate user-scoped FFmpeg/Python installations.
$env:Path = $env:Path + ';' + [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' + [Environment]::GetEnvironmentVariable('Path', 'User')
switch ($Service) {
    'api' {
        $projectPattern=[regex]::Escape($PSScriptRoot)
        $ttsWorker=Get-CimInstance Win32_Process | Where-Object { $_.Name -match '^python(w)?\.exe$' -and $_.CommandLine -match $projectPattern -and $_.CommandLine -match '\-m\s+backend\.tts_worker(\s|$)' }
        if (-not $ttsWorker) {
            New-Item -ItemType Directory -Force -Path (Join-Path $PSScriptRoot 'data') | Out-Null
            Start-Process -FilePath (Join-Path $PSScriptRoot '.venv\Scripts\python.exe') -ArgumentList @('-u','-m','backend.tts_worker') -WorkingDirectory $PSScriptRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $PSScriptRoot 'data\tts.stdout.log') -RedirectStandardError (Join-Path $PSScriptRoot 'data\tts.stderr.log')
            Write-Output 'Started the speech queue worker. All inference runs on Modal.'
        }
        $worker=Get-CimInstance Win32_Process | Where-Object { $_.Name -match '^python(w)?\.exe$' -and $_.CommandLine -match $projectPattern -and $_.CommandLine -match '\-m\s+backend\.jobs(\s|$)' }
        if (-not $worker) {
            New-Item -ItemType Directory -Force -Path (Join-Path $PSScriptRoot 'data') | Out-Null
            Start-Process -FilePath (Join-Path $PSScriptRoot '.venv\Scripts\python.exe') -ArgumentList @('-u','-m','backend.jobs') -WorkingDirectory $PSScriptRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $PSScriptRoot 'data\worker.stdout.log') -RedirectStandardError (Join-Path $PSScriptRoot 'data\worker.stderr.log')
            Write-Output 'Started the local processing worker. Logs are in data/worker.*.log.'
        }
        & ./.venv/Scripts/python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 8000 --no-access-log
    }
    'worker' { & ./.venv/Scripts/python.exe -m backend.jobs }
    'tts' { & ./.venv/Scripts/python.exe -m backend.tts_worker }
    'web' { Set-Location -LiteralPath (Join-Path $PSScriptRoot 'frontend'); & npm.cmd run dev -- --port 5173 --strictPort }
}
