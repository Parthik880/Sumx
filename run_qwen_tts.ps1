$ErrorActionPreference='Stop'
Set-Location -LiteralPath $PSScriptRoot
$qwenPython=Join-Path $PSScriptRoot '.venv-faster-qwen\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $qwenPython)) { throw 'Run .\setup_qwen_tts.ps1 first.' }
$env:PYTHONIOENCODING='utf-8'
$env:HF_HUB_DISABLE_SYMLINKS_WARNING='1'
& $qwenPython -m backend.qwen_worker
