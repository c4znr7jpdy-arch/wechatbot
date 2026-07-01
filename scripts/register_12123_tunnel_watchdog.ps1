param(
    [string]$TunnelTaskName = "Jiang12123ReverseTunnel",
    [string]$WatchdogTaskName = "Jiang12123ReverseTunnelWatchdog",
    [int]$IntervalMinutes = 5
)

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$scriptPath = Join-Path $PSScriptRoot "ensure_12123_tunnel_task.ps1"

if (-not (Test-Path -LiteralPath $scriptPath)) {
    throw "Missing script: $scriptPath"
}

$hiddenVbs = Join-Path $PSScriptRoot "ensure_12123_tunnel_task_hidden.vbs"
if (Test-Path -LiteralPath $hiddenVbs) {
    $action = New-ScheduledTaskAction `
        -Execute "wscript.exe" `
        -Argument "`"$hiddenVbs`"" `
        -WorkingDirectory $projectRoot
} else {
    $action = New-ScheduledTaskAction `
        -Execute "powershell.exe" `
        -Argument "-WindowStyle Hidden -NoProfile -ExecutionPolicy Bypass -File `"$scriptPath`" -TaskName `"$TunnelTaskName`"" `
        -WorkingDirectory $projectRoot
}

$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
    -RepetitionInterval (New-TimeSpan -Minutes $IntervalMinutes)

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -Hidden `
    -MultipleInstances IgnoreNew

Register-ScheduledTask `
    -TaskName $WatchdogTaskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Description "Restart 12123 SSH reverse tunnel scheduled task when it is not running." `
    -Force | Out-Null

Write-Host "Scheduled watchdog registered: $WatchdogTaskName"
