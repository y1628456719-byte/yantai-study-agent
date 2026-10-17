$ErrorActionPreference='Stop'
$runtime=Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $runtime)) {
    $python=Get-Command python -CommandType Application -ErrorAction Stop | Select-Object -First 1
    & $python.Source -m venv (Join-Path $PSScriptRoot '.venv')
    if ($LASTEXITCODE -ne 0) { throw 'Python 3.11+ is required.' }
}
& $runtime -m pip install -i https://pypi.tuna.tsinghua.edu.cn/simple -r (Join-Path $PSScriptRoot 'requirements-lock.txt')
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
