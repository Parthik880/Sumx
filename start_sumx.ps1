param([switch]$NoBrowser)
# Compatibility entry point for the existing README command.
& (Join-Path $PSScriptRoot 'start-sumx.ps1') -NoBrowser:$NoBrowser
