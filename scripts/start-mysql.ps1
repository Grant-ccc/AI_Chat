$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
& (Join-Path $taskRoot '.venv/Scripts/python.exe') (Join-Path $PSScriptRoot 'start_mysql.py')
exit $LASTEXITCODE
