param([ValidateSet('mock','live')][string]$Mode='mock', [int]$Port=8790)
$ErrorActionPreference='Stop'
$runtime=Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $runtime)) { throw 'Please run Setup.ps1 in this folder first.' }
& $runtime -B (Join-Path $PSScriptRoot 'run.py') --mode $Mode --port $Port
exit $LASTEXITCODE
