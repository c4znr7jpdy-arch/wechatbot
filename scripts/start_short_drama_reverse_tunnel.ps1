param(
    [string]$Server = "47.242.208.64",
    [string]$User = "root",
    [string]$RemoteSocket = "/run/short-drama-player.sock",
    [int]$LocalPort = 6197,
    [string]$IdentityFile = "",
    [int]$ServerAliveInterval = 30,
    [int]$ServerAliveCountMax = 3,
    [int]$RetrySeconds = 10
)

$ErrorActionPreference = "Stop"

$ssh = Get-Command ssh -ErrorAction SilentlyContinue
if (-not $ssh) {
    throw "OpenSSH client not found. Install Windows OpenSSH Client first."
}
if ($RemoteSocket -notmatch '^/run/[A-Za-z0-9._-]+\.sock$') {
    throw "RemoteSocket must be a simple .sock path directly under /run."
}

$argsList = @(
    "-T",
    "-o", "BatchMode=yes",
    "-o", "ExitOnForwardFailure=yes",
    "-o", "ServerAliveInterval=$ServerAliveInterval",
    "-o", "ServerAliveCountMax=$ServerAliveCountMax",
    "-o", "StreamLocalBindUnlink=yes",
    "-R", "$RemoteSocket`:127.0.0.1:$LocalPort"
)

if ($IdentityFile.Trim()) {
    $argsList += @("-i", $IdentityFile)
}

$argsList += "$User@$Server"
$argsList += (
    "trap 'rm -f -- $RemoteSocket' EXIT HUP INT TERM; " +
    "chmod 660 '$RemoteSocket' && chown root:www-data '$RemoteSocket' && " +
    "while :; do sleep 3600; done"
)
while ($true) {
    $cleanupArgs = @("-T", "-o", "BatchMode=yes")
    if ($IdentityFile.Trim()) {
        $cleanupArgs += @("-i", $IdentityFile)
    }
    $cleanupArgs += @("$User@$Server", "rm -f -- '$RemoteSocket'")
    & $ssh.Source @cleanupArgs
    & $ssh.Source @argsList
    Start-Sleep -Seconds ([Math]::Max(3, $RetrySeconds))
}
