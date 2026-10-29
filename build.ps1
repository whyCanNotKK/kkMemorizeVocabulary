param(
    [switch]$OpenOutput,
    [switch]$Pause
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
Set-Location -LiteralPath $PSScriptRoot
# Import script functions in this scope before helper scripts can trigger autoload.
Import-Module Microsoft.PowerShell.Utility -Global

function Invoke-BuildCommand([string]$Executable, [string[]]$Arguments) {
    # Windows PowerShell transcripts otherwise miss native stdout/stderr.
    $savedPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        & $Executable @Arguments 2>&1 | ForEach-Object { Write-Host $_.ToString() }
        $commandExitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $savedPreference
    }
    if ($commandExitCode -ne 0) {
        throw "Command failed ($commandExitCode): $Executable $($Arguments -join ' ')"
    }
}

$buildExitCode = 1
$buildLock = $null
$transcribing = $false
$previousBuildPath = $env:PATH
try {
    New-Item -ItemType Directory -Path 'build' -Force | Out-Null
    # A second double-click must not overwrite an active build's output or log.
    $buildLock = [IO.File]::Open((Join-Path $PSScriptRoot 'build/installer.lock'),
        [IO.FileMode]::OpenOrCreate, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None)
    Start-Transcript -Path 'build/installer-build.log' -Force | Out-Null
    $transcribing = $true
    Write-Host '[1/6] Preparing Python 3.12 x64 and locked dependencies...'
    $python = Join-Path $PSScriptRoot '.venv/Scripts/python.exe'
    if (-not (Test-Path -LiteralPath $python)) {
        if (Get-Command py.exe -ErrorAction SilentlyContinue) {
            & py.exe -3.12 -m venv .venv
        } elseif (Get-Command python.exe -ErrorAction SilentlyContinue) {
            & python.exe -m venv .venv
        } else {
            throw 'Install Python 3.12 x64 from python.org, then double-click BuildInstaller.exe again.'
        }
        if ($LASTEXITCODE -ne 0) { throw 'Could not create .venv. Install Python 3.12 x64.' }
    }
    & $python -c 'import sys, struct; sys.exit(0 if sys.version_info[:2] == (3, 12) and struct.calcsize(''P'') == 8 else 1)'
    if ($LASTEXITCODE -ne 0) { throw '.venv must use Python 3.12 x64. Recreate it with that Python version.' }
    & $python scripts/check_build_dependencies.py
    if ($LASTEXITCODE -ne 0) {
        Invoke-BuildCommand $python @('-m', 'pip', 'install', '-r', 'requirements.lock.txt')
        & $python scripts/check_build_dependencies.py
        if ($LASTEXITCODE -ne 0) { throw 'Locked dependencies are still missing or mismatched.' }
    }

    Write-Host '[2/6] Preparing the local Inno Setup compiler...'
    $compiler = & "$PSScriptRoot/scripts/prepare-inno.ps1"
    Write-Host '[3/6] Running the test suite with temporary databases...'
    Invoke-BuildCommand $python @('-m', 'pytest', '-q')

    Write-Host '[4/6] Building the application...'
    try {
        # Do not collect incompatible ICU/API-set DLLs from other tool suites.
        $env:PATH = "$PSScriptRoot\.venv\Scripts;$env:SystemRoot\System32;$env:SystemRoot"
        Invoke-BuildCommand $python @('-m', 'PyInstaller', '--noconfirm', 'kkMemorizeVocabulary.spec')
    } finally {
        $env:PATH = $previousBuildPath
    }

    Write-Host '[5/6] Verifying the bundle and creating the portable ZIP...'
    Invoke-BuildCommand $python @('scripts/verify_release.py')
    Copy-Item -LiteralPath README.md -Destination dist/kkMemorizeVocabulary/README.md
    New-Item -ItemType Directory -Path dist/kkMemorizeVocabulary/docs -Force | Out-Null
    Copy-Item -Path docs/* -Destination dist/kkMemorizeVocabulary/docs -Recurse -Force
    Compress-Archive -Path dist/kkMemorizeVocabulary -DestinationPath dist/kkMemorizeVocabulary-windows-x64.zip -Force

    Write-Host '[6/6] Creating the Windows installer...'
    $appVersion = & $python -c 'import tomllib; print(tomllib.load(open(''pyproject.toml'', ''rb''))[''project''][''version''])'
    if ($LASTEXITCODE -ne 0) { throw 'Could not read the application version.' }
    Invoke-BuildCommand $compiler @("/DAppVersion=$appVersion", "$PSScriptRoot/installer/kkMemorizeVocabulary.iss")
    $installer = Join-Path $PSScriptRoot 'dist/kkMemorizeVocabulary-Setup.exe'
    if (-not (Test-Path -LiteralPath $installer)) { throw 'Installer output is missing.' }
    # Archive's Windows PowerShell module can hide Utility script functions.
    # Use .NET directly so checksum generation works after Compress-Archive.
    $hashAlgorithm = [Security.Cryptography.SHA256]::Create()
    $hashStream = [IO.File]::OpenRead($installer)
    try {
        $hash = [BitConverter]::ToString($hashAlgorithm.ComputeHash($hashStream)).Replace('-', '')
    } finally {
        $hashStream.Dispose()
        $hashAlgorithm.Dispose()
    }
    Set-Content -LiteralPath "$installer.sha256" -Value "$hash  kkMemorizeVocabulary-Setup.exe" -Encoding ASCII
    Write-Host "`nSUCCESS: $installer" -ForegroundColor Green
    Write-Host 'Copy this single Setup.exe to a Windows 10/11 x64 computer and double-click to install.'
    Write-Host 'Log: build/installer-build.log'
    $buildExitCode = 0
    if ($OpenOutput) { Start-Process explorer.exe -ArgumentList ('/select,"{0}"' -f $installer) }
} catch {
    Write-Host "`nBUILD FAILED: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host $_.ScriptStackTrace
    Write-Host 'See build/installer-build.log for details. An older installer in dist is not a new build.'
} finally {
    $env:PATH = $previousBuildPath
    if ($transcribing) { Stop-Transcript | Out-Null }
    if ($null -ne $buildLock) { $buildLock.Dispose() }
    if ($Pause) { Read-Host 'Press Enter to close this window' | Out-Null }
}
exit $buildExitCode
