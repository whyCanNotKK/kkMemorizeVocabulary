# This script emits only the compiler path on the success output stream.
$ErrorActionPreference = 'Stop'
Import-Module Microsoft.PowerShell.Utility -Global
$toolRoot = Join-Path (Split-Path -Parent $PSScriptRoot) 'build/tools'
$toolDir = Join-Path $toolRoot 'inno-6.7.3'
$compiler = Join-Path $toolDir 'ISCC.exe'
if (-not (Test-Path -LiteralPath $compiler)) {
    New-Item -ItemType Directory -Path $toolRoot -Force | Out-Null
    $download = Join-Path $toolRoot 'innosetup-6.7.3.exe'
    $expectedHash = '9c73c3bae7ed48d44112a0f48e66742c00090bdb5bef71d9d3c056c66e97b732'
    if ((-not (Test-Path -LiteralPath $download)) -or
        (Get-FileHash -LiteralPath $download -Algorithm SHA256).Hash -ne $expectedHash) {
        Write-Host 'Downloading Inno Setup 6.7.3 from the official release...'
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        Invoke-WebRequest -UseBasicParsing -Uri 'https://github.com/jrsoftware/issrc/releases/download/is-6_7_3/innosetup-6.7.3.exe' -OutFile $download
    }
    if ((Get-FileHash -LiteralPath $download -Algorithm SHA256).Hash -ne $expectedHash) {
        throw 'Inno Setup SHA256 verification failed. Download was not executed.'
    }
    Write-Host 'Extracting the compiler in portable mode (no system installation)...'
    $toolSetup = Start-Process -FilePath $download -ArgumentList @(
        '/PORTABLE=1', '/CURRENTUSER', '/VERYSILENT', '/SUPPRESSMSGBOXES',
        '/NORESTART', '/SP-', '/NOICONS', ('/DIR="{0}"' -f $toolDir)
    ) -WindowStyle Hidden -Wait -PassThru
    if ($toolSetup.ExitCode -ne 0) { throw "Inno Setup preparation failed: $($toolSetup.ExitCode)" }
}
if (-not (Test-Path -LiteralPath $compiler)) { throw 'The Inno Setup compiler is missing.' }
return $compiler
