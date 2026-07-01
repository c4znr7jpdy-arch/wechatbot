$ErrorActionPreference = "Stop"

$serviceDir = Join-Path $PSScriptRoot "..\services\netease-cloud-music-api"
Set-Location $serviceDir

$env:PORT = "3300"
npm start
