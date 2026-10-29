$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$csc = Join-Path $env:WINDIR 'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
if (-not (Test-Path -LiteralPath $csc)) { throw '.NET Framework 4.x C# compiler is required.' }
$icon = Join-Path $projectRoot 'assets\app.ico'
$output = Join-Path $projectRoot 'BuildInstaller.exe'
$source = Join-Path $PSScriptRoot 'BuildInstaller.cs'
& $csc /nologo /target:exe /platform:anycpu /optimize+ "/win32icon:$icon" "/out:$output" $source
if ($LASTEXITCODE -ne 0) { throw 'Could not compile BuildInstaller.exe. Close any running copy and retry.' }
Write-Host "Ready: $projectRoot/BuildInstaller.exe"
