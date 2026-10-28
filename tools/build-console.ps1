$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$compiler = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
& $compiler /nologo /target:winexe /reference:System.Windows.Forms.dll `
    "/out:$projectRoot\RTLForge.exe" "$PSScriptRoot\ConsoleLauncher.cs"
if ($LASTEXITCODE -ne 0) { throw 'Console launcher build failed.' }
Write-Host "Built $projectRoot\RTLForge.exe"
