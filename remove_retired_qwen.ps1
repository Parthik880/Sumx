# Manual cleanup only. Automatic execution was blocked by tool approval review.
$ErrorActionPreference='Stop'
$sumxRoot=(Resolve-Path -LiteralPath $PSScriptRoot).Path
$sumxOldEnv=Join-Path $sumxRoot '.venv-qwen'
$sumxOldModel=Join-Path $sumxRoot 'models\qwen3-tts\models--Qwen--Qwen3-TTS-12Hz-1.7B-Base'
$sumxActive=Get-CimInstance Win32_Process | Where-Object { $_.Name -match '^python(w)?\.exe$' -and $_.CommandLine -match ([regex]::Escape($sumxOldEnv)) }
if ($sumxActive) {throw 'The retired environment is still in use. Stop its process before cleanup.'}
foreach ($sumxTarget in @($sumxOldEnv,$sumxOldModel)) {
    $sumxAbsolute=[System.IO.Path]::GetFullPath($sumxTarget)
    if (-not $sumxAbsolute.StartsWith($sumxRoot+[System.IO.Path]::DirectorySeparatorChar,[System.StringComparison]::OrdinalIgnoreCase)) {throw 'Unsafe cleanup target outside SUMX.'}
    if (Test-Path -LiteralPath $sumxAbsolute) {
        $sumxItem=Get-Item -LiteralPath $sumxAbsolute
        if ($sumxItem.Attributes -band [System.IO.FileAttributes]::ReparsePoint) {throw 'Refusing to recursively remove a junction or symlink.'}
        Write-Output ('Removing retired runtime/model: '+$sumxAbsolute)
        Remove-Item -LiteralPath $sumxAbsolute -Recurse -Force
    }
}
Write-Output 'Retired standard environment and 1.7B weights removed. Faster runtime, 0.6B weights and all user data are preserved.'
