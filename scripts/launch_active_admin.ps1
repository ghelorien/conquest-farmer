$ErrorActionPreference = 'Stop'

$repository = 'C:\Users\Floor\Documents\ChatGPT\Conquest-farmer'
$python = 'C:\Users\Floor\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
$releaseScript = Join-Path $repository 'scripts\release.py'
$dataRoot = 'C:\Users\Floor\AppData\Local\Conquest'
$errorLog = Join-Path $dataRoot 'launcher-error.log'

try {
    Set-Location -LiteralPath $repository
    & $python $releaseScript launch --data-root $dataRoot 2>> $errorLog
    if ($LASTEXITCODE -ne 0) {
        throw "Release launcher exited with code $LASTEXITCODE"
    }
}
catch {
    Add-Content -LiteralPath $errorLog -Value ((Get-Date -Format o) + ' ' + $_.Exception.Message)
    Add-Type -AssemblyName PresentationFramework
    [System.Windows.MessageBox]::Show(
        "Conquest could not start. Details were written to $errorLog",
        'Conquest launch failed',
        'OK',
        'Error'
    ) | Out-Null
    exit 1
}
