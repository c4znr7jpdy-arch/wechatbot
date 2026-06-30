param(
    [Parameter(Mandatory = $true)]
    [string]$Server,

    [Parameter(Mandatory = $true)]
    [string]$User,

    [int]$RemotePort = 18789,
    [int]$LocalPort = 8789,
    [string]$IdentityFile = "",
    [int]$ServerAliveInterval = 30,
    [int]$ServerAliveCountMax = 3
)

$ErrorActionPreference = "Stop"

function Test-LocalWebhook {
    param([int]$Port)

    try {
        $resp = Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:$Port/health" -TimeoutSec 5
        if ($resp.StatusCode -ge 200 -and $resp.StatusCode -lt 500) {
            return $true
        }
    } catch {
        return $false
    }
    return $false
}

$ssh = Get-Command ssh -ErrorAction SilentlyContinue
if (-not $ssh) {
    throw "OpenSSH client not found. Install Windows OpenSSH Client first."
}

if (-not (Test-LocalWebhook -Port $LocalPort)) {
    Write-Warning "Local webhook http://127.0.0.1:$LocalPort/health is not reachable. Make sure AstrBot and jiang_12123_notify are running."
}

$argsList = @(
    "-N",
    "-T",
    "-o", "ExitOnForwardFailure=yes",
    "-o", "ServerAliveInterval=$ServerAliveInterval",
    "-o", "ServerAliveCountMax=$ServerAliveCountMax",
    "-R", "127.0.0.1:$RemotePort`:127.0.0.1:$LocalPort"
)

if ($IdentityFile.Trim()) {
    $argsList += @("-i", $IdentityFile)
}

$argsList += "$User@$Server"

Write-Host "Starting SSH reverse tunnel:"
Write-Host "  server: $User@$Server"
Write-Host "  server localhost:$RemotePort -> this PC localhost:$LocalPort"
Write-Host ""
Write-Host "Nginx on the server should proxy to: http://127.0.0.1:$RemotePort"
Write-Host "Press Ctrl+C to stop."

& $ssh.Source @argsList
