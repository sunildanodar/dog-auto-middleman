param(
    [string]$TaskName = "DogAutoMiddlemanBot",
    [string]$PythonPath = "python"
)

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$launcher = Join-Path $projectRoot "run_bot_24_7.ps1"

if (-not (Test-Path $launcher)) {
    throw "Launcher script not found: $launcher"
}

$escapedLauncher = '"' + $launcher + '"'
$arguments = "-NoProfile -ExecutionPolicy Bypass -File $escapedLauncher -PythonPath $PythonPath"

$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $arguments
$startupTrigger = New-ScheduledTaskTrigger -AtStartup
$logonTrigger = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1)

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger @($startupTrigger, $logonTrigger) -Principal $principal -Settings $settings -Force | Out-Null

Write-Host "Scheduled task '$TaskName' installed."
Write-Host "It will start the bot at Windows startup and restart if it exits."