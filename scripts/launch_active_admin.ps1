param(
    [int]$ClientPid = 0,
    [long]$ClientStarted = 0,
    [long]$ClientHwnd = 0,
    [int]$CloseControllerPid = 0,
    [long]$CloseControllerStarted = 0
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
    if (($CloseControllerPid -gt 0) -ne ($CloseControllerStarted -gt 0) -or
            ($CloseControllerPid -gt 0 -and $ClientPid -gt 0)) {
        throw 'Controller close requires its PID and creation time, without a client attachment'
    }
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        $arguments = '-NoProfile -ExecutionPolicy Bypass -File "' + $PSCommandPath + '"'
        if ($ClientPid -gt 0) {
            $arguments += " -ClientPid $ClientPid -ClientStarted $ClientStarted -ClientHwnd $ClientHwnd"
        }
        if ($CloseControllerPid -gt 0) {
            $arguments += " -CloseControllerPid $CloseControllerPid -CloseControllerStarted $CloseControllerStarted"
        }
        Start-Process -FilePath 'powershell.exe' -ArgumentList $arguments -Verb RunAs -WindowStyle Hidden
        exit 0
    }
    if ($CloseControllerPid -gt 0) {
        $controller = Get-Process -Id $CloseControllerPid -ErrorAction Stop
        $created = $controller.StartTime.ToUniversalTime().Ticks - 504911232000000000
        if ($created -ne $CloseControllerStarted -or $controller.MainWindowHandle -eq 0) {
            throw 'Controller process or window identity changed; close not sent'
        }
        $helper = Join-Path $repository 'scripts\close_controller.py'
        & $python -B $helper --pid $CloseControllerPid --created $CloseControllerStarted --hwnd $controller.MainWindowHandle.ToInt64()
        if ($LASTEXITCODE -ne 0) {
            throw "Controller normal close helper exited with code $LASTEXITCODE"
        }
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
