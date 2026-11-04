[CmdletBinding()]
param([switch]$NoBrowser)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
Set-Location -LiteralPath $PSScriptRoot

# Pick up user-scoped installers even in a terminal opened before installation.
$env:Path = $env:Path + ';' + [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' + [Environment]::GetEnvironmentVariable('Path', 'User')
$python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
$data = Join-Path $PSScriptRoot 'data'
$statePath = Join-Path $data 'launcher-processes.json'
$projectPattern = [regex]::Escape($PSScriptRoot)
$lock = $null
$state = @{}

function Find-Service([string]$Pattern) {
    @(Get-CimInstance Win32_Process | Where-Object {
        $_.CommandLine -and $_.CommandLine -match $projectPattern -and $_.CommandLine -match $Pattern
    })
}

function Check-Port([int]$Port, [string]$Pattern) {
    $listeners = @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
    foreach ($listener in $listeners) {
        $owner = Get-CimInstance Win32_Process -Filter "ProcessId=$($listener.OwningProcess)"
        if (-not $owner -or -not $owner.CommandLine -or $owner.CommandLine -notmatch $projectPattern -or $owner.CommandLine -notmatch $Pattern) {
            throw "Port $Port is in use by PID $($listener.OwningProcess). Close that application or free the port, then run start-sumx.bat again. No process was terminated."
        }
    }
    return $listeners.Count -gt 0
}

function Start-ServiceProcess([string]$Name, [string]$File, [string[]]$Arguments, [string]$Pattern) {
    $running = @(Find-Service $Pattern)
    if ($running.Count -gt 0) {
        Write-Host "$Name is already running."
        $state[$Name] = @($running | ForEach-Object { [int]$_.ProcessId })
        return
    }
    $process = Start-Process -FilePath $File -ArgumentList $Arguments -WorkingDirectory $PSScriptRoot -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput (Join-Path $data "$Name.stdout.log") -RedirectStandardError (Join-Path $data "$Name.stderr.log")
    $state[$Name] = @([int]$process.Id)
    $state | ConvertTo-Json | Set-Content -LiteralPath $statePath -Encoding UTF8
    Write-Host "Started $Name (PID $($process.Id))."
}

function Wait-Url([string]$Url, [string]$Name) {
    $deadline = (Get-Date).AddSeconds(40)
    do {
        try {
            $response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 5
            if ($response.StatusCode -eq 200) { return }
        } catch { }
        Start-Sleep -Milliseconds 400
    } while ((Get-Date) -lt $deadline)
    throw "$Name did not become ready at $Url. Check data\$Name.stderr.log and data\$Name.stdout.log."
}

try {
    if (-not (Test-Path -LiteralPath $python)) { throw 'Missing .venv. Run the dependency setup described in SETUP_NEW_LAPTOP.md.' }
    if (-not (Test-Path -LiteralPath (Join-Path $PSScriptRoot '.env'))) { throw 'Missing root .env. See SETUP_NEW_LAPTOP.md.' }
    New-Item -ItemType Directory -Force -Path $data | Out-Null
    try { $lock = [IO.File]::Open((Join-Path $data 'launcher.lock'), 'OpenOrCreate', 'ReadWrite', 'None') }
    catch { throw 'Another SUMX launcher is starting the app. Wait for it to finish, then retry.' }

    $node = (Get-Command node.exe -ErrorAction Stop).Source
    $nodeVersion = [version]((& $node --version).Trim().TrimStart('v'))
    if ($LASTEXITCODE -ne 0 -or $nodeVersion -lt [version]'22.12.0') { throw 'Node.js 22.12 or newer is required.' }
    $vite = Join-Path $PSScriptRoot 'frontend\node_modules\vite\bin\vite.js'
    if (-not (Test-Path -LiteralPath $vite)) { throw 'Frontend dependencies are missing. Run npm ci in frontend once.' }

    & $python -m pip check > (Join-Path $data 'dependency-check.log') 2>&1
    if ($LASTEXITCODE -ne 0) { throw 'Backend dependencies are inconsistent. See data\dependency-check.log.' }
    $preflight = @'
import importlib, shutil, sys
def report_failure(exc_type, exc, traceback):
    print('Backend preflight failed:', exc_type.__name__)
    if isinstance(exc, (AssertionError, ModuleNotFoundError)):
        print(str(exc))
    elif hasattr(exc, 'errors'):
        print('Invalid setting fields/types:', [(item['loc'], item['type']) for item in exc.errors()])
sys.excepthook = report_failure
from backend.config import settings
for module in ('fastapi', 'uvicorn', 'sqlalchemy', 'alembic', 'httpx', 'pydantic_settings', 'multipart', 'argon2', 'google.auth', 'itsdangerous', 'modal', 'backend.main'):
    importlib.import_module(module)
assert len(settings.session_secret) >= 32, 'SESSION_SECRET must contain at least 32 characters'
assert settings.app_url == 'http://localhost:5173', 'This local launcher expects APP_URL=http://localhost:5173'
assert not settings.cookie_secure, 'Local HTTP requires COOKIE_SECURE=False'
assert shutil.which(settings.ffmpeg), 'FFmpeg is missing from PATH or FFMPEG'
assert shutil.which(settings.ffprobe), 'FFprobe is missing from PATH or FFPROBE'
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from backend.models import engine
with engine.connect() as connection:
    assert set(MigrationContext.configure(connection).get_current_heads()) == set(ScriptDirectory.from_config(Config('alembic.ini')).get_heads()), 'Database needs migration; see SETUP_NEW_LAPTOP.md'
print('Backend configuration, imports, media tools and database schema passed.')
'@
    $preflight | & $python - > (Join-Path $data 'preflight.log') 2>&1
    if ($LASTEXITCODE -ne 0) { throw 'Backend preflight failed. See data\preflight.log.' }

    $apiPattern = '\-m\s+uvicorn\s+backend\.main:app(\s|$)'
    $webPattern = 'vite[\\/]bin[\\/]vite\.js.*--port\s+5173(\s|$)'
    $apiListening = Check-Port 8000 $apiPattern
    $webListening = Check-Port 5173 $webPattern
    if (-not $apiListening) {
        Start-ServiceProcess 'api' $python @('-u','-m','uvicorn','backend.main:app','--host','127.0.0.1','--port','8000','--no-access-log') $apiPattern
    }
    Wait-Url 'http://127.0.0.1:8000/api/health' 'api'
    Start-ServiceProcess 'worker' $python @('-u','-m','backend.jobs') '\-m\s+backend\.jobs(\s|$)'
    Start-ServiceProcess 'tts' $python @('-u','-m','backend.tts_worker') '\-m\s+backend\.tts_worker(\s|$)'
    if (-not $webListening) {
        Start-ServiceProcess 'web' $node @(('"'+$vite+'"'),('"'+(Join-Path $PSScriptRoot 'frontend')+'"'),'--host','127.0.0.1','--port','5173','--strictPort') $webPattern
    }
    # IPv4 avoids repeated localhost IPv6 fallback delays in Windows PowerShell.
    Wait-Url 'http://127.0.0.1:5173' 'web'
    Wait-Url 'http://127.0.0.1:5173/api/health' 'api'
    Start-Sleep -Seconds 2
    foreach ($service in @(@('worker','\-m\s+backend\.jobs(\s|$)'), @('tts','\-m\s+backend\.tts_worker(\s|$)'))) {
        if (@(Find-Service $service[1]).Count -eq 0) { throw "$($service[0]) stopped. Check data\$($service[0]).stderr.log." }
    }
    foreach ($service in @(@('api',$apiPattern), @('web',$webPattern), @('worker','\-m\s+backend\.jobs(\s|$)'), @('tts','\-m\s+backend\.tts_worker(\s|$)'))) {
        $state[$service[0]] = @(Find-Service $service[1] | ForEach-Object { [int]$_.ProcessId })
    }
    $state | ConvertTo-Json | Set-Content -LiteralPath $statePath -Encoding UTF8
    Write-Host ''
    Write-Host 'SUMX:  http://localhost:5173  (also http://127.0.0.1:5173)'
    Write-Host 'Admin: http://localhost:5173/admin'
    Write-Host 'API:   http://127.0.0.1:8000/docs'
    Write-Host 'Services run as separate background processes. Logs and process IDs are in data\.'
    Write-Host 'Run stop-sumx.ps1 to stop this project. External service blockers are listed in SETUP_NEW_LAPTOP.md.'
    if (-not $NoBrowser) { Start-Process 'http://localhost:5173' }
} catch {
    Write-Host "SUMX could not start: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
} finally {
    if ($lock) { $lock.Dispose() }
}
