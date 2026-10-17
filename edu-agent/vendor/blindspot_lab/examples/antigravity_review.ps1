[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$PromptFile,
    [ValidateSet('gemini-4-argon-high', 'gemini-3.8-flash-high')]
    [string]$Model = 'gemini-4-argon-high',
    [ValidateSet('high')][string]$Effort = 'high',
    [ValidateRange(30, 900)][int]$TimeoutSeconds = 300
)

# Task-local adaptation of gemini-worker's safe wrapper, using IDs observed in
# this installed CLI's models list. It does not change the shared skill/defaults.
$ErrorActionPreference = 'Stop'
$cli = 'D:\Apps\Antigravity CLI\agy.exe'
$moduleRoot = Split-Path -Parent $PSScriptRoot
$promptText = Get-Content -LiteralPath $PromptFile -Raw -Encoding UTF8
$startInfo = [System.Diagnostics.ProcessStartInfo]::new()
$startInfo.FileName = $cli
$startInfo.WorkingDirectory = $moduleRoot
$startInfo.UseShellExecute = $false
$startInfo.RedirectStandardOutput = $true
$startInfo.RedirectStandardError = $true
$startInfo.CreateNoWindow = $true
$schema = '{"type":"object","required":["verdict","findings"],"properties":{"verdict":{"type":"string"},"findings":{"type":"array","items":{"type":"string"}}}}'
foreach ($argument in @('--print', $promptText, '--output-format', 'json', '--json-schema', $schema, '--model', $Model,
    '--effort', $Effort, '--mode', 'plan', '--sandbox', '--print-timeout', "${TimeoutSeconds}s",
    '--disable-slash-commands')) {
    [void]$startInfo.ArgumentList.Add($argument)
}
$process = [System.Diagnostics.Process]::new()
$process.StartInfo = $startInfo
[void]$process.Start()
$stdoutTask = $process.StandardOutput.ReadToEndAsync()
$stderrTask = $process.StandardError.ReadToEndAsync()
if (-not $process.WaitForExit($TimeoutSeconds * 1000 + 10000)) {
    $process.Kill($true)
    throw 'Antigravity review timed out; process tree stopped.'
}
$stdout = $stdoutTask.GetAwaiter().GetResult()
$stderr = $stderrTask.GetAwaiter().GetResult()
$outputPath = Join-Path $moduleRoot 'verification\antigravity-review.json'
if ($process.ExitCode -ne 0) {
    @{ status = 'failed'; model = $Model; effort = $Effort; exit_code = $process.ExitCode;
       stderr = $stderr; output = $stdout } | ConvertTo-Json -Depth 20 |
       Set-Content -LiteralPath $outputPath -Encoding UTF8
    throw 'Antigravity CLI returned an error; see the task-local review record.'
}
$parsed = $stdout | ConvertFrom-Json -Depth 100
$resultStatus = if ([string]::IsNullOrWhiteSpace($parsed.response)) { 'empty_response' } else { 'completed' }
@{ status = $resultStatus; requested_model = $Model; requested_effort = $Effort;
   requested_mode = 'plan'; requested_sandbox = $true; result = $parsed } |
   ConvertTo-Json -Depth 100 | Set-Content -LiteralPath $outputPath -Encoding UTF8
Write-Output 'Antigravity CLI review completed; result saved without printing its full context.'
