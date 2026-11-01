param([ValidateSet('api','worker','web','qwen')][string]$Service = 'web')
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
switch ($Service) {
    'api' {
        $projectPattern=[regex]::Escape($PSScriptRoot)
        $qwenPython=Join-Path $PSScriptRoot '.venv-faster-qwen\Scripts\python.exe'
        $qwenWorker=Get-CimInstance Win32_Process | Where-Object { $_.Name -match '^python(w)?\.exe$' -and $_.CommandLine -match $projectPattern -and $_.CommandLine -match '\-m\s+backend\.qwen_worker(\s|$)' }
        if ((Test-Path -LiteralPath $qwenPython) -and -not $qwenWorker) {
            New-Item -ItemType Directory -Force -Path (Join-Path $PSScriptRoot 'data') | Out-Null
            Start-Process -FilePath $qwenPython -ArgumentList @('-u','-m','backend.qwen_worker') -WorkingDirectory $PSScriptRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $PSScriptRoot 'data\qwen.stdout.log') -RedirectStandardError (Join-Path $PSScriptRoot 'data\qwen.stderr.log')
            Write-Output 'Started local Qwen GPU worker. Startup does not reinstall dependencies.'
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
    'qwen' { & ./run_qwen_tts.ps1 }
    'web' { Set-Location -LiteralPath (Join-Path $PSScriptRoot 'frontend'); & npm.cmd run dev -- --port 5173 --strictPort }
}
