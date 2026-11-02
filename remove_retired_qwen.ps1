# Manual cleanup only. Removes local GPU files that SUMX no longer uses; speech runs on Modal.
$ErrorActionPreference='Stop'
$sumxRoot=(Resolve-Path -LiteralPath $PSScriptRoot).Path
$sumxTargets=@('.venv-qwen','.venv-faster-qwen','models') | ForEach-Object { Join-Path $sumxRoot $_ }
$sumxActive=Get-CimInstance Win32_Process | Where-Object { $_.Name -match '^python(w)?\.exe$' -and $_.CommandLine -match '\.venv-(faster-)?qwen' }
if ($sumxActive) {throw 'The retired environment is still in use. Stop its process before cleanup.'}
foreach ($sumxTarget in $sumxTargets) {
    $sumxAbsolute=[System.IO.Path]::GetFullPath($sumxTarget)
    if (-not $sumxAbsolute.StartsWith($sumxRoot+[System.IO.Path]::DirectorySeparatorChar,[System.StringComparison]::OrdinalIgnoreCase)) {throw 'Unsafe cleanup target outside SUMX.'}
    if (Test-Path -LiteralPath $sumxAbsolute) {
        $sumxItem=Get-Item -LiteralPath $sumxAbsolute
        if ($sumxItem.Attributes -band [System.IO.FileAttributes]::ReparsePoint) {throw 'Refusing to recursively remove a junction or symlink.'}
        Write-Output ('Removing retired runtime/model: '+$sumxAbsolute)
        Remove-Item -LiteralPath $sumxAbsolute -Recurse -Force
    }
}
Write-Output 'Local GPU environments and model weights removed. All user data is preserved.'
