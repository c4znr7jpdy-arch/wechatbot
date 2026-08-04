param(
    [string]$TaskName = "JiangShortDramaReverseTunnel"
)

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$hiddenVbs = Join-Path $PSScriptRoot "start_short_drama_reverse_tunnel_hidden.vbs"
if (-not (Test-Path -LiteralPath $hiddenVbs)) {
    throw "Missing tunnel launcher: $hiddenVbs"
}

$action = New-ScheduledTaskAction `
    -Execute "wscript.exe" `
    -Argument "`"$hiddenVbs`"" `
    -WorkingDirectory $projectRoot
$trigger = New-ScheduledTaskTrigger -AtLogOn
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -Hidden `
    -RestartCount 999 `
    -RestartInterval (New-TimeSpan -Minutes 1)

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Description "Keep the short-drama player reverse tunnel online." `
    -Force | Out-Null

Write-Output "Scheduled task registered: $TaskName"
