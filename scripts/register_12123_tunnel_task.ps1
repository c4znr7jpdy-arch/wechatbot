param(
    [Parameter(Mandatory = $true)]
    [string]$Server,

    [Parameter(Mandatory = $true)]
    [string]$User,

    [int]$RemotePort = 18789,
    [int]$LocalPort = 8789,
    [string]$IdentityFile = "",
    [string]$TaskName = "Jiang12123ReverseTunnel"
)

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$scriptPath = Join-Path $PSScriptRoot "start_12123_reverse_tunnel.ps1"

if (-not (Test-Path -LiteralPath $scriptPath)) {
    throw "Missing script: $scriptPath"
}

$argumentParts = @(
    "-NoProfile",
    "-ExecutionPolicy", "Bypass",
    "-File", "`"$scriptPath`"",
    "-Server", "`"$Server`"",
    "-User", "`"$User`"",
    "-RemotePort", "$RemotePort",
    "-LocalPort", "$LocalPort"
)

if ($IdentityFile.Trim()) {
    $argumentParts += @("-IdentityFile", "`"$IdentityFile`"")
}

$hiddenVbs = Join-Path $PSScriptRoot "start_12123_reverse_tunnel_hidden.vbs"
if (Test-Path -LiteralPath $hiddenVbs) {
    $action = New-ScheduledTaskAction `
        -Execute "wscript.exe" `
        -Argument "`"$hiddenVbs`"" `
        -WorkingDirectory $projectRoot
} else {
    $action = New-ScheduledTaskAction `
        -Execute "powershell.exe" `
        -Argument (("-WindowStyle Hidden " + ($argumentParts -join " ")).Trim()) `
        -WorkingDirectory $projectRoot
}

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
    -Description "Keep SSH reverse tunnel for 12123 MacroDroid webhook online." `
    -Force | Out-Null

Write-Host "Scheduled task registered: $TaskName"
Write-Host "Start it now with:"
Write-Host "  Start-ScheduledTask -TaskName `"$TaskName`""
Write-Host "Check state with:"
Write-Host "  Get-ScheduledTask -TaskName `"$TaskName`" | Select-Object TaskName,State"
