param(
    [string]$TaskName = "Jiang12123ReverseTunnel",
    [string]$HealthUrl = "https://xiuxianjyj.xin/health"
)

$ErrorActionPreference = "Stop"

try {
    $resp = Invoke-WebRequest -UseBasicParsing -Uri $HealthUrl -TimeoutSec 8
    if ($resp.StatusCode -eq 200 -and $resp.Content -like "*jiang_12123_notify*") {
        return
    }
} catch {
    # Health check failed; fall through and ask Task Scheduler to bring the tunnel up.
}

$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction Stop
if ($task.State -ne "Running") {
    Start-ScheduledTask -TaskName $TaskName
}
