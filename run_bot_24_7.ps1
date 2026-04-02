param(
    [string]$PythonPath = "python",
    [int]$RestartDelaySeconds = 5
)

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$logDir = Join-Path $projectRoot "logs"

if (-not (Test-Path $logDir)) {
    New-Item -ItemType Directory -Path $logDir | Out-Null
}

Set-Location $projectRoot

$pythonCommand = Get-Command $PythonPath -ErrorAction SilentlyContinue
if (-not $pythonCommand) {
    throw "Python executable '$PythonPath' not found in PATH."
}

$pythonExecutable = $pythonCommand.Source

while ($true) {
    $timestamp = Get-Date -Format "yyyy-MM-dd_HH-mm-ss"
    $logFile = Join-Path $logDir "bot_$timestamp.log"

    "[$(Get-Date -Format s)] Starting bot process" | Tee-Object -FilePath $logFile -Append

    $process = Start-Process -FilePath $pythonExecutable -ArgumentList "bot.py" -WorkingDirectory $projectRoot -RedirectStandardOutput $logFile -RedirectStandardError $logFile -PassThru
    $process.WaitForExit()

    "[$(Get-Date -Format s)] Bot exited with code $($process.ExitCode). Restarting in $RestartDelaySeconds seconds." | Tee-Object -FilePath $logFile -Append
    Start-Sleep -Seconds $RestartDelaySeconds
}