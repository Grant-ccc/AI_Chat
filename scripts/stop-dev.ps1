param([switch]$TestDb)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$pidName = if ($TestDb) { '.local/test-dev-processes.json' } else { '.local/dev-processes.json' }
$pidFile = Join-Path $projectRoot $pidName
if (-not (Test-Path -LiteralPath $pidFile)) { Write-Host 'No saved dev processes.'; exit }
$devProcesses = Get-Content -LiteralPath $pidFile -Raw | ConvertFrom-Json
foreach ($item in $devProcesses.PSObject.Properties) {
    $devProcess = Get-CimInstance Win32_Process -Filter "ProcessId = $($item.Value)"
    if ($null -eq $devProcess) { continue }
    if (-not $devProcess.CommandLine.Contains($projectRoot)) {
        throw "PID $($item.Value) does not belong to this project; refusing to stop."
    }
    Stop-Process -Id $item.Value
    Write-Host "Stopped $($item.Name)."
}
Remove-Item -LiteralPath $pidFile
