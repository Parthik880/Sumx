$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
foreach ($tool in @('python','node','npm.cmd','ffmpeg','ffprobe')) {
    if (-not (Get-Command $tool -ErrorAction SilentlyContinue)) { throw "$tool must be installed and available on PATH." }
}
if (-not (Test-Path -LiteralPath '.venv/Scripts/python.exe')) { & python -m venv .venv; if ($LASTEXITCODE) { throw 'Virtual environment creation failed' } }
$python = Join-Path $PSScriptRoot '.venv/Scripts/python.exe'
& $python -m pip install -r backend/requirements-lock.txt
if ($LASTEXITCODE) { throw 'Backend dependency installation failed' }
& $python -c "from pathlib import Path; import secrets; p=Path('.env'); p.write_text(Path('.env.example').read_text(encoding='utf-8').replace('SESSION_SECRET=CHANGE_ME', 'SESSION_SECRET='+secrets.token_urlsafe(48), 1), encoding='utf-8') if not p.exists() else None; Path('data/media').mkdir(parents=True,exist_ok=True)"
& $python -m alembic upgrade head
if ($LASTEXITCODE) { throw 'Database migration failed' }
Push-Location -LiteralPath frontend
try { & npm.cmd ci; if ($LASTEXITCODE) { throw 'Frontend dependency installation failed' } } finally { Pop-Location }
Write-Output 'SUMX is prepared. Set OpenRouter and Google credentials in .env, create an administrator, then start the API, worker and frontend as described in README.md.'
