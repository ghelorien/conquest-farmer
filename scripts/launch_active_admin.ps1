param(
    [int]$ClientPid = 0,
    [long]$ClientStarted = 0,
    [long]$ClientHwnd = 0
)

$ErrorActionPreference = 'Stop'

$repository = 'C:\Users\Floor\Documents\ChatGPT\Conquest-farmer'
$python = 'C:\Users\Floor\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
$releaseScript = Join-Path $repository 'scripts\release.py'
$dataRoot = 'C:\Users\Floor\AppData\Local\Conquest'
$errorLog = Join-Path $dataRoot 'launcher-error.log'

try {
    $selected = @($ClientPid, $ClientStarted, $ClientHwnd)
    if (($selected | Where-Object { $_ -gt 0 }).Count -notin @(0, 3)) {
        throw 'Client attachment requires PID, creation time, and window handle together'
    }
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        $arguments = '-NoProfile -ExecutionPolicy Bypass -File "' + $PSCommandPath + '"'
        if ($ClientPid -gt 0) {
            $arguments += " -ClientPid $ClientPid -ClientStarted $ClientStarted -ClientHwnd $ClientHwnd"
        }
        Start-Process -FilePath 'powershell.exe' -ArgumentList $arguments -Verb RunAs -WindowStyle Hidden
        exit 0
    }
    Set-Location -LiteralPath $repository
    $releaseArgs = @('launch', '--data-root', $dataRoot)
    if ($ClientPid -gt 0) {
        $releaseArgs += @('--', '--embed-client', '--client-pid', "$ClientPid",
                          '--client-started', "$ClientStarted", '--client-hwnd', "$ClientHwnd")
    }
    & $python $releaseScript @releaseArgs 2>> $errorLog
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
