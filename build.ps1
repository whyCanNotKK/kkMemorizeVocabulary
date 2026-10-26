$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$previousBuildPath = $env:PATH
try {
    # Tool suites such as Poppler may place incompatible ICU/API-set DLLs on PATH.
    # Dependency discovery must use the project's Qt and Windows, not those suites.
    $env:PATH = "$PSScriptRoot\.venv\Scripts;$env:SystemRoot\System32;$env:SystemRoot"
    & .\.venv\Scripts\python.exe -m PyInstaller --noconfirm kkMemorizeVocabulary.spec
    if ($LASTEXITCODE -ne 0) { throw 'PyInstaller build failed' }
} finally {
    $env:PATH = $previousBuildPath
}
& .\.venv\Scripts\python.exe scripts\verify_release.py
if ($LASTEXITCODE -ne 0) { throw 'Release verification failed' }
Copy-Item -LiteralPath README.md -Destination dist\kkMemorizeVocabulary\README.md
Copy-Item -LiteralPath docs -Destination dist\kkMemorizeVocabulary\docs -Recurse -Force
Compress-Archive -Path dist\kkMemorizeVocabulary -DestinationPath dist\kkMemorizeVocabulary-windows-x64.zip -Force
Write-Output 'Ready: dist\kkMemorizeVocabulary\kkMemorizeVocabulary.exe'
