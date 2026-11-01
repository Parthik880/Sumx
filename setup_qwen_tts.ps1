param([ValidateSet('Qwen/Qwen3-TTS-12Hz-0.6B-Base')][string]$Model='Qwen/Qwen3-TTS-12Hz-0.6B-Base')
$ErrorActionPreference='Stop'
Set-Location -LiteralPath $PSScriptRoot
$qwenPython=Join-Path $PSScriptRoot '.venv-faster-qwen\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $qwenPython)) {
    & py -3.12 -m venv (Join-Path $PSScriptRoot '.venv-faster-qwen')
    if ($LASTEXITCODE -ne 0) { throw 'Install Python 3.12, then run this script again.' }
}
if (Get-Command uv -ErrorAction SilentlyContinue) {
    & uv pip install --python $qwenPython torch==2.10.0 torchaudio==2.10.0 --index-url https://download.pytorch.org/whl/cu128
    if ($LASTEXITCODE -ne 0) { throw 'CUDA PyTorch installation failed.' }
    & uv pip install --python $qwenPython -r (Join-Path $PSScriptRoot 'requirements-qwen.txt')
} else {
    & $qwenPython -m pip install torch==2.10.0 torchaudio==2.10.0 --index-url https://download.pytorch.org/whl/cu128
    if ($LASTEXITCODE -ne 0) { throw 'CUDA PyTorch installation failed.' }
    & $qwenPython -m pip install -r (Join-Path $PSScriptRoot 'requirements-qwen.txt')
}
if ($LASTEXITCODE -ne 0) { throw 'Qwen runtime installation failed.' }
$env:PYTHONIOENCODING='utf-8'
$env:HF_HUB_DISABLE_SYMLINKS_WARNING='1'
& $qwenPython -m backend.qwen_setup --model $Model
if ($LASTEXITCODE -ne 0) { throw 'Qwen setup/readiness check failed. See the diagnostic above.' }
Write-Output "Prepared $Model. Set QWEN_TTS_MODEL in .env to the same model, then run .\run_qwen_tts.ps1."
